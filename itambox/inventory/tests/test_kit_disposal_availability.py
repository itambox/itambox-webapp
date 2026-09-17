"""#496 review repair: kit availability must not drop undisposed hardware.

Regression for `inventory.services._asset_pools`, which filtered with
``.exclude(disposals__cancelled_at__isnull=True)``. Over a nullable relation that
renders as a LEFT OUTER JOIN inside a NOT EXISTS subquery, so the joined NULL row made
``cancelled_at IS NULL`` true for devices that have **no** disposal record at all - every
undisposed device was reported unavailable.

Every negative expectation here carries an explicit owner-tenant positive control, so a
failure can never pass because the candidate set was empty. Counts are asserted, not
emptiness. Mixed cancelled+active history is covered on purpose: the helper's correctness
must not be assumed for multi-row history.
"""

import datetime

from django.test import TestCase
from django.utils import timezone
from model_bakery import baker

from assets.models import Asset, AssetDisposal, AssetType, DisposalMethodChoices, Manufacturer, StatusLabel
from assets.services import cancel_asset_disposal, dispose_asset
from core.tests.mixins import TenantTestMixin
from inventory.models import Kit, KitItem
from inventory.services import kit_availability

DISPOSAL_PERMS = [
    "assets.view_asset",
    "assets.change_asset",
    "assets.add_assetdisposal",
    "assets.dispose_asset",
]


class KitDisposalAvailabilityTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(permissions=DISPOSAL_PERMS)
        self.set_active_tenant(self.tenant)
        self.deployable = baker.make(StatusLabel, type="deployable", name="Deployable")
        self.archived = baker.make(StatusLabel, type="archived", name="Archived")
        baker.make(StatusLabel, type=StatusLabel.TYPE_PENDING, name="Pending")
        manufacturer = baker.make(Manufacturer, name=f"Kit maker {self.tenant.pk}")
        self.asset_type = baker.make(AssetType, manufacturer=manufacturer, model="Kit laptop")
        self.kit = baker.make(Kit, name="Repair kit", tenant=self.tenant)
        self.item = baker.make(KitItem, kit=self.kit, asset_type=self.asset_type)
        self.disposal_date = datetime.date(2026, 6, 1)

    def _device(self, tag):
        return baker.make(
            Asset, name=tag, asset_tag=tag, status=self.deployable, asset_type=self.asset_type, tenant=self.tenant
        )

    def _count(self, items=None):
        """Availability of the hardware rows, plus the kit state."""
        rows, state = kit_availability(items or [self.item], self.tenant)
        return [row["available_count"] for row in rows], state

    def _second_row(self):
        """A second hardware requirement: two rows demand two distinct devices."""
        return baker.make(KitItem, kit=self.kit, asset_type=self.asset_type)

    def _dispose(self, asset):
        return dispose_asset(
            asset=asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=self.disposal_date,
            user=self.tenant_user,
        )

    def test_undisposed_deployable_hardware_stays_available(self):
        """Root cause of the review finding: a device with no disposal row is available."""
        self._device("KD-UNDISPOSED-A")
        self._device("KD-UNDISPOSED-B")

        counts, state = self._count()

        self.assertEqual(counts, [2])
        self.assertEqual(state, "available")

    def test_active_disposal_is_excluded_even_after_a_forced_status_drift(self):
        second = self._second_row()
        self._device("KD-ACTIVE-CONTROL")
        disposed = self._device("KD-ACTIVE-DISPOSED")
        self._dispose(disposed)
        Asset._base_manager.filter(pk=disposed.pk).update(status=self.deployable)

        counts, state = self._count([self.item, second])

        # Two rows demand two devices. Only the undisposed control device may count, so
        # each row reports 1 and the kit is short. A wrongly counted disposed device
        # would report 2 and an available kit.
        self.assertEqual(counts, [1, 1], "the undisposed control device must stay available")
        self.assertNotEqual(state, "available", "the disposed device must not satisfy the demand")

    def test_cancelled_history_is_eligible_again_after_a_deliberate_status_restoration(self):
        self._device("KD-CANCELLED-CONTROL")
        recycled = self._device("KD-CANCELLED-ONLY")
        disposal = self._dispose(recycled)
        cancel_asset_disposal(disposal, user=self.tenant_user, reason="recorded in error")
        Asset._base_manager.filter(pk=recycled.pk).update(status=self.deployable, disposed_at=None, requestable=True)

        counts, state = self._count()

        self.assertEqual(counts, [2], "cancelled-only history must be eligible again after a deliberate restore")
        self.assertEqual(state, "available")

    def test_cancelled_plus_active_history_is_excluded(self):
        """Multi-row history: an uncancelled record must win over a cancelled one."""
        second = self._second_row()
        self._device("KD-MIXED-CONTROL")
        mixed = self._device("KD-MIXED-DISPOSED")
        first = self._dispose(mixed)
        cancel_asset_disposal(first, user=self.tenant_user, reason="recorded in error")
        Asset._base_manager.filter(pk=mixed.pk).update(status=self.deployable)
        self._dispose(mixed)
        Asset._base_manager.filter(pk=mixed.pk).update(status=self.deployable)
        self.assertEqual(AssetDisposal.all_objects.filter(asset_id=mixed.pk).count(), 2)
        self.assertEqual(AssetDisposal.all_objects.filter(asset_id=mixed.pk, cancelled_at__isnull=True).count(), 1)

        counts, state = self._count([self.item, second])

        self.assertEqual(counts, [1, 1], "the undisposed control device must stay available")
        self.assertNotEqual(state, "available", "cancelled history must not release an actively disposed device")

    def test_soft_deleted_active_tombstone_is_excluded(self):
        second = self._second_row()
        self._device("KD-TOMBSTONE-CONTROL")
        hidden = self._device("KD-TOMBSTONE")
        self._dispose(hidden)
        AssetDisposal.all_objects.filter(asset_id=hidden.pk).update(deleted_at=timezone.now())
        Asset._base_manager.filter(pk=hidden.pk).update(status=self.deployable)

        counts, state = self._count([self.item, second])

        self.assertEqual(counts, [1, 1], "the undisposed control device must stay available")
        self.assertNotEqual(state, "available", "hidden evidence must not release the device")
