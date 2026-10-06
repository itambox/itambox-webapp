"""Phase 3 (E1) — AssetStateMachine enforcement on the SAVE path.

These tests exercise state-machine enforcement on the save path. Transition
validation lives in Asset.clean(), which the global
`validate_custom_validators_on_save` pre_save signal (core/signals.py) runs on
every ChangeLoggingMixin save — so a plain Asset.save() is enough to trigger it
(no separate validation block in save() is required). They also confirm the
existing service flows (checkout/checkin/dispose) still pass through the state
machine cleanly.

Note: bulk QuerySet.update() bypasses both clean() and the pre_save signal, so
mass status flips are NOT validated — a pre-existing gap, not covered here.

Run with:
    pytest assets/tests/test_asset_state_transitions.py
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from model_bakery import baker

from assets.choices import StatusTypeChoices
from assets.models import Asset, AssetAssignment, DisposalMethodChoices, StatusLabel
from assets.services import checkin_asset, checkout_asset, dispose_asset
from core.tests.mixins import TenantTestMixin

User = get_user_model()


class AssetStateMachineSavePathTest(TenantTestMixin, TestCase):
    """Save-level state-machine enforcement and service-flow integration."""

    def setUp(self):
        self.setup_tenant_context()
        self.set_active_tenant(self.tenant, self.tenant_membership)
        self.user = baker.make(User, is_superuser=True, is_staff=True)
        # Unique status-label names per type (suffix '-sm3') to avoid colliding
        # with sibling tests in the order-dependent full run.
        self.pending = baker.make(StatusLabel, type="pending", name="Pending-sm3")
        self.deployable = baker.make(StatusLabel, type="deployable", name="Deployable-sm3")
        self.deployed = baker.make(StatusLabel, type="deployed", name="Deployed-sm3")
        self.archived = baker.make(StatusLabel, type="archived", name="Archived-sm3")
        self.asset = baker.make(
            Asset,
            name="State Machine Laptop",
            asset_tag="SM3-0001",
            status=self.pending,
            tenant=self.tenant,
        )
        self.holder = baker.make("organization.AssetHolder", tenant=self.tenant)

    def test_legal_transition_via_save_succeeds(self):
        """pending -> deployable is allowed and must save without raising."""
        self.asset.status = self.deployable
        self.asset.save()  # must not raise
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "deployable")

    def test_illegal_transition_via_save_raises(self):
        """archived -> deployable is illegal and must raise on save()."""
        # pending -> archived is legal; perform it first.
        self.asset.status = self.archived
        self.asset.save()
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "archived")

        # archived only allows -> pending, so -> deployable must raise.
        self.asset.status = self.deployable
        with self.assertRaises(ValidationError) as ctx:
            self.asset.save()
        self.assertIn("Illegal state transition", str(ctx.exception))

    def test_update_fields_does_not_bypass_status_transition_validation(self):
        """Field-limited saves still execute non-custom model validation."""
        self.asset.status = self.archived
        self.asset.save()
        self.asset.refresh_from_db()

        self.asset.status = self.deployable
        with self.assertRaises(ValidationError) as ctx:
            self.asset.save(update_fields=["status"])
        self.assertIn("Illegal state transition", str(ctx.exception))

    def test_checked_out_asset_cannot_be_archived_via_save(self):
        """An asset with an active assignment must not be archived on save()."""
        from organization.models import AssetHolder

        holder = baker.make(AssetHolder, tenant=self.tenant)
        # Reach a checked-out 'deployed' state via the LEGAL path
        # (pending -> deployable -> deployed through checkout); a direct
        # pending -> deployed save is itself an illegal transition.
        self.asset.status = self.deployable
        self.asset.save()
        checkout_asset(asset=self.asset, holder=holder, user=self.user)
        self.asset.refresh_from_db()
        self.assertTrue(self.asset.assignments.filter(is_active=True).exists())

        self.asset.status = self.archived
        with self.assertRaises(ValidationError) as ctx:
            self.asset.save()
        self.assertIn("active assignment requires", str(ctx.exception))

    def test_checkout_archived_asset_is_blocked(self):
        """checkout_asset must reject an archived asset with a clear message."""
        from organization.models import AssetHolder

        holder = baker.make(AssetHolder, tenant=self.tenant)
        self.asset.status = self.archived
        self.asset.save()

        with self.assertRaises(ValidationError) as ctx:
            checkout_asset(asset=self.asset, holder=holder, user=self.user)
        self.assertIn("Cannot check out", str(ctx.exception))

    def test_service_flows_still_pass(self):
        """checkout -> checkin -> dispose must all complete without raising."""
        from organization.models import AssetHolder

        holder = baker.make(AssetHolder, tenant=self.tenant)

        # Start deployable so checkout (deployable -> deployed) is legal.
        self.asset.status = self.deployable
        self.asset.save()

        # checkout (deployable -> deployed)
        checkout_asset(asset=self.asset, holder=holder, user=self.user)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "deployed")

        # checkin (deployed -> reverts to pre-checkout deployable)
        checkin_asset(asset=self.asset, user=self.user)
        self.asset.refresh_from_db()
        self.assertFalse(self.asset.assignments.filter(is_active=True).exists())

        # dispose (-> archived)
        dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date="2026-06-16",
            user=self.user,
        )
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "archived")

    def test_issuability_is_shared_by_candidate_forms_and_checkout(self):
        for status_type in StatusTypeChoices.values:
            with self.subTest(status_type=status_type):
                self._assert_issuability_contract(status_type)

    def _assert_issuability_contract(self, status_type):
        from types import SimpleNamespace

        from assets.forms.bulk_scan_forms import AssetBulkCheckOutForm
        from assets.forms.checkout_forms import AssetCheckOutForm
        from assets.forms.request_forms import AssetRequestActionForm, AssetRequestForm
        from assets.models import AssetType, Manufacturer
        from inventory.forms import KitCheckoutForm
        from inventory.models import Kit, KitItem
        from licenses.forms import LicenseCheckOutForm
        from licenses.models import License
        from subscriptions.forms import SubscriptionCheckoutForm
        from subscriptions.models import Subscription

        manufacturer = baker.make(Manufacturer)
        asset_type = baker.make(AssetType, manufacturer=manufacturer, requestable=True)
        kit = Kit.objects.create(name=f"Issuability kit {status_type}", tenant=self.tenant)
        kit_item = KitItem.objects.create(kit=kit, asset_type=asset_type, qty=1)
        license_obj = baker.make(License, tenant=self.tenant)
        subscription = baker.make(Subscription, tenant=self.tenant)

        if status_type == StatusTypeChoices.DEPLOYED:
            candidate = baker.make(Asset, status=self.deployable, asset_type=asset_type, tenant=self.tenant)
            checkout_asset(candidate, holder=self.holder, user=self.user)
            candidate.refresh_from_db()
        else:
            status = StatusLabel.objects.create(
                name=f"Issuability {status_type}", slug=f"issuability-{status_type}", type=status_type
            )
            candidate = baker.make(
                Asset,
                status=status,
                asset_type=asset_type,
                requestable=True,
                tenant=self.tenant,
            )

        expected = status_type == StatusTypeChoices.DEPLOYABLE
        self.assertEqual(candidate.is_issuable, expected)
        self.assertEqual(Asset.issuable().filter(pk=candidate.pk).exists(), expected)

        request_instance = SimpleNamespace(asset_type=asset_type, qty=1)
        forms_and_fields = (
            (AssetCheckOutForm(asset=self.asset), "asset_target"),
            (AssetBulkCheckOutForm(), "asset_target"),
            (AssetRequestForm(), "asset"),
            (AssetRequestActionForm(request_instance=request_instance), "allocated_asset"),
            (LicenseCheckOutForm(license=license_obj), "asset"),
            (SubscriptionCheckoutForm(subscription=subscription), "asset"),
            (KitCheckoutForm(kit=kit), f"asset_{kit_item.pk}"),
        )
        for form, field_name in forms_and_fields:
            with self.subTest(form=type(form).__name__, status_type=status_type):
                self.assertEqual(form.fields[field_name].queryset.filter(pk=candidate.pk).exists(), expected)

        if expected:
            checkout_asset(candidate, holder=self.holder, user=self.user)
            candidate.refresh_from_db()
            self.assertEqual(candidate.status.type, StatusTypeChoices.DEPLOYED)
            self.assertIsNotNone(candidate.active_assignment)
        else:
            original_status_id = candidate.status_id
            with self.assertRaises(ValidationError):
                checkout_asset(candidate, holder=self.holder, user=self.user)
            candidate.refresh_from_db()
            self.assertEqual(candidate.status_id, original_status_id)
            if status_type == StatusTypeChoices.DEPLOYED:
                self.assertIsNotNone(candidate.active_assignment)
            else:
                self.assertIsNone(candidate.active_assignment)

    def test_active_assignment_requires_deployed_asset(self):
        self.asset.status = self.deployable
        self.asset.save()

        with self.assertRaises(ValidationError):
            AssetAssignment.objects.create(asset=self.asset, assigned_user=self.holder)
        self.assertFalse(AssetAssignment.objects.filter(asset=self.asset, is_active=True).exists())

    def test_manual_create_or_edit_cannot_set_or_clear_deployed_status(self):
        self.asset.status = self.deployable
        self.asset.save()

        self.asset.status = self.deployed
        with self.assertRaises(ValidationError):
            self.asset.save(update_fields=["status"])
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, self.deployable)

        checkout_asset(self.asset, holder=self.holder, user=self.user)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, self.deployed)
        self.assertIsNotNone(self.asset.active_assignment)

        self.asset.status = self.deployable
        with self.assertRaises(ValidationError):
            self.asset.save(update_fields=["status"])
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status, self.deployed)
        self.assertIsNotNone(self.asset.active_assignment)

        with self.assertRaises(ValidationError):
            baker.make(Asset, status=self.deployed, tenant=self.tenant)
