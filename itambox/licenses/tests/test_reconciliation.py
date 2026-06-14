"""Tests for SAM license reconciliation (licenses/reconciliation.py).

Covers:
- compliant case   (installs <= entitled seats)
- over-deployed    (installs > entitled seats)
- unlicensed       (installs > 0, entitled seats == 0)
- no installs      (installs == 0, seated licenses → still compliant)
- reconcile_tenant_licensing() aggregation
- tenant isolation (tenant A's data never leaks into tenant B's results)

Uses TenantTestMixin + model_bakery per project conventions.
"""

from django.test import TestCase
from model_bakery import baker

from core.tests.mixins import TenantTestMixin
from licenses.models import License, LicenseTypeChoices
from licenses.reconciliation import (
    reconcile_software,
    reconcile_tenant_licensing,
    STATUS_COMPLIANT,
    STATUS_OVER_DEPLOYED,
    STATUS_UNLICENSED,
)
from software.models import Software, InstalledSoftware
from assets.models import Asset


def _make_software(tenant=None, name='Test App'):
    """Create a Software catalogue entry (global when tenant=None).

    Manufacturer name is derived from the (per-test unique) software name so a
    test creating several Software entries doesn't collide on the Manufacturer
    name unique constraint; the slug auto-generates from the name on save.
    """
    return baker.make(
        Software,
        name=name,
        manufacturer__name=f'Mfr {name}',
        tenant=tenant,
    )


def _make_license(software, tenant, seats=5):
    """Create an active License with the given seat count."""
    return baker.make(
        License,
        software=software,
        tenant=tenant,
        seats=seats,
        license_type=LicenseTypeChoices.PERPETUAL_SEAT,
    )


def _make_asset(tenant):
    """Create an Asset belonging to *tenant*."""
    return baker.make(Asset, tenant=tenant)


def _make_install(software, asset):
    """Record an InstalledSoftware row on *asset* for *software*."""
    return baker.make(InstalledSoftware, software=software, asset=asset)


class ReconcileSoftwareTests(TenantTestMixin, TestCase):
    """Unit tests for reconcile_software()."""

    def setUp(self):
        self.setup_tenant_context(name='Acme Corp', slug='acme-corp')

    # ── compliant ─────────────────────────────────────────────────────────────

    def test_compliant_installs_equal_seats(self):
        with self.tenant_context(self.tenant):
            sw = _make_software(tenant=self.tenant, name='Visio 2021')
            _make_license(sw, self.tenant, seats=3)
            # 3 installs on 3 distinct assets (InstalledSoftware is unique per
            # asset+software), matching the 3 entitled seats.
            for _ in range(3):
                _make_install(sw, _make_asset(self.tenant))

            result = reconcile_software(sw)

        self.assertEqual(result['installed_count'], 3)
        self.assertEqual(result['entitled_seats'], 3)
        self.assertEqual(result['delta'], 0)
        self.assertTrue(result['compliant'])
        self.assertEqual(result['status'], STATUS_COMPLIANT)

    def test_compliant_installs_less_than_seats(self):
        with self.tenant_context(self.tenant):
            sw = _make_software(tenant=self.tenant, name='Word 365')
            asset = _make_asset(self.tenant)
            _make_license(sw, self.tenant, seats=10)
            _make_install(sw, asset)

            result = reconcile_software(sw)

        self.assertEqual(result['installed_count'], 1)
        self.assertEqual(result['entitled_seats'], 10)
        self.assertEqual(result['delta'], 9)
        self.assertTrue(result['compliant'])
        self.assertEqual(result['status'], STATUS_COMPLIANT)

    def test_compliant_no_installs(self):
        """A seated license with zero installs is still compliant."""
        with self.tenant_context(self.tenant):
            sw = _make_software(tenant=self.tenant, name='Excel 365')
            _make_license(sw, self.tenant, seats=5)

            result = reconcile_software(sw)

        self.assertEqual(result['installed_count'], 0)
        self.assertEqual(result['entitled_seats'], 5)
        self.assertTrue(result['compliant'])
        self.assertEqual(result['status'], STATUS_COMPLIANT)

    # ── over-deployed ─────────────────────────────────────────────────────────

    def test_over_deployed(self):
        with self.tenant_context(self.tenant):
            sw = _make_software(tenant=self.tenant, name='AutoCAD 2024')
            _make_license(sw, self.tenant, seats=2)
            for _ in range(4):
                asset = _make_asset(self.tenant)
                _make_install(sw, asset)

            result = reconcile_software(sw)

        self.assertEqual(result['installed_count'], 4)
        self.assertEqual(result['entitled_seats'], 2)
        self.assertEqual(result['delta'], -2)
        self.assertFalse(result['compliant'])
        self.assertEqual(result['status'], STATUS_OVER_DEPLOYED)

    # ── unlicensed ────────────────────────────────────────────────────────────

    def test_unlicensed(self):
        with self.tenant_context(self.tenant):
            sw = _make_software(tenant=self.tenant, name='Rogue App')
            asset = _make_asset(self.tenant)
            _make_install(sw, asset)
            # No license created

            result = reconcile_software(sw)

        self.assertEqual(result['installed_count'], 1)
        self.assertEqual(result['entitled_seats'], 0)
        self.assertEqual(result['delta'], -1)
        self.assertFalse(result['compliant'])
        self.assertEqual(result['status'], STATUS_UNLICENSED)

    # ── multiple licenses aggregated ──────────────────────────────────────────

    def test_multiple_licenses_summed(self):
        """Seats from multiple licenses for the same software are summed."""
        with self.tenant_context(self.tenant):
            sw = _make_software(tenant=self.tenant, name='Windows 11')
            _make_license(sw, self.tenant, seats=10)
            _make_license(sw, self.tenant, seats=5)
            asset = _make_asset(self.tenant)
            _make_install(sw, asset)

            result = reconcile_software(sw)

        self.assertEqual(result['entitled_seats'], 15)
        self.assertTrue(result['compliant'])

    # ── result dict shape ─────────────────────────────────────────────────────

    def test_result_dict_keys(self):
        with self.tenant_context(self.tenant):
            sw = _make_software(tenant=self.tenant, name='KeyCheck')
            result = reconcile_software(sw)

        expected_keys = {
            'software_id', 'software_name',
            'installed_count', 'entitled_seats',
            'delta', 'compliant', 'status',
            'linked_seats',
        }
        self.assertEqual(set(result.keys()), expected_keys)
        self.assertEqual(result['software_id'], sw.pk)


