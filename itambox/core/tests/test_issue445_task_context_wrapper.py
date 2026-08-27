"""TaskContext behavior and issue #445 provider-boundary characterization."""

import ast
import inspect
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from core.context import (
    _current_user,
    _issued_system_authorizations,
    _request_id,
    _system_authorization_scope,
    get_current_all_accessible,
    get_current_membership,
    get_current_tenant,
    get_current_tenant_group,
    set_current_all_accessible,
    set_current_membership,
    set_current_tenant,
    set_current_tenant_group,
)
from core.tasks.context import TaskContext
from core.tests.mixins import grant
from organization.models import Role, RoleGrantScope, Tenant, TenantGroup

User = get_user_model()


class Issue445TaskContextWrapperTests(TestCase):
    """PASS for scope semantics; RED for removal of organization.access imports."""

    def setUp(self):
        self.group = TenantGroup.objects.create(name="Issue445 group", slug="issue445-context-group")
        self.provider = Tenant.objects.create(
            name="Issue445 provider", slug="issue445-context-provider", is_provider=True
        )
        self.direct = Tenant.objects.create(name="Issue445 direct", slug="issue445-context-direct")
        self.group_target = Tenant.objects.create(
            name="Issue445 group target",
            slug="issue445-context-group-target",
            group=self.group,
            managed_by=self.provider,
        )
        self.user = User.objects.create_user(username="issue445-context-user", password="pw")
        self.direct_role = Role.objects.create(tenant=self.direct, name="Direct", permissions=[])
        self.direct_grant = grant(self.user, self.direct, self.direct_role)
        provider_role = Role.objects.create(tenant=self.provider, name="Group reach", permissions=[])
        self.group_grant = grant(
            self.user,
            self.provider,
            provider_role,
            reach="managed",
            managed_scope=RoleGrantScope.SCOPE_TENANT_GROUP,
            scope_group=self.group,
        )

    def _ambient_snapshot(self):
        return (
            _request_id.get(),
            _current_user.get(),
            get_current_tenant(),
            get_current_membership(),
            get_current_tenant_group(),
            get_current_all_accessible(),
            _system_authorization_scope.get(),
            _issued_system_authorizations.get(),
        )

    def _install_ambient(self):
        request_id = uuid.uuid4()
        scope = object()
        authorization = object()
        _request_id.set(request_id)
        _current_user.set(self.user)
        set_current_tenant(self.direct)
        set_current_membership(self.direct_grant.membership)
        set_current_tenant_group(self.group)
        set_current_all_accessible(True)
        _system_authorization_scope.set(scope)
        _issued_system_authorizations.set((authorization,))
        return self._ambient_snapshot()

    def test_direct_and_group_derived_access_are_allowed(self):
        for tenant in (self.direct, self.group_target):
            with self.subTest(tenant=tenant.slug), TaskContext(tenant.pk, self.user.pk) as context:
                self.assertEqual(context.tenant.pk, tenant.pk)
                self.assertEqual(context.user.pk, self.user.pk)

    def test_revoked_deleted_and_inactive_access_are_denied(self):
        cases = []
        membership = self.direct_grant.membership
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        cases.append(("inactive membership", self.direct.pk, self.user.pk))

        revoked_tenant = Tenant.objects.create(name="Revoked tenant", slug="issue445-revoked-grant")
        revoked_user = User.objects.create_user(username="issue445-revoked-user", password="pw")
        revoked_role = Role.objects.create(tenant=revoked_tenant, name="Revoked", permissions=[])
        revoked_grant = grant(revoked_user, revoked_tenant, revoked_role)
        revoked_grant.delete()
        cases.append(("revoked grant", revoked_tenant.pk, revoked_user.pk))

        deleted_tenant = Tenant.objects.create(name="Deleted role tenant", slug="issue445-deleted-role")
        deleted_user = User.objects.create_user(username="issue445-deleted-role-user", password="pw")
        deleted_role = Role.objects.create(tenant=deleted_tenant, name="Deleted", permissions=[])
        grant(deleted_user, deleted_tenant, deleted_role)
        deleted_role.delete()
        cases.append(("deleted role", deleted_tenant.pk, deleted_user.pk))

        inactive_tenant = Tenant.objects.create(name="Inactive actor tenant", slug="issue445-inactive-actor")
        inactive_user = User.objects.create_user(username="issue445-inactive-user", password="pw", is_active=False)
        inactive_role = Role.objects.create(tenant=inactive_tenant, name="Inactive", permissions=[])
        grant(inactive_user, inactive_tenant, inactive_role)
        cases.append(("inactive actor", inactive_tenant.pk, inactive_user.pk))

        for label, tenant_id, user_id in cases:
            with self.subTest(case=label), self.assertRaises(PermissionDenied):
                with TaskContext(tenant_id, user_id):
                    self.fail("revoked/deleted/inactive task access must fail closed")

    def test_superuser_and_actorless_system_paths_remain_explicit(self):
        superuser = User.objects.create_superuser(username="issue445-context-root", password="pw")
        with TaskContext(self.direct.pk, superuser.pk) as context:
            self.assertEqual(context.tenant.pk, self.direct.pk)
            self.assertEqual(context.user.pk, superuser.pk)
        with TaskContext(self.direct.pk, None) as context:
            self.assertEqual(context.tenant.pk, self.direct.pk)
            self.assertIsNone(context.user)

    def test_enter_clears_every_ambient_scope_and_exit_restores_exact_values(self):
        before = self._install_ambient()
        with TaskContext(None, None):
            self.assertIsNone(get_current_tenant())
            self.assertIsNone(get_current_tenant_group())
            self.assertIsNone(get_current_membership())
            self.assertFalse(get_current_all_accessible())
            self.assertIsNone(_current_user.get())
            self.assertEqual(_issued_system_authorizations.get(), ())
            self.assertIsNot(_system_authorization_scope.get(), before[6])
            self.assertNotEqual(_request_id.get(), before[0])
        after = self._ambient_snapshot()
        self.assertEqual(after, before)
        self.assertTrue(all(a is b for a, b in zip(after[1:5], before[1:5], strict=True)))

    def test_setup_failure_restores_exact_ambient_values(self):
        before = self._install_ambient()
        with mock.patch.object(TaskContext, "_resolve_principal_and_tenant", side_effect=RuntimeError("canary")):
            with self.assertRaises(RuntimeError):
                with TaskContext(self.direct.pk, self.user.pk):
                    pass
        self.assertEqual(self._ambient_snapshot(), before)

    def test_nested_context_restores_worker_scope_exactly(self):
        before = self._install_ambient()
        with TaskContext(self.direct.pk, self.user.pk):
            outer = self._ambient_snapshot()
            with TaskContext(None, None):
                self.assertIsNone(get_current_tenant())
            self.assertEqual(self._ambient_snapshot(), outer)
        self.assertEqual(self._ambient_snapshot(), before)

    def test_organization_access_monkeypatch_is_observed_by_task_provider_path(self):
        with mock.patch("organization.access.accessible_tenant_ids", return_value=set()) as resolver:
            with self.assertRaises(PermissionDenied):
                with TaskContext(self.direct.pk, self.user.pk):
                    pass
        resolver.assert_called_once()

    def test_task_context_source_is_domain_blind(self):
        import core.tasks.context as context_module

        source = inspect.getsource(context_module)
        tree = ast.parse(source)
        forbidden = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("organization"):
                forbidden.append(node.module)
            elif isinstance(node, ast.Import):
                forbidden.extend(alias.name for alias in node.names if alias.name.startswith("organization"))
        resolver_source = inspect.getsource(TaskContext._resolve_principal_and_tenant)
        self.assertFalse(
            forbidden or "organization.access" in resolver_source,
            "missing issue445 TaskContext provider contract: core.tasks.context still references organization.access",
        )
