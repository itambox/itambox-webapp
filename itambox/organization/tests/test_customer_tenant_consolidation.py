"""Regression tests for RBAC review §8 (fix #11): folding the standalone
``customer-tenants`` route into the main tenant list.

Covers:
  (a) ``organization:customer_tenant_list`` no longer resolves.
  (b) ``TenantFilterSet`` exposes the "managed by provider" filter (+ a provider filter).
  (c) an ordinary tenant user's tenant list stays tenant-scoped — the cross-provider
      opt-in (``?all_providers=true``) is IGNORED for a non-provider-admin, so no
      other-tenant rows leak.
  (d) a provider-admin CAN opt into the cross-provider set via ``?all_providers=true``.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from core.tests.mixins import TenantTestMixin
from organization.filters import TenantFilterSet
from organization.models import Membership, Provider, Role, Tenant

User = get_user_model()


class CustomerTenantConsolidationTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.clear_tenant_context()

        # Two providers, each managing its own customer tenant. Plus a standalone
        # (provider-less) tenant that the ordinary user belongs to.
        self.provider_a = Provider.objects.create(name="Alpha MSP", slug="alpha-msp")
        self.provider_b = Provider.objects.create(name="Bravo MSP", slug="bravo-msp")
        self.customer_a = Tenant.objects.create(
            name="Customer A", slug="customer-a", provider=self.provider_a,
        )
        self.customer_b = Tenant.objects.create(
            name="Customer B", slug="customer-b", provider=self.provider_b,
        )
        # The tenant the ordinary user is a member of (no provider).
        self.own_tenant = Tenant.objects.create(name="Own Co", slug="own-co")

        # An ordinary tenant user: member of own_tenant only, with view_tenant so the
        # list view is reachable (200). Deliberately NOT a provider admin.
        self.ordinary_role = Role.objects.create(
            tenant=self.own_tenant,
            name="Ordinary",
            permissions=["organization.view_tenant"],
        )
        self.ordinary_user = User.objects.create_user(
            username="ordinary", email="ordinary@example.com", password="pw",
            is_active=True,
        )
        self.ordinary_membership = Membership.objects.create(
            user=self.ordinary_user, tenant=self.own_tenant, is_active=True,
        )
        self.ordinary_membership.roles.add(self.ordinary_role)

        # A provider-admin: provider-A staff holding organization.manage_provider (+
        # view_tenant so the list renders). Non-superuser — must qualify via the
        # unified capability, exactly like the old ProviderAdminMixin.
        self.provider_admin_role = Role.objects.create(
            provider=self.provider_a,
            scope=Role.SCOPE_PROVIDER,
            name="Provider Admin",
            permissions=["organization.manage_provider", "organization.view_tenant"],
        )
        self.provider_admin = User.objects.create_user(
            username="prov_admin", email="prov_admin@example.com", password="pw",
            is_active=True,
        )
        self.provider_admin_membership = Membership.objects.create(
            user=self.provider_admin, provider=self.provider_a,
            tenant_scope=Membership.SCOPE_ALL, is_active=True,
        )
        self.provider_admin_membership.roles.add(self.provider_admin_role)

        self.list_url = reverse("organization:tenant_list")

    def tearDown(self):
        self.clear_tenant_context()

    # --- (a) route removed -------------------------------------------------
    def test_customer_tenant_list_route_removed(self):
        with self.assertRaises(NoReverseMatch):
            reverse("organization:customer_tenant_list")

    # --- (b) filter exposed ------------------------------------------------
    def test_filterset_exposes_managed_by_provider_filter(self):
        fs = TenantFilterSet()
        self.assertIn("managed_by_provider", fs.filters)
        # And a provider ModelChoice filter for narrowing to one provider.
        self.assertIn("provider", fs.filters)

    def test_managed_by_provider_filter_selects_provider_managed_tenants(self):
        # Unscoped (base manager) sanity check of the filter's predicate itself.
        fs = TenantFilterSet(
            {"managed_by_provider": "true"},
            queryset=Tenant._base_manager.all(),
        )
        self.assertTrue(fs.is_valid(), fs.errors)
        pks = set(fs.qs.values_list("pk", flat=True))
        self.assertIn(self.customer_a.pk, pks)
        self.assertIn(self.customer_b.pk, pks)
        self.assertNotIn(self.own_tenant.pk, pks)

    # --- (c) ordinary user stays tenant-scoped -----------------------------
    def _list_pks(self, response):
        return {row.pk for row in response.context["table"].data}

    def test_ordinary_user_list_is_tenant_scoped(self):
        self.client.force_login(self.ordinary_user)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        pks = self._list_pks(response)
        # Only their own (active) tenant — no provider-managed tenants leak.
        self.assertEqual(pks, {self.own_tenant.pk})
        self.assertNotIn(self.customer_a.pk, pks)
        self.assertNotIn(self.customer_b.pk, pks)

    def test_ordinary_user_cannot_opt_into_cross_provider_set(self):
        """SECURITY: the ?all_providers=true toggle is honoured ONLY for provider
        admins. An ordinary user passing it must NOT widen their scope."""
        self.client.force_login(self.ordinary_user)
        response = self.client.get(self.list_url, {"all_providers": "true"})
        self.assertEqual(response.status_code, 200)
        pks = self._list_pks(response)
        self.assertEqual(pks, {self.own_tenant.pk})
        self.assertNotIn(self.customer_a.pk, pks)
        self.assertNotIn(self.customer_b.pk, pks)
        self.assertFalse(response.context["viewing_all_providers"])
        self.assertFalse(response.context["can_view_all_providers"])

    # --- (d) provider-admin opt-in: OWN provider only, no cross-MSP leak --------
    def test_provider_admin_opt_in_shows_only_own_provider_tenants(self):
        """A provider-A admin opting in sees provider-A's customer tenants — but NOT
        another MSP's (RBAC review #11 HIGH: ``?all_providers=true`` must scope to the
        providers the user actually manages, never every provider system-wide). The
        provider-less standalone tenant never appears in the widened set."""
        self.client.force_login(self.provider_admin)
        # Establish provider-A's customer tenant as the active tenant (the admin's
        # accessible scope is SCOPE_ALL, so customer_a is reachable).
        session = self.client.session
        session["active_tenant_id"] = self.customer_a.pk
        session.save()

        response = self.client.get(self.list_url, {"all_providers": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_view_all_providers"])
        self.assertTrue(response.context["viewing_all_providers"])

        pks = self._list_pks(response)
        self.assertIn(self.customer_a.pk, pks)       # own provider's tenant
        self.assertNotIn(self.customer_b.pk, pks)    # another MSP's — must NOT leak
        self.assertNotIn(self.own_tenant.pk, pks)    # provider-less standalone

    def test_provider_admins_are_isolated_from_each_other(self):
        """Two single-provider admins: neither sees the other's customer tenants under
        ``?all_providers=true`` (RBAC review #11 HIGH — cross-MSP isolation)."""
        b_admin_role = Role.objects.create(
            provider=self.provider_b, scope=Role.SCOPE_PROVIDER, name="B Admin",
            permissions=["organization.manage_provider", "organization.view_tenant"],
        )
        b_admin = User.objects.create_user(
            username="prov_admin_b", email="prov_admin_b@example.com", password="pw",
            is_active=True,
        )
        b_membership = Membership.objects.create(
            user=b_admin, provider=self.provider_b,
            tenant_scope=Membership.SCOPE_ALL, is_active=True,
        )
        b_membership.roles.add(b_admin_role)

        self.client.force_login(b_admin)
        session = self.client.session
        session["active_tenant_id"] = self.customer_b.pk
        session.save()

        response = self.client.get(self.list_url, {"all_providers": "true"})
        self.assertEqual(response.status_code, 200)
        pks = self._list_pks(response)
        self.assertIn(self.customer_b.pk, pks)       # own provider's tenant
        self.assertNotIn(self.customer_a.pk, pks)    # provider-A's — must NOT leak

    def test_superuser_opt_in_shows_all_provider_managed_tenants(self):
        """A superuser opting in sees every provider-managed tenant across all MSPs
        (the one principal legitimately allowed the full cross-provider view)."""
        su = User.objects.create_user(
            username="root", email="root@example.com", password="pw",
            is_active=True, is_superuser=True, is_staff=True,
        )
        self.client.force_login(su)
        response = self.client.get(self.list_url, {"all_providers": "true"})
        self.assertEqual(response.status_code, 200)
        pks = self._list_pks(response)
        self.assertIn(self.customer_a.pk, pks)
        self.assertIn(self.customer_b.pk, pks)
        self.assertNotIn(self.own_tenant.pk, pks)    # provider-less standalone excluded

    def test_provider_admin_without_opt_in_stays_scoped(self):
        """Without the toggle the provider-admin's list is the ordinary tenant-scoped
        list — the cross-provider set is strictly opt-in."""
        self.client.force_login(self.provider_admin)
        session = self.client.session
        session["active_tenant_id"] = self.customer_a.pk
        session.save()

        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["viewing_all_providers"])
        pks = self._list_pks(response)
        # Scoped to the single active tenant, not the whole cross-provider set.
        self.assertEqual(pks, {self.customer_a.pk})
        self.assertNotIn(self.customer_b.pk, pks)