class ReconcileTenantLicensingTests(TenantTestMixin, TestCase):
    """Tests for reconcile_tenant_licensing()."""

    def setUp(self):
        self.setup_tenant_context(name='Beta Inc', slug='beta-inc')

    def test_empty_result_when_no_data(self):
        with self.tenant_context(self.tenant):
            results = reconcile_tenant_licensing()
        self.assertEqual(results, [])

    def test_includes_only_relevant_software(self):
        """Software with neither installs nor licenses is excluded."""
        with self.tenant_context(self.tenant):
            sw_licensed = _make_software(tenant=self.tenant, name='A Licensed App')
            sw_bare = _make_software(tenant=self.tenant, name='Z Bare Catalogue Entry')
            _make_license(sw_licensed, self.tenant, seats=3)

            results = reconcile_tenant_licensing()

        result_ids = {r['software_id'] for r in results}
        self.assertIn(sw_licensed.pk, result_ids)
        self.assertNotIn(sw_bare.pk, result_ids)

    def test_sorted_by_software_name(self):
        with self.tenant_context(self.tenant):
            sw_z = _make_software(tenant=self.tenant, name='Zebra Suite')
            sw_a = _make_software(tenant=self.tenant, name='Alpha Viewer')
            _make_license(sw_z, self.tenant, seats=1)
            _make_license(sw_a, self.tenant, seats=1)

            results = reconcile_tenant_licensing()

        names = [r['software_name'] for r in results]
        self.assertEqual(names, sorted(names))

    def test_multiple_software_entries(self):
        with self.tenant_context(self.tenant):
            sw1 = _make_software(tenant=self.tenant, name='App One')
            sw2 = _make_software(tenant=self.tenant, name='App Two')
            _make_license(sw1, self.tenant, seats=5)
            _make_license(sw2, self.tenant, seats=2)
            asset = _make_asset(self.tenant)
            _make_install(sw2, asset)

            results = reconcile_tenant_licensing()

        self.assertEqual(len(results), 2)
        by_id = {r['software_id']: r for r in results}
        self.assertEqual(by_id[sw1.pk]['status'], STATUS_COMPLIANT)
        self.assertEqual(by_id[sw2.pk]['status'], STATUS_COMPLIANT)


