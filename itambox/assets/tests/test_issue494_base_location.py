"""A person assignment changes responsibility, not the asset's base location."""

from django.template.loader import render_to_string
from django.test import TestCase
from django.utils.translation import override
from model_bakery import baker

from assets.filters import AssetFilterSet
from assets.models import Asset, StatusLabel
from assets.services import checkin_asset, checkout_asset, checkout_kit
from assets.tests.test_issue492_fulfillment import _all_accessible_scope
from compliance.audit_services import expected_assets_queryset
from compliance.models import AuditSession
from core.tests.mixins import TenantTestMixin
from inventory.models import Kit, KitItem
from organization.models import AssetHolder, Location, Role, Tenant


class BaseLocationTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(permissions=["compliance.view_auditsession"])
        self.set_active_tenant(self.tenant, self.tenant_membership)
        self.deployable = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE)
        self.deployed = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYED)
        self.base = baker.make(Location, tenant=self.tenant, name="Office 203")
        self.holder = baker.make(AssetHolder, tenant=self.tenant, user=self.tenant_user)
        self.asset_type = baker.make("assets.AssetType")
        self.asset = baker.make(
            Asset, tenant=self.tenant, asset_type=self.asset_type, location=self.base, status=self.deployable
        )

    def tearDown(self):
        self.clear_tenant_context()
        super().tearDown()

    def test_person_assignment_panel_explains_base_location(self):
        checkout_asset(self.asset, holder=self.holder, user=self.tenant_user)
        self.asset.refresh_from_db()
        html = render_to_string(
            "assets/includes/detail/asset_assignment.html",
            {
                "object": self.asset,
                "assignment": self.asset.active_assignment,
            },
        )
        self.assertIn("Base / Storage Location:", html)
        self.assertIn("Not a live position", html)
        self.assertNotIn("Physical Location:", html)
        self.assertIn("Office 203", html)

    def test_german_panel_uses_compiled_base_location_copy(self):
        with override("de"):
            html = render_to_string("assets/includes/detail/asset_assignment.html", {"object": self.asset})
        self.assertIn("Basis-/Lagerstandort:", html)
        self.assertIn("Kein Echtzeitstandort", html)

    def test_checkout_to_parent_with_unknown_location_keeps_existing_behavior(self):
        parent = baker.make(Asset, tenant=self.tenant, location=None, status=self.deployable)
        checkout_asset(self.asset, asset_target=parent, user=self.tenant_user)
        self.asset.refresh_from_db()
        self.assertIsNone(self.asset.location_id)

    def test_location_filter_retains_employee_assigned_asset(self):
        checkout_asset(self.asset, holder=self.holder, user=self.tenant_user)
        result = AssetFilterSet({"location": self.base.pk}, queryset=Asset.objects.all())
        self.assertTrue(result.is_valid(), result.errors)
        self.assertIn(self.asset.pk, result.qs.values_list("pk", flat=True))

    def test_location_audit_expected_set_retains_employee_assigned_asset(self):
        session = baker.make(AuditSession, tenant=self.tenant, location=self.base, created_by=self.tenant_user)
        checkout_asset(self.asset, holder=self.holder, user=self.tenant_user)
        expected = expected_assets_queryset(session, user=self.tenant_user)
        self.assertEqual(set(expected.values_list("pk", flat=True)), {self.asset.pk})

    def test_all_accessible_location_audit_remains_location_and_tenant_bound(self):
        other = baker.make(Tenant)
        role = baker.make(Role, tenant=other, permissions=["compliance.view_auditsession"])
        self.grant(self.tenant_user, other, role)
        elsewhere = baker.make(Location, tenant=other)
        foreign = baker.make(Asset, tenant=other, location=elsewhere, status=self.deployable)
        session = baker.make(AuditSession, tenant=None, location=self.base, created_by=self.tenant_user)
        checkout_asset(self.asset, holder=self.holder, user=self.tenant_user)
        with _all_accessible_scope(self.tenant_user):
            expected = set(expected_assets_queryset(session, user=self.tenant_user).values_list("pk", flat=True))
        self.assertEqual(expected, {self.asset.pk})
        self.assertNotIn(foreign.pk, expected)

    def test_unknown_base_is_not_inferred_from_person_assignment(self):
        self.asset.location = None
        self.asset.save(update_fields=["location"])
        checkout_asset(self.asset, holder=self.holder, user=self.tenant_user)
        self.asset.refresh_from_db()
        self.assertIsNone(self.asset.location_id)

    def test_checkin_preserves_base_without_explicit_destination(self):
        checkout_asset(self.asset, holder=self.holder, user=self.tenant_user)
        checkin_asset(self.asset, user=self.tenant_user)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.location_id, self.base.pk)
        self.assertIsNone(self.asset.active_assignment)

    def test_checkin_explicit_destination_updates_location(self):
        destination = baker.make(Location, tenant=self.tenant)
        checkout_asset(self.asset, holder=self.holder, user=self.tenant_user)
        checkin_asset(self.asset, user=self.tenant_user, location=destination)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.location_id, destination.pk)

    def test_checkout_to_location_updates_location(self):
        destination = baker.make(Location, tenant=self.tenant)
        checkout_asset(self.asset, location=destination, user=self.tenant_user)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.location_id, destination.pk)

    def test_checkout_to_asset_uses_parent_location(self):
        destination = baker.make(Location, tenant=self.tenant)
        parent = baker.make(Asset, tenant=self.tenant, location=destination, status=self.deployable)
        checkout_asset(self.asset, asset_target=parent, user=self.tenant_user)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.location_id, destination.pk)

    def test_kit_checkout_to_location_updates_location(self):
        destination = baker.make(Location, tenant=self.tenant)
        kit = baker.make(Kit, tenant=self.tenant)
        item = baker.make(KitItem, kit=kit, asset_type=self.asset.asset_type, qty=1)
        checkout_kit(kit, location=destination, user=self.tenant_user, selected_assets={item.pk: self.asset.pk})
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.location_id, destination.pk)

    def test_person_checkout_preserves_base_location(self):
        checkout_asset(self.asset, holder=self.holder, user=self.tenant_user)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.location_id, self.base.pk)
        self.assertEqual(self.asset.active_assignment.assigned_user_id, self.holder.pk)

    def test_kit_person_checkout_preserves_base_location(self):
        kit = baker.make(Kit, tenant=self.tenant)
        item = baker.make(KitItem, kit=kit, asset_type=self.asset.asset_type, qty=1)
        checkout_kit(kit, holder=self.holder, user=self.tenant_user, selected_assets={item.pk: self.asset.pk})
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.location_id, self.base.pk)
        self.assertEqual(self.asset.active_assignment.assigned_user_id, self.holder.pk)
