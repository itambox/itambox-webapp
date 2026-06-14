"""Tests for:

1. License.cost_center FK (Task 1)
   - Default is None (blank/null).
   - Can be explicitly set to a CostCenter instance.

2. LicenseSeatAssignment.installed_software FK + clean() validation (Task 2)
   - Happy path: asset-assigned seat linked to an install on the same asset.
   - Rejection: holder-assigned seat with an install link set.
   - Rejection: install is on a *different* asset than the seat's asset.

Uses TenantTestMixin + model_bakery per project conventions.
The organization.CostCenter model is created by a concurrent agent; tests that
require it are skipped gracefully when the model is not yet available.
"""

from django.test import TestCase
from django.core.exceptions import ValidationError
from model_bakery import baker

from core.tests.mixins import TenantTestMixin
from licenses.models import License, LicenseTypeChoices, LicenseSeatAssignment
from software.models import Software, InstalledSoftware
from assets.models import Asset
from organization.models import AssetHolder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_cost_center_model():
    """Return organization.CostCenter or None if not yet migrated."""
    try:
        from django.apps import apps
        return apps.get_model('organization', 'CostCenter')
    except LookupError:
        return None


def _make_software(tenant=None, name='Test Software'):
    return baker.make(
        Software,
        name=name,
        manufacturer__name=f'Mfr {name}',
        tenant=tenant,
    )


def _make_license(software, tenant=None, seats=5):
    return baker.make(
        License,
        software=software,
        tenant=tenant,
        seats=seats,
        license_type=LicenseTypeChoices.PERPETUAL_SEAT,
    )


# ---------------------------------------------------------------------------
# Task 1 — License.cost_center
# ---------------------------------------------------------------------------

class LicenseCostCenterFieldTests(TenantTestMixin, TestCase):
    """Tests for the cost_center FK on License."""

    def setUp(self):
        self.setup_tenant_context(name='CC Tenant', slug='cc-tenant')
        with self.tenant_context(self.tenant):
            self.software = _make_software(tenant=self.tenant, name='CC App')

    def test_cost_center_defaults_to_null(self):
        """A license created without specifying cost_center should have None."""
        with self.tenant_context(self.tenant):
            lic = _make_license(self.software, self.tenant)
        self.assertIsNone(lic.cost_center_id)

    def test_cost_center_can_be_blank_on_save(self):
        """Saving a license without cost_center should not raise."""
        with self.tenant_context(self.tenant):
            lic = _make_license(self.software, self.tenant)
        lic.full_clean()  # should not raise

    def test_cost_center_field_exists(self):
        """License model must expose a cost_center_id attribute."""
        with self.tenant_context(self.tenant):
            lic = _make_license(self.software, self.tenant)
        self.assertTrue(hasattr(lic, 'cost_center_id'))

    def test_cost_center_can_be_assigned(self):
        """When CostCenter model is available, a license can be linked to one."""
        CostCenter = _get_cost_center_model()
        if CostCenter is None:
            self.skipTest('organization.CostCenter not yet migrated')

        with self.tenant_context(self.tenant):
            cc = baker.make(CostCenter, tenant=self.tenant)
            lic = _make_license(self.software, self.tenant)
            lic.cost_center = cc
            lic.save()
            lic.refresh_from_db()
            self.assertEqual(lic.cost_center_id, cc.pk)

    def test_related_manager_name(self):
        """CostCenter.licenses reverse relation exists when model is available."""
        CostCenter = _get_cost_center_model()
        if CostCenter is None:
            self.skipTest('organization.CostCenter not yet migrated')

        with self.tenant_context(self.tenant):
            cc = baker.make(CostCenter, tenant=self.tenant)
            lic = _make_license(self.software, self.tenant)
            lic.cost_center = cc
            lic.save()
            self.assertIn(lic, cc.licenses.all())


# ---------------------------------------------------------------------------
# Task 2 — LicenseSeatAssignment.installed_software link
# ---------------------------------------------------------------------------