class TenantIsolationTests(TenantTestMixin, TestCase):
    """Verify that tenant A's installs and seats never appear in tenant B's results.

    This is the critical security boundary test for the reconciliation module.
    """

    def setUp(self):
        self.setup_tenant_context(name='Tenant Alpha', slug='tenant-alpha')
        # Create a second tenant manually (TenantTestMixin only sets up one)
        from organization.models import Tenant
        self.tenant_b = Tenant.objects.create(name='Tenant Beta', slug='tenant-beta')

    def test_installs_scoped_to_active_tenant(self):
        """Installs on tenant B's assets must not count in tenant A's reconciliation."""
        # Use global (null-tenant) software so both tenants can reference it
        with self.tenant_context(self.tenant):
            sw = _make_software(tenant=None, name='Cross-Tenant App')
            _make_license(sw, self.tenant, seats=2)

        # Install the software on a tenant B asset — do this outside any context
        # so we bypass the tenant manager's filter_by_tenant guard, simulating
        # data that exists in the DB belonging to tenant B.
        asset_b = baker.make(Asset, tenant=self.tenant_b)
        baker.make(InstalledSoftware, software=sw, asset=asset_b)

        # Now reconcile from tenant A's perspective
        with self.tenant_context(self.tenant):
            result = reconcile_software(sw)

        # Tenant A has 0 installs (the install is on tenant B's asset)
        self.assertEqual(result['installed_count'], 0)
        self.assertEqual(result['entitled_seats'], 2)
        self.assertTrue(result['compliant'])

    def test_licenses_scoped_to_active_tenant(self):
        """Licenses owned by tenant B must not count in tenant A's entitled seats."""
        with self.tenant_context(self.tenant):
            sw = _make_software(tenant=None, name='Shared App')
            # No license for tenant A
            asset_a = _make_asset(self.tenant)
            _make_install(sw, asset_a)

        # Create a license for tenant B directly (bypassing context)
        baker.make(License, software=sw, tenant=self.tenant_b, seats=100,
                   license_type=LicenseTypeChoices.PERPETUAL_SEAT)

        # Reconcile from tenant A's view
        with self.tenant_context(self.tenant):
            result = reconcile_software(sw)

        # Tenant A has 1 install, 0 entitled seats → unlicensed
        self.assertEqual(result['installed_count'], 1)
        self.assertEqual(result['entitled_seats'], 0)
        self.assertEqual(result['status'], STATUS_UNLICENSED)

    def test_reconcile_tenant_licensing_excludes_other_tenant_data(self):
        """reconcile_tenant_licensing() must only return software relevant to the active tenant."""
        with self.tenant_context(self.tenant):
            sw_a = _make_software(tenant=self.tenant, name='Alpha Only App')
            _make_license(sw_a, self.tenant, seats=5)

        # Tenant B has its own software + license
        sw_b = baker.make(
            Software,
            name='Beta Only App',
            manufacturer__name='BetaCo',
            manufacturer__slug='betaco',
            tenant=self.tenant_b,
        )
        baker.make(License, software=sw_b, tenant=self.tenant_b, seats=3,
                   license_type=LicenseTypeChoices.PERPETUAL_SEAT)

        # Reconcile as tenant A
        with self.tenant_context(self.tenant):
            results = reconcile_tenant_licensing()

        result_ids = {r['software_id'] for r in results}
        self.assertIn(sw_a.pk, result_ids)
        self.assertNotIn(sw_b.pk, result_ids)


class SoftwareReconcileMethodTests(TenantTestMixin, TestCase):
    """Test the Software.reconcile() convenience method."""

    def setUp(self):
        self.setup_tenant_context(name='Method Test', slug='method-test')

    def test_reconcile_method_returns_correct_dict(self):
        with self.tenant_context(self.tenant):
            sw = _make_software(tenant=self.tenant, name='MethodApp')
            _make_license(sw, self.tenant, seats=5)
            asset = _make_asset(self.tenant)
            _make_install(sw, asset)

            result = sw.reconcile()

        self.assertEqual(result['software_id'], sw.pk)
        self.assertEqual(result['installed_count'], 1)
        self.assertEqual(result['entitled_seats'], 5)
        self.assertTrue(result['compliant'])

    def test_reconcile_method_reflects_current_state(self):
        """Calling reconcile() twice returns fresh data, not a cached snapshot."""
        with self.tenant_context(self.tenant):
            sw = _make_software(tenant=self.tenant, name='FreshApp')
            _make_license(sw, self.tenant, seats=3)

            result_before = sw.reconcile()
            self.assertEqual(result_before['installed_count'], 0)

            asset = _make_asset(self.tenant)
            _make_install(sw, asset)

            result_after = sw.reconcile()
            self.assertEqual(result_after['installed_count'], 1)
