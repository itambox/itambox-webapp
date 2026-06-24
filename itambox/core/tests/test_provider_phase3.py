"""Phase 3 tests: Provider grants wired into permission resolution.

Covers the MSP access model end-to-end:
  - tenant_scope = explicit / tenant_group / all
  - provider grants additive with TenantMembership grants
  - has_provider_capability / is_provider_staff / can_manage_user_groups
  - accessible_tenant_ids includes provider-scoped tenants
  - inactive membership / missing template grant nothing
  - backward compat: no Provider behaves as before
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase

from core.auth.provider import has_provider_capability, is_provider_staff, can_manage_user_groups
from core.managers import set_current_tenant, set_current_membership
from organization.access import accessible_tenant_ids
from organization.models import (
    Tenant, TenantGroup, TenantRole, TenantMembership, Provider, ProviderRole, ProviderRoleTemplate,
)
from users.models import ProviderMembership

User = get_user_model()


def _flush(user):
    for attr in list(user.__dict__):
        if (attr.startswith('_effective_perms_') or attr.startswith('_tenant_membership_')
                or attr in ('_is_provider_staff_cache', '_provider_layer_active_cache')):
            delattr(user, attr)


class _Base(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.provider = Provider.objects.create(name="Acme MSP")
        self.other_provider = Provider.objects.create(name="Other MSP")
        self.group = TenantGroup.objects.create(name="EMEA", slug="emea")
        self.child_group = TenantGroup.objects.create(name="EMEA-North", slug="emea-n", parent=self.group)
        # Tenants under our provider
        self.t1 = Tenant.objects.create(name="Cust1", slug="c1", provider=self.provider)
        self.t2 = Tenant.objects.create(name="Cust2", slug="c2", provider=self.provider, group=self.group)
        self.t3 = Tenant.objects.create(name="Cust3", slug="c3", provider=self.provider, group=self.child_group)
        # A tenant under a different provider, and a non-provider tenant
        self.other_t = Tenant.objects.create(name="OtherCust", slug="oc", provider=self.other_provider)
        self.plain_t = Tenant.objects.create(name="Plain", slug="plain")

        self.template = ProviderRoleTemplate.objects.create(
            provider=self.provider, name="Technician",
            permissions=["assets.view_asset", "assets.change_asset"],
        )
        self.role = ProviderRole.objects.create(
            provider=self.provider, name="Senior", tenant_role_template=self.template,
            can_manage_groups=True, can_manage_provider_users=True,
        )
        self.staff = User.objects.create_user(username="tech", email="t@e.com", password="pw")

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _membership(self, scope, assigned=None, scope_group=None, active=True, role=None):
        pm = ProviderMembership.objects.create(
            user=self.staff, provider=self.provider,
            provider_role=role if role is not None else self.role,
            tenant_scope=scope, scope_group=scope_group, is_active=active,
        )
        if assigned:
            pm.assigned_tenants.set(assigned)
        _flush(self.staff)
        return pm


class TenantScopeTests(_Base):
    def test_explicit_scope_only_assigned(self):
        self._membership(ProviderMembership.SCOPE_EXPLICIT, assigned=[self.t1])
        self.assertTrue(self.staff.has_perm('assets.view_asset', obj=self.t1))
        self.assertTrue(self.staff.has_perm('assets.change_asset', obj=self.t1))
        self.assertFalse(self.staff.has_perm('assets.view_asset', obj=self.t2))
        # A permission not in the template is never granted, even in-scope.
        self.assertFalse(self.staff.has_perm('assets.delete_asset', obj=self.t1))

    def test_tenant_group_scope_includes_descendants(self):
        self._membership(ProviderMembership.SCOPE_TENANT_GROUP, scope_group=self.group)
        self.assertTrue(self.staff.has_perm('assets.view_asset', obj=self.t2))   # in group
        self.assertTrue(self.staff.has_perm('assets.view_asset', obj=self.t3))   # descendant group
        self.assertFalse(self.staff.has_perm('assets.view_asset', obj=self.t1))  # no group

    def test_all_scope(self):
        self._membership(ProviderMembership.SCOPE_ALL)
        for t in (self.t1, self.t2, self.t3):
            self.assertTrue(self.staff.has_perm('assets.view_asset', obj=t))
        # Never leaks to another provider's tenant.
        self.assertFalse(self.staff.has_perm('assets.view_asset', obj=self.other_t))

    def test_out_of_scope_denied(self):
        self._membership(ProviderMembership.SCOPE_EXPLICIT, assigned=[self.t1])
        self.assertFalse(self.staff.has_perm('assets.view_asset', obj=self.t3))

    def test_inactive_membership_grants_nothing(self):
        self._membership(ProviderMembership.SCOPE_ALL, active=False)
        self.assertFalse(self.staff.has_perm('assets.view_asset', obj=self.t1))

    def test_role_without_template_grants_nothing(self):
        roleless = ProviderRole.objects.create(provider=self.provider, name="NoTmpl")
        self._membership(ProviderMembership.SCOPE_ALL, role=roleless)
        self.assertFalse(self.staff.has_perm('assets.view_asset', obj=self.t1))


class AdditiveGrantsTests(_Base):
    def test_provider_grant_additive_with_membership(self):
        # Direct membership in t1 grants delete; provider grant adds view/change.
        m = TenantMembership.objects.create(user=self.staff, tenant=self.t1, is_active=True)
        del_role = TenantRole.objects.create(tenant=self.t1, name="Deleter", permissions=["assets.delete_asset"])
        m.roles.set([del_role])
        self._membership(ProviderMembership.SCOPE_EXPLICIT, assigned=[self.t1])
        self.assertTrue(self.staff.has_perm('assets.delete_asset', obj=self.t1))  # membership
        self.assertTrue(self.staff.has_perm('assets.view_asset', obj=self.t1))    # provider
        self.assertTrue(self.staff.has_perm('assets.change_asset', obj=self.t1))  # provider


class AccessibleTenantIdsTests(_Base):
    def test_explicit(self):
        self._membership(ProviderMembership.SCOPE_EXPLICIT, assigned=[self.t1, self.t2])
        self.assertEqual(accessible_tenant_ids(self.staff), {self.t1.pk, self.t2.pk})

    def test_tenant_group(self):
        self._membership(ProviderMembership.SCOPE_TENANT_GROUP, scope_group=self.group)
        self.assertEqual(accessible_tenant_ids(self.staff), {self.t2.pk, self.t3.pk})

    def test_all(self):
        self._membership(ProviderMembership.SCOPE_ALL)
        self.assertEqual(accessible_tenant_ids(self.staff), {self.t1.pk, self.t2.pk, self.t3.pk})

    def test_union_with_membership(self):
        TenantMembership.objects.create(user=self.staff, tenant=self.plain_t, is_active=True)
        self._membership(ProviderMembership.SCOPE_EXPLICIT, assigned=[self.t1])
        self.assertEqual(accessible_tenant_ids(self.staff), {self.plain_t.pk, self.t1.pk})


class CapabilityTests(_Base):
    def test_is_provider_staff(self):
        other = User.objects.create_user(username="nostaff", email="n@e.com", password="pw")
        self.assertFalse(is_provider_staff(other))
        self._membership(ProviderMembership.SCOPE_EXPLICIT, assigned=[self.t1])
        self.assertTrue(is_provider_staff(self.staff))

    def test_has_provider_capability(self):
        self._membership(ProviderMembership.SCOPE_ALL)
        self.assertTrue(has_provider_capability(self.staff, 'manage_groups'))
        self.assertTrue(has_provider_capability(self.staff, 'manage_provider_users'))
        self.assertFalse(has_provider_capability(self.staff, 'manage_tenants'))

    def test_has_provider_capability_superuser(self):
        su = User.objects.create_superuser(username="su", email="su@e.com", password="pw")
        self.assertTrue(has_provider_capability(su, 'manage_tenants'))

    def test_can_manage_user_groups_paths(self):
        su = User.objects.create_superuser(username="su2", email="su2@e.com", password="pw")
        self.assertTrue(can_manage_user_groups(su))  # superuser
        plain = User.objects.create_user(username="p", email="p@e.com", password="pw")
        self.assertFalse(can_manage_user_groups(plain))
        # legacy direct grant (single-company backward compat)
        plain.user_permissions.add(
            Permission.objects.get(content_type__app_label='users', codename='manage_usergroups')
        )
        plain = User.objects.get(pk=plain.pk)
        self.assertTrue(can_manage_user_groups(plain))
        # provider capability path
        self._membership(ProviderMembership.SCOPE_ALL)
        self.assertTrue(can_manage_user_groups(self.staff))


class BackwardCompatTests(_Base):
    def test_no_provider_membership_no_access(self):
        # Staff with no ProviderMembership and no TenantMembership gets nothing.
        self.assertFalse(self.staff.has_perm('assets.view_asset', obj=self.t1))
        self.assertEqual(accessible_tenant_ids(self.staff), set())

    def test_plain_tenant_membership_unaffected(self):
        m = TenantMembership.objects.create(user=self.staff, tenant=self.plain_t, is_active=True)
        role = TenantRole.objects.create(tenant=self.plain_t, name="Viewer", permissions=["assets.view_asset"])
        m.roles.set([role])
        _flush(self.staff)
        self.assertTrue(self.staff.has_perm('assets.view_asset', obj=self.plain_t))
        self.assertEqual(accessible_tenant_ids(self.staff), {self.plain_t.pk})
