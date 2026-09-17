"""#496 language review item 3: a required lifecycle status must not be optional.

``dispose_asset`` needs an archived status and ``cancel_asset_disposal`` needs a pending
status. When the required label is missing the whole operation must abort atomically with
a translated validation error, before the asset, the assignment, the evidence or the audit
trail are touched - never silently continue with a misleading success message and never
create status labels automatically.
"""

import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase
from model_bakery import baker

from assets.models import Asset, AssetAssignment, AssetDisposal, DisposalMethodChoices, StatusLabel
from assets.services import cancel_asset_disposal, dispose_asset
from core.tests.mixins import TenantTestMixin

DISPOSAL_PERMS = [
    "assets.view_asset",
    "assets.change_asset",
    "assets.add_assetdisposal",
    "assets.dispose_asset",
]


class DisposalRequiredStatusTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(permissions=DISPOSAL_PERMS)
        self.set_active_tenant(self.tenant)
        self.deployable = baker.make(StatusLabel, type=StatusLabel.TYPE_DEPLOYABLE, name="Deployable")
        self.archived = baker.make(StatusLabel, type=StatusLabel.TYPE_ARCHIVED, name="Archived")
        self.pending = baker.make(StatusLabel, type=StatusLabel.TYPE_PENDING, name="Pending")
        self.holder = baker.make("organization.AssetHolder", tenant=self.tenant)
        self.asset = baker.make(Asset, name="Status laptop", status=self.deployable, tenant=self.tenant)

    def _remove_label(self, label_type):
        StatusLabel.all_objects.filter(type=label_type).delete()

    def _dispose(self, **kwargs):
        return dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 1),
            user=self.tenant_user,
            **kwargs,
        )

    def _assignment(self):
        return baker.make(AssetAssignment, asset=self.asset, assigned_user=self.holder)

    def test_missing_archived_label_aborts_before_any_mutation(self):
        assignment = self._assignment()
        self._remove_label(StatusLabel.TYPE_ARCHIVED)

        with self.assertRaises(ValidationError) as rejected:
            self._dispose()

        self.assertIn("archived", str(rejected.exception).lower())
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status_id, self.deployable.pk)
        self.assertIsNone(self.asset.disposed_at)
        self.assertIsNone(self.asset.disposal_value)
        self.assertFalse(AssetDisposal.all_objects.filter(asset_id=self.asset.pk).exists())
        assignment.refresh_from_db()
        self.assertTrue(assignment.is_active, "the auto check-in must not run before the guard")

    def test_missing_pending_label_aborts_before_any_mutation(self):
        assignment = self._assignment()
        disposal = self._dispose()
        self.assertIsNotNone(disposal.pk)
        AssetAssignment._base_manager.filter(pk=assignment.pk).update(is_active=False)
        self.asset.refresh_from_db()
        frozen_at, frozen_value = self.asset.disposed_at, self.asset.disposal_value
        self._remove_label(StatusLabel.TYPE_PENDING)

        with self.assertRaises(ValidationError) as rejected:
            cancel_asset_disposal(disposal, user=self.tenant_user, reason="recorded in error")

        self.assertIn("pending", str(rejected.exception).lower())
        disposal.refresh_from_db()
        self.asset.refresh_from_db()
        self.assertIsNone(disposal.cancelled_at, "the record must stay active")
        self.assertIsNone(disposal.cancelled_by_id)
        self.assertEqual(disposal.cancellation_reason, "")
        self.assertEqual(self.asset.status.type, "archived")
        self.assertEqual((self.asset.disposed_at, self.asset.disposal_value), (frozen_at, frozen_value))

    def test_present_labels_still_drive_both_transitions(self):
        assignment = self._assignment()

        disposal = self._dispose()

        self.asset.refresh_from_db()
        assignment.refresh_from_db()
        self.assertEqual(self.asset.status.type, StatusLabel.TYPE_ARCHIVED)
        self.assertIsNotNone(self.asset.disposed_at)
        self.assertFalse(assignment.is_active)

        cancel_asset_disposal(disposal, user=self.tenant_user, reason="recorded in error")

        disposal.refresh_from_db()
        self.asset.refresh_from_db()
        self.assertIsNotNone(disposal.cancelled_at)
        self.assertEqual(self.asset.status.type, StatusLabel.TYPE_PENDING)
        self.assertIsNone(self.asset.disposed_at)
        self.assertIsNone(self.asset.disposal_value)

    def test_no_status_labels_are_created_automatically(self):
        self._remove_label(StatusLabel.TYPE_ARCHIVED)
        before = StatusLabel.all_objects.filter(type=StatusLabel.TYPE_ARCHIVED).count()

        with self.assertRaises(ValidationError):
            self._dispose()

        self.assertEqual(StatusLabel.all_objects.filter(type=StatusLabel.TYPE_ARCHIVED).count(), before)
