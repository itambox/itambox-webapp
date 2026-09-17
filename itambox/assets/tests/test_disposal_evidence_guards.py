"""#496: disposal evidence immutability, and all-or-nothing disposal/cancellation.

These tests inject a failure *after* the lifecycle write has happened, so a partial
state (record written, asset not moved / record cancelled, asset still frozen) is
detectable. The positive path is covered by test_disposal.py and
test_disposal_cancellation.py.
"""

import datetime
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from model_bakery import baker

from assets.models import Asset, AssetDisposal, DisposalMethodChoices, StatusLabel
from assets.services import cancel_asset_disposal, dispose_asset
from core.tests.mixins import TenantTestMixin

DISPOSAL_PERMS = [
    "assets.view_asset",
    "assets.change_asset",
    "assets.view_assetdisposal",
    "assets.add_assetdisposal",
    "assets.dispose_asset",
]


class DisposalEvidenceGuardTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(permissions=DISPOSAL_PERMS)
        self.set_active_tenant(self.tenant)
        self.deployable = baker.make(StatusLabel, type="deployable", name="Deployable")
        self.asset = baker.make(Asset, name="Rollback Laptop", status=self.deployable, tenant=self.tenant)

    def _state(self):
        self.asset.refresh_from_db()
        return (
            self.asset.status_id,
            self.asset.disposed_at,
            AssetDisposal.all_objects.filter(asset_id=self.asset.pk).count(),
        )

    def _dispose(self):
        return dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 4),
            user=self.tenant_user,
        )

    def test_dispose_leaves_no_partial_state_when_a_later_write_fails(self):
        """A failure after the record write must roll back record and asset together."""
        before = self._state()
        original_save = Asset.save

        def exploding_save(instance, *args, **kwargs):
            if AssetDisposal.all_objects.filter(asset_id=instance.pk).exists():
                raise RuntimeError("injected failure after the disposal record was written")
            return original_save(instance, *args, **kwargs)

        with mock.patch.object(Asset, "save", exploding_save):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    self._dispose()

        self.assertEqual(self._state(), before)
        self.assertFalse(AssetDisposal.all_objects.filter(asset_id=self.asset.pk).exists())

    def test_cancel_leaves_no_partial_state_when_a_later_write_fails(self):
        """A failure after the cancellation write must leave the record active."""
        disposal = self._dispose()
        self.asset.refresh_from_db()
        before = (self.asset.status_id, self.asset.disposed_at, disposal.cancellation_reason)
        original_save = Asset.save

        def exploding_save(instance, *args, **kwargs):
            if AssetDisposal.all_objects.filter(asset_id=instance.pk, cancelled_at__isnull=False).exists():
                raise RuntimeError("injected failure after the cancellation was written")
            return original_save(instance, *args, **kwargs)

        with mock.patch.object(Asset, "save", exploding_save):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    cancel_asset_disposal(disposal, user=self.tenant_user, reason="recorded in error")

        disposal.refresh_from_db()
        self.asset.refresh_from_db()
        self.assertIsNone(disposal.cancelled_at)
        self.assertIsNone(disposal.cancelled_by_id)
        self.assertEqual(disposal.cancellation_reason, before[2])
        self.assertEqual((self.asset.status_id, self.asset.disposed_at), (before[0], before[1]))
        self.assertTrue(self.asset.is_disposed)

    def test_evidence_delete_is_refused(self):
        disposal = self._dispose()
        with self.assertRaises(ValidationError):
            disposal.delete()
        stored = AssetDisposal.all_objects.get(pk=disposal.pk)
        self.assertIsNone(stored.deleted_at)
        self.assertIsNone(stored.cancelled_at)

    def test_cancelled_record_cannot_be_restored(self):
        disposal = self._dispose()
        cancel_asset_disposal(disposal, user=self.tenant_user, reason="recorded in error")
        disposal.refresh_from_db()
        with self.assertRaises(ValidationError):
            disposal.restore()
        stored = AssetDisposal.all_objects.get(pk=disposal.pk)
        self.assertIsNotNone(stored.cancelled_at)
        self.assertIsNone(stored.deleted_at)
