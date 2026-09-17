"""#496 repair12: an uncancelled soft-deleted disposal still owns the asset's lifecycle.

The recycle bin must not turn an active disposal into "no record". The asset detail has to
show the tombstoned record with an explicit previously-deleted marker (and must not offer a
new disposal that the service would reject anyway), and an authorised cancellation has to
reach that record **without** restoring it: ``deleted_at`` and the original evidence stay.
"""

import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from assets.models import Asset, AssetDisposal, DisposalMethodChoices, StatusLabel
from assets.services import dispose_asset
from core.tests.mixins import TenantTestMixin
from organization.models import Tenant

User = get_user_model()

DISPOSAL_PERMS = [
    "assets.view_asset",
    "assets.change_asset",
    "assets.add_assetdisposal",
    "assets.view_assetdisposal",
    "assets.change_assetdisposal",
    "assets.delete_assetdisposal",
    "assets.dispose_asset",
]


class TombstonedActiveDisposalHttpTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(permissions=DISPOSAL_PERMS)
        self.set_active_tenant(self.tenant)
        self.deployable = baker.make(StatusLabel, type="deployable", name="Deployable")
        self.archived = baker.make(StatusLabel, type="archived", name="Archived")
        self.pending = baker.make(StatusLabel, type="pending", name="Pending")
        self.asset = baker.make(Asset, name="Tombstone Laptop", status=self.deployable, tenant=self.tenant)
        self.record = dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 1),
            sanitization_certificate="CERT-TOMB",
            user=self.tenant_user,
        )
        self.asset.refresh_from_db()
        # The migrated/legacy shape: soft-deleted but NOT cancelled - still the active owner.
        AssetDisposal.all_objects.filter(pk=self.record.pk).update(deleted_at=timezone.now())
        self.record.refresh_from_db()
        self.cancel_url = reverse("assets:assetdisposal_cancel", kwargs={"pk": self.record.pk})
        self.client.force_login(self.tenant_user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

    def _login_as(self, user, tenant):
        self.client.force_login(user)
        session = self.client.session
        session["active_tenant_id"] = tenant.pk
        session.save()

    def test_asset_detail_shows_the_tombstoned_active_record(self):
        response = self.client.get(self.asset.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Previously deleted", body, "the hidden state must be explicitly marked")
        self.assertNotIn("No active disposal record for this asset.", body)
        # the disposal card itself must not offer a new disposal the service would reject
        card = body.split('id="disposal"', 1)[1].split("Disposal history", 1)[0]
        self.assertNotIn(f"?asset={self.asset.pk}&_quickadd=1", card, "no new-disposal affordance")
        self.assertNotIn("No active disposal record", card)

    def test_cancel_view_reaches_the_tombstoned_record(self):
        response = self.client.get(self.cancel_url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Previously deleted", response.content.decode())

    def test_authorised_cancellation_keeps_the_tombstone_and_records_the_evidence(self):
        response = self.client.post(self.cancel_url, {"reason": "recorded in error"})

        self.assertIn(response.status_code, (200, 302))
        stored = AssetDisposal.all_objects.get(pk=self.record.pk)
        self.assertIsNotNone(stored.cancelled_at)
        self.assertEqual(stored.cancelled_by_id, self.tenant_user.pk)
        self.assertEqual(stored.cancellation_reason, "recorded in error")
        self.assertIsNotNone(stored.deleted_at, "cancelling must not restore the tombstone")
        self.assertFalse(
            AssetDisposal.objects.filter(pk=self.record.pk).exists(),
            "the record stays in the recycle bin",
        )
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "pending")
        self.assertIsNone(self.asset.disposed_at)

    def test_foreign_tenant_cannot_open_or_cancel_the_tombstone(self):
        other = Tenant.objects.create(name="Tombstone Other", slug="tombstone-other")
        role = self.tenant_role.__class__.objects.create(tenant=other, name="Other Role", permissions=DISPOSAL_PERMS)
        user = User.objects.create_user(username="tomb-other", email="tomb-other@example.com", password="password")
        self.grant(user, other, role)
        self._login_as(user, other)

        self.assertEqual(self.client.get(self.cancel_url).status_code, 404)
        self.assertEqual(self.client.post(self.cancel_url, {"reason": "nope"}).status_code, 404)
        self.assertIsNone(AssetDisposal.all_objects.get(pk=self.record.pk).cancelled_at)

    def test_same_tenant_without_permission_is_denied(self):
        role = self.tenant_role.__class__.objects.create(
            tenant=self.tenant, name="No disposal right", permissions=["assets.view_asset", "assets.change_asset"]
        )
        user = User.objects.create_user(username="tomb-weak", email="tomb-weak@example.com", password="password")
        self.grant(user, self.tenant, role)
        self._login_as(user, self.tenant)

        self.assertEqual(self.client.get(self.cancel_url).status_code, 403)
        self.assertEqual(self.client.post(self.cancel_url, {"reason": "let me"}).status_code, 403)
        self.assertIsNone(AssetDisposal.all_objects.get(pk=self.record.pk).cancelled_at)

    def test_ordinary_active_disposal_still_behaves(self):
        asset = baker.make(Asset, name="Plain Laptop", status=self.deployable, tenant=self.tenant)
        record = dispose_asset(
            asset=asset,
            disposal_method=DisposalMethodChoices.DONATION,
            disposal_date=datetime.date(2026, 6, 2),
            user=self.tenant_user,
        )

        body = self.client.get(asset.get_absolute_url()).content.decode()
        self.assertNotIn("Previously deleted", body)
        self.assertNotIn("No active disposal record for this asset.", body)
        url = reverse("assets:assetdisposal_cancel", kwargs={"pk": record.pk})
        self.assertEqual(self.client.get(url).status_code, 200)