class SeatInstallLinkTests(TenantTestMixin, TestCase):
    """Tests for the optional installed_software FK on LicenseSeatAssignment."""

    def setUp(self):
        self.setup_tenant_context(name='SAM Tenant', slug='sam-tenant')
        with self.tenant_context(self.tenant):
            self.software = _make_software(tenant=self.tenant, name='SAM App')
            self.license = _make_license(self.software, self.tenant, seats=10)
            self.asset = baker.make(Asset, tenant=self.tenant)
            self.other_asset = baker.make(Asset, tenant=self.tenant)
            self.holder = baker.make(AssetHolder, tenant=self.tenant)
            self.install = baker.make(
                InstalledSoftware,
                software=self.software,
                asset=self.asset,
            )
            self.other_install = baker.make(
                InstalledSoftware,
                software=self.software,
                asset=self.other_asset,
            )

    # ── happy path ────────────────────────────────────────────────────────────

    def test_seat_linked_to_install_on_same_asset(self):
        """Asset-assigned seat linked to an install on the same asset passes clean()."""
        with self.tenant_context(self.tenant):
            seat = LicenseSeatAssignment(
                license=self.license,
                asset=self.asset,
                assigned_holder=None,
                installed_software=self.install,
            )
            seat.full_clean()  # must not raise
            seat.save()
        self.assertEqual(seat.installed_software_id, self.install.pk)

    def test_seat_without_install_link_is_still_valid(self):
        """An asset-assigned seat with no install link is valid (link is optional)."""
        with self.tenant_context(self.tenant):
            seat = LicenseSeatAssignment(
                license=self.license,
                asset=self.asset,
                assigned_holder=None,
                installed_software=None,
            )
            seat.full_clean()  # must not raise
            seat.save()
        self.assertIsNone(seat.installed_software_id)

    def test_covering_seats_reverse_relation(self):
        """InstalledSoftware.covering_seats should return linked seat assignments."""
        with self.tenant_context(self.tenant):
            seat = baker.make(
                LicenseSeatAssignment,
                license=self.license,
                asset=self.asset,
                assigned_holder=None,
                installed_software=self.install,
            )
        self.assertIn(seat, self.install.covering_seats.all())

    # ── rejection: holder seat with install link ──────────────────────────────

    def test_holder_seat_with_install_link_raises(self):
        """A holder-assigned seat may not carry an install link."""
        with self.tenant_context(self.tenant):
            seat = LicenseSeatAssignment(
                license=self.license,
                asset=None,
                assigned_holder=self.holder,
                installed_software=self.install,
            )
            with self.assertRaises(ValidationError) as ctx:
                seat.full_clean()
        errors = ctx.exception.message_dict
        self.assertIn('installed_software', errors)

    # ── rejection: install on a different asset ───────────────────────────────

    def test_install_on_different_asset_raises(self):
        """The linked install must be on the same asset as the seat."""
        with self.tenant_context(self.tenant):
            seat = LicenseSeatAssignment(
                license=self.license,
                asset=self.asset,
                assigned_holder=None,
                installed_software=self.other_install,  # wrong asset
            )
            with self.assertRaises(ValidationError) as ctx:
                seat.full_clean()
        errors = ctx.exception.message_dict
        self.assertIn('installed_software', errors)

    # ── reconciliation: linked_seats count ───────────────────────────────────

    def test_reconcile_linked_seats_counted(self):
        """reconcile_software() reports the correct linked_seats count."""
        from licenses.reconciliation import reconcile_software

        with self.tenant_context(self.tenant):
            # Create two seats: one with an install link, one without.
            baker.make(
                LicenseSeatAssignment,
                license=self.license,
                asset=self.asset,
                assigned_holder=None,
                installed_software=self.install,
            )
            baker.make(
                LicenseSeatAssignment,
                license=self.license,
                asset=self.other_asset,
                assigned_holder=None,
                installed_software=None,
            )
            result = reconcile_software(self.software)

        self.assertEqual(result['linked_seats'], 1)

    def test_reconcile_linked_seats_zero_when_none(self):
        """linked_seats is 0 when no seats have an install link."""
        from licenses.reconciliation import reconcile_software

        with self.tenant_context(self.tenant):
            baker.make(
                LicenseSeatAssignment,
                license=self.license,
                asset=self.asset,
                assigned_holder=None,
                installed_software=None,
            )
            result = reconcile_software(self.software)

        self.assertEqual(result['linked_seats'], 0)
