from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.managers import (
    set_current_all_accessible,
    set_current_membership,
    set_current_tenant,
    set_current_tenant_group,
)
from core.tests.mixins import grant
from itambox.middleware import CurrentUserMiddleware, TenantMiddleware
from organization.models import Role, RoleGrant, RoleGrantScope, Tenant, TenantGroup
from users.forms import UserPreferencesForm
from users.models import UserPreference
from users.services import parse_workspace_key, resolve_workspace_selection

User = get_user_model()


class DefaultWorkspaceTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        set_current_all_accessible(False)

        self.factory = RequestFactory()
        self.group = TenantGroup.objects.create(name="Customer Group", slug="default-ws-group")
        self.home = Tenant.objects.create(name="Home Tenant", slug="default-ws-home")
        self.customer = Tenant.objects.create(
            name="Customer Tenant",
            slug="default-ws-customer",
            group=self.group,
        )
        self.foreign = Tenant.objects.create(name="Foreign Tenant", slug="default-ws-foreign")
        self.home_role = Role.objects.create(tenant=self.home, name="Home role", permissions=[])
        self.customer_role = Role.objects.create(tenant=self.customer, name="Customer role", permissions=[])
        self.user = User.objects.create_user(username="default-ws-user", password="pw")
        grant(self.user, self.home, self.home_role)
        grant(self.user, self.customer, self.customer_role)

    def tearDown(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        set_current_all_accessible(False)

    def _request(self, session=None, user=None):
        store = import_module(settings.SESSION_ENGINE).SessionStore
        request = self.factory.get("/")
        request.user = user or self.user
        request.session = store()
        for key, value in (session or {}).items():
            request.session[key] = value
        return request

    def _run_middleware(self, session=None, user=None):
        request = self._request(session, user=user)
        current_user = CurrentUserMiddleware(get_response=lambda inner: None)
        current_user_tokens = current_user.process_request(request)
        tenant = TenantMiddleware(get_response=lambda inner: None)
        previous_scope = tenant.process_request(request)
        return request, current_user, current_user_tokens, tenant, previous_scope

    @staticmethod
    def _finish_middleware(request, current_user, current_user_tokens, tenant, previous_scope):
        tenant.process_response(request, None, previous_scope)
        current_user.process_response(request, None, current_user_tokens)

    def test_preferences_offer_all_tenants_and_every_reachable_workspace(self):
        form = UserPreferencesForm(user=self.user)
        choices = dict(form.fields["default_workspace"].choices)

        self.assertEqual(choices["all"], "All Tenants")
        self.assertEqual(choices[f"tenant:{self.home.pk}"], "Home Tenant")
        self.assertEqual(choices[f"tenant:{self.customer.pk}"], "Customer Tenant")
        self.assertEqual(choices[f"group:{self.group.pk}"], "Customer Group")
        self.assertIn("", choices)
        self.assertNotIn(f"tenant:{self.foreign.pk}", choices)

    def test_saved_tenant_default_is_applied_before_home_tenant_fallback(self):
        UserPreference.objects.create(
            user=self.user,
            data={"default_workspace": f"tenant:{self.customer.pk}"},
        )

        request, current_user, tokens, tenant, previous_scope = self._run_middleware()
        try:
            self.assertEqual(request.active_tenant, self.customer)
            self.assertIsNone(request.active_tenant_group)
            self.assertFalse(request.active_all_accessible)
            self.assertEqual(request.session["active_tenant_id"], self.customer.pk)
        finally:
            self._finish_middleware(request, current_user, tokens, tenant, previous_scope)

    def test_saved_all_tenants_default_activates_aggregate_scope(self):
        UserPreference.objects.create(user=self.user, data={"default_workspace": "all"})

        request, current_user, tokens, tenant, previous_scope = self._run_middleware()
        try:
            self.assertIsNone(request.active_tenant)
            self.assertIsNone(request.active_tenant_group)
            self.assertTrue(request.active_all_accessible)
            self.assertTrue(request.session["active_all_accessible"])
            self.assertNotIn("active_tenant_id", request.session)
            self.assertNotIn("active_tenant_group_id", request.session)
        finally:
            self._finish_middleware(request, current_user, tokens, tenant, previous_scope)

    def test_saved_group_default_is_applied_before_home_tenant_fallback(self):
        UserPreference.objects.create(
            user=self.user,
            data={"default_workspace": f"group:{self.group.pk}"},
        )

        request, current_user, tokens, tenant, previous_scope = self._run_middleware()
        try:
            self.assertIsNone(request.active_tenant)
            self.assertEqual(request.active_tenant_group, self.group)
            self.assertFalse(request.active_all_accessible)
            self.assertEqual(request.session["active_tenant_group_id"], self.group.pk)
        finally:
            self._finish_middleware(request, current_user, tokens, tenant, previous_scope)

    def test_existing_session_selection_overrides_saved_default(self):
        UserPreference.objects.create(
            user=self.user,
            data={"default_workspace": f"tenant:{self.customer.pk}"},
        )

        request, current_user, tokens, tenant, previous_scope = self._run_middleware(
            session={"active_tenant_id": self.home.pk},
        )
        try:
            self.assertEqual(request.active_tenant, self.home)
            self.assertFalse(request.active_all_accessible)
        finally:
            self._finish_middleware(request, current_user, tokens, tenant, previous_scope)

    def test_inaccessible_saved_default_falls_back_without_widening_scope(self):
        UserPreference.objects.create(
            user=self.user,
            data={"default_workspace": f"tenant:{self.foreign.pk}"},
        )

        request, current_user, tokens, tenant, previous_scope = self._run_middleware()
        try:
            self.assertEqual(request.active_tenant, self.home)
            self.assertNotEqual(request.active_tenant, self.foreign)
            self.assertFalse(request.active_all_accessible)
            self.assertEqual(request.session["active_tenant_id"], self.home.pk)
        finally:
            self._finish_middleware(request, current_user, tokens, tenant, previous_scope)

    def test_automatic_fallback_ignores_soft_deleted_membership_tenant(self):
        deleted = Tenant.objects.create(name="Deleted fallback tenant", slug="default-ws-deleted")
        provider = Tenant.objects.create(name="Fallback provider", slug="default-ws-provider", is_provider=True)
        managed = Tenant.objects.create(
            name="Fallback managed tenant",
            slug="default-ws-managed",
            managed_by=provider,
        )
        deleted_role = Role.objects.create(tenant=deleted, name="Deleted role", permissions=[])
        provider_role = Role.objects.create(tenant=provider, name="Provider role", permissions=[])
        user = User.objects.create_user(username="default-ws-soft-deleted", password="pw")
        grant(user, deleted, deleted_role)
        grant(
            user,
            provider,
            provider_role,
            reach=RoleGrant.REACH_MANAGED,
            managed_scope=RoleGrantScope.SCOPE_TENANT,
            assigned_tenants=[managed],
        )
        Tenant._base_manager.filter(pk=deleted.pk).update(deleted_at=timezone.now())

        request, current_user, tokens, tenant, previous_scope = self._run_middleware(user=user)
        try:
            self.assertIn(request.active_tenant.pk, {provider.pk, managed.pk})
            self.assertNotEqual(request.active_tenant.pk, deleted.pk)
            self.assertNotEqual(request.session.get("active_tenant_id"), deleted.pk)
        finally:
            self._finish_middleware(request, current_user, tokens, tenant, previous_scope)

    def test_workspace_key_parser_rejects_malformed_values(self):
        for value in (None, 7, "unknown", "tenant:", "tenant:0", "group:-1", "group:not-an-id"):
            with self.subTest(value=value):
                self.assertIsNone(parse_workspace_key(value))
        self.assertEqual(parse_workspace_key("tenant:12"), ("tenant", 12))
        self.assertEqual(parse_workspace_key("group:12"), ("group", 12))

    def test_no_access_user_gets_only_automatic_workspace_choice(self):
        stranger = User.objects.create_user(username="default-ws-stranger", password="pw")
        choices = dict(UserPreferencesForm(user=stranger).fields["default_workspace"].choices)
        self.assertEqual(choices, {"": "Automatic"})

    def test_superuser_can_resolve_global_and_group_defaults(self):
        superuser = User.objects.create_superuser(
            username="default-ws-superuser",
            email="default-ws-superuser@example.com",
            password="pw",
        )
        self.assertFalse(resolve_workspace_selection(superuser, "all").all_accessible)
        self.assertEqual(resolve_workspace_selection(superuser, f"group:{self.group.pk}").group, self.group)

    def test_inaccessible_or_unknown_group_default_is_rejected(self):
        other_group = TenantGroup.objects.create(name="Other Group", slug="default-ws-other-group")
        other_tenant = Tenant.objects.create(
            name="Other Tenant",
            slug="default-ws-other",
            group=other_group,
        )
        self.assertIsNone(resolve_workspace_selection(self.user, f"group:{other_group.pk}"))
        self.assertIsNone(resolve_workspace_selection(self.user, f"group:{other_tenant.pk + 100000}"))
        self.assertIsNone(resolve_workspace_selection(self.user, "unknown"))

    def test_apply_default_workspace_handles_aggregate_selection(self):
        request = self._request()
        selection = SimpleNamespace(tenant=None, group=None, all_accessible=True)
        with patch("itambox.middleware.resolve_default_workspace", return_value=selection):
            self.assertTrue(TenantMiddleware._apply_default_workspace(request))
        self.assertTrue(request.session["active_all_accessible"])
        self.assertNotIn("active_tenant_id", request.session)
        self.assertNotIn("active_tenant_group_id", request.session)

    def test_saving_automatic_workspace_clears_existing_default(self):
        UserPreference.objects.create(
            user=self.user,
            data={"default_workspace": f"tenant:{self.customer.pk}"},
        )
        form = UserPreferencesForm(user=self.user, data={})
        form.data = form.data.copy()
        form.data.update(
            {
                "pagination_per_page": form.fields["pagination_per_page"].choices[0][0],
                "theme": "light",
                "language": form.fields["language"].choices[0][0],
                "default_workspace": "",
            }
        )
        self.assertTrue(form.is_valid())
        form.save()
        self.assertNotIn("default_workspace", UserPreference.objects.get(user=self.user).data)

    def test_saving_default_workspace_applies_it_after_preferences_redirect(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.home.pk
        session.save()

        form = UserPreferencesForm(user=self.user)
        response = self.client.post(
            reverse("users:user_preferences"),
            data={
                "pagination_per_page": form.fields["pagination_per_page"].choices[0][0],
                "theme": "light",
                "language": form.fields["language"].choices[0][0],
                "default_workspace": f"tenant:{self.customer.pk}",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            UserPreference.objects.get(user=self.user).data["default_workspace"], f"tenant:{self.customer.pk}"
        )
        self.assertEqual(self.client.session.get("active_tenant_id"), self.customer.pk)
        self.assertNotIn("active_all_accessible", self.client.session)
