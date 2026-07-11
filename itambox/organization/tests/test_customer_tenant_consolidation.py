"""Regression tests for RBAC review §8 (fix #11): folding the standalone
``customer-tenants`` route into the main tenant list.

Post RBAC-Stage-2 collapse (``organization.Provider`` deleted; a "provider" is now
just ``Tenant(is_provider=True)``, with customer tenants pointing back via
``Tenant.managed_by``), this covers the successor gates from
``scratch/RBAC_STAGE2_SPEC.md`` §6:

  (a) ``organization:customer_tenant_list`` no longer resolves.
  (b) ``TenantFilterSet`` exposes the ``managed_by`` (narrow to one managing tenant)
      and ``is_provider`` filters.
  (c) an ordinary tenant user's tenant list stays tenant-scoped — the cross-tenant
      opt-in (``?all_providers=true``) is IGNORED for a user who holds no
      ``organization.change_tenant`` on any ``is_provider`` tenant, so no other
      tenant's rows leak.
  (d) a user holding ``organization.change_tenant`` on an ``is_provider`` tenant CAN
      opt into the widened set via ``?all_providers=true`` — scoped to ONLY the
      tenants managed by the ``is_provider`` tenant(s) they administer (no cross-MSP
      leak: an admin of MSP-A must never see MSP-B's managed tenants).
  (e) a superuser opting in sees every managed tenant across every MSP.
"""
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import NoReverseMatch, reverse

from core.tests.mixins import TenantTestMixin
from organization.filters import TenantFilterSet
from organization.models import Role, Tenant

User = get_user_model()


class CustomerTenantConsolidationTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.clear_tenant_context()

        # Two managing (is_provider) tenants, each managing its own customer tenant.
        # Plus a standalone tenant (no managed_by) that the ordinary user belongs to.
        self.msp_a = Tenant.objects.create(name="Alpha MSP", slug="alpha-msp", is_provider=True)
        self.msp_b = Tenant.objects.create(name="Bravo MSP", slug="bravo-msp", is_provider=True)
        self.customer_a = Tenant.objects.create(
            name="Customer A", slug="customer-a", managed_by=self.msp_a,
        )
        self.customer_b = Tenant.objects.create(
            name="Customer B", slug="customer-b", managed_by=self.msp_b,
        )
        # The tenant the ordinary user is a member of (standalone, not managed).
        self.own_tenant = Tenant.objects.create(name="Own Co", slug="own-co")

        # An ordinary tenant user: member of own_tenant only, with view_tenant so the
        # list view is reachable (200). Deliberately holds no organization.change_tenant
        # anywhere — not an MSP admin.
        self.ordinary_role = Role.objects.create(
            tenant=self.own_tenant,
            name="Ordinary",
            permissions=["organization.view_tenant"],
        )
        self.ordinary_user = User.objects.create_user(
            username="ordinary", email="ordinary@example.com", password="pw",
            is_active=True,
        )
        self.grant(self.ordinary_user, self.own_tenant, self.ordinary_role)

        # An MSP-A admin: own-reach role at msp_a holding organization.change_tenant
        # (+ view_tenant so the list renders). Non-superuser — must qualify via the
        # same organization.change_tenant gate the nav/menu helper uses.
        self.msp_a_admin_role = Role.objects.create(
            tenant=self.msp_a,
            name="MSP Admin",
            permissions=["organization.change_tenant", "organization.view_tenant"],
        )
        self.msp_a_admin = User.objects.create_user(
            username="msp_a_admin", email="msp_a_admin@example.com", password="pw",
            is_active=True,
        )
        self.grant(self.msp_a_admin, self.msp_a, self.msp_a_admin_role)

        self.list_url = reverse("organization:tenant_list")

    def tearDown(self):
        self.clear_tenant_context()

    # --- (a) route removed -------------------------------------------------
    def test_customer_tenant_list_route_removed(self):
        with self.assertRaises(NoReverseMatch):
            reverse("organization:customer_tenant_list")

    # --- (b) filter exposed ------------------------------------------------
    def test_filterset_exposes_managed_by_and_is_provider_filters(self):
        fs = TenantFilterSet()
        self.assertIn("managed_by", fs.filters)
        self.assertIn("is_provider", fs.filters)

    def test_managed_by_filter_selects_tenants_managed_by_given_tenant(self):
        # The ``managed_by`` field's own choices are a per-request queryset (see
        # ``_tenant_filter_managed_by_queryset``, which scopes the dropdown to avoid
        # a cross-MSP enumeration leak) — pass an authenticated request so msp_a is a
        # valid choice. The MSP-A admin holds ``organization.change_tenant`` on
        # msp_a, which is exactly what qualifies it for the dropdown.
        request = RequestFactory().get("/")
        request.user = self.msp_a_admin
        fs = TenantFilterSet(
            {"managed_by": str(self.msp_a.pk)},
            queryset=Tenant._base_manager.all(),
            request=request,
        )
        self.assertTrue(fs.is_valid(), fs.errors)
        pks = set(fs.qs.values_list("pk", flat=True))
        self.assertIn(self.customer_a.pk, pks)
        self.assertNotIn(self.customer_b.pk, pks)
        self.assertNotIn(self.own_tenant.pk, pks)

    def test_is_provider_filter_selects_provider_tenants(self):
        fs = TenantFilterSet(
            {"is_provider": "true"},
            queryset=Tenant._base_manager.all(),
        )
        self.assertTrue(fs.is_valid(), fs.errors)
        pks = set(fs.qs.values_list("pk", flat=True))
        self.assertIn(self.msp_a.pk, pks)
        self.assertIn(self.msp_b.pk, pks)
        self.assertNotIn(self.customer_a.pk, pks)
        self.assertNotIn(self.own_tenant.pk, pks)

    # --- (c) ordinary user stays tenant-scoped -----------------------------
    def _list_pks(self, response):
        return {row.pk for row in response.context["table"].data}

    def test_ordinary_user_list_is_tenant_scoped(self):
        self.client.force_login(self.ordinary_user)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        pks = self._list_pks(response)
        # Only their own (active) tenant — no managed tenants leak.
        self.assertEqual(pks, {self.own_tenant.pk})
        self.assertNotIn(self.customer_a.pk, pks)
        self.assertNotIn(self.customer_b.pk, pks)

    def test_ordinary_user_cannot_opt_into_cross_tenant_set(self):
        """SECURITY: the ?all_providers=true toggle is honoured ONLY for a user
        holding organization.change_tenant on an is_provider tenant. An ordinary
        user passing it must NOT widen their scope."""
        self.client.force_login(self.ordinary_user)
        response = self.client.get(self.list_url, {"all_providers": "true"})
        self.assertEqual(response.status_code, 200)
        pks = self._list_pks(response)
        self.assertEqual(pks, {self.own_tenant.pk})
        self.assertNotIn(self.customer_a.pk, pks)
        self.assertNotIn(self.customer_b.pk, pks)
        self.assertFalse(response.context["viewing_all_providers"])
        self.assertFalse(response.context["can_view_all_providers"])

    # --- (d) MSP admin opt-in: OWN managed tenants only, no cross-MSP leak ------
    def test_msp_admin_opt_in_shows_only_own_managed_tenants(self):
        """An MSP-A admin opting in sees MSP-A's customer tenants — but NOT another
        MSP's (RBAC review #11 HIGH: ``?all_providers=true`` must scope to the
        is_provider tenants the user actually administers, never every managed
        tenant system-wide). The standalone tenant never appears in the widened set."""
        self.client.force_login(self.msp_a_admin)
        # Establish the MSP tenant itself as the active tenant — that's where the
        # admin holds their own-reach membership, so it satisfies the ambient
        # organization.view_tenant permission check the list view requires.
        session = self.client.session
        session["active_tenant_id"] = self.msp_a.pk
        session.save()

        response = self.client.get(self.list_url, {"all_providers": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_view_all_providers"])
        self.assertTrue(response.context["viewing_all_providers"])

        pks = self._list_pks(response)
        self.assertIn(self.customer_a.pk, pks)        # own managed tenant
        self.assertNotIn(self.customer_b.pk, pks)      # another MSP's — must NOT leak
        self.assertNotIn(self.own_tenant.pk, pks)      # standalone tenant
        self.assertNotIn(self.msp_a.pk, pks)            # the managing tenant itself isn't a "managed" row

    def test_msp_admins_are_isolated_from_each_other(self):
        """Two single-MSP admins: neither sees the other's managed tenants under
        ``?all_providers=true`` (RBAC review #11 HIGH — cross-MSP isolation)."""
        msp_b_admin_role = Role.objects.create(
            tenant=self.msp_b, name="MSP B Admin",
            permissions=["organization.change_tenant", "organization.view_tenant"],
        )
        msp_b_admin = User.objects.create_user(
            username="msp_b_admin", email="msp_b_admin@example.com", password="pw",
            is_active=True,
        )
        self.grant(msp_b_admin, self.msp_b, msp_b_admin_role)

        self.client.force_login(msp_b_admin)
        session = self.client.session
        session["active_tenant_id"] = self.msp_b.pk
        session.save()

        response = self.client.get(self.list_url, {"all_providers": "true"})
        self.assertEqual(response.status_code, 200)
        pks = self._list_pks(response)
        self.assertIn(self.customer_b.pk, pks)        # own managed tenant
        self.assertNotIn(self.customer_a.pk, pks)      # MSP-A's — must NOT leak

    def test_superuser_opt_in_shows_all_managed_tenants(self):
        """A superuser opting in sees every managed tenant across all MSPs (the one
        principal legitimately allowed the full cross-tenant view)."""
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
        self.assertNotIn(self.own_tenant.pk, pks)      # standalone tenant excluded

    def test_msp_admin_without_opt_in_stays_scoped(self):
        """Without the toggle the MSP admin's list is the ordinary tenant-scoped
        list — the cross-tenant set is strictly opt-in."""
        self.client.force_login(self.msp_a_admin)
        session = self.client.session
        session["active_tenant_id"] = self.msp_a.pk
        session.save()

        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["viewing_all_providers"])
        pks = self._list_pks(response)
        # Scoped to the single active tenant (the MSP tenant itself), not the whole
        # cross-tenant managed set.
        self.assertEqual(pks, {self.msp_a.pk})
        self.assertNotIn(self.customer_a.pk, pks)
        self.assertNotIn(self.customer_b.pk, pks)

    def test_user_with_change_tenant_only_on_managed_tenant_cannot_opt_in(self):
        """Holding organization.change_tenant scoped to a MANAGED tenant (not an
        is_provider one) must not satisfy the gate — ``_manageable_provider_ids``
        only counts ``is_provider`` tenants where the user holds the permission
        directly, not permissions projected onto tenants they manage."""
        customer_admin_role = Role.objects.create(
            tenant=self.customer_a, name="Customer A Admin",
            permissions=["organization.change_tenant", "organization.view_tenant"],
        )
        customer_admin = User.objects.create_user(
            username="customer_a_admin", email="customer_a_admin@example.com", password="pw",
            is_active=True,
        )
        self.grant(customer_admin, self.customer_a, customer_admin_role)

        self.client.force_login(customer_admin)
        response = self.client.get(self.list_url, {"all_providers": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_view_all_providers"])
        self.assertFalse(response.context["viewing_all_providers"])
        pks = self._list_pks(response)
        self.assertEqual(pks, {self.customer_a.pk})
        self.assertNotIn(self.customer_b.pk, pks)
