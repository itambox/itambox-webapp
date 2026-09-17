"""#496 repair14: one lifecycle meaning across every surface.

An uncancelled record - including a soft-deleted tombstone - still owns the asset's disposal.
These tests pin the cross-surface consequences: the model property, the financial panel, the
disposal list/detail links, the report cards/chart and the admin error path must all agree,
stay tenant-scoped and never present a tombstone as a plain archival freeze.
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker

from assets.models import Asset, AssetDisposal, DisposalMethodChoices, StatusLabel
from assets.services import dispose_asset
from core.models import ObjectChange
from core.reports import build_report_context
from core.tests.mixins import TenantTestMixin
from extras.models import ReportTemplate
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


class TombstoneCrossSurfaceTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(permissions=DISPOSAL_PERMS)
        self.set_active_tenant(self.tenant)
        self.deployable = baker.make(StatusLabel, type="deployable", name="Deployable")
        self.archived = baker.make(StatusLabel, type="archived", name="Archived")
        self.pending = baker.make(StatusLabel, type="pending", name="Pending")
        self.asset = baker.make(
            Asset, name="Cross Laptop", asset_tag="CROSS-1", status=self.deployable, tenant=self.tenant
        )
        self.record = dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 1),
            sanitization_certificate="CERT-CROSS",
            proceeds=Decimal("42.50"),
            currency="EUR",
            user=self.tenant_user,
        )
        self.asset.refresh_from_db()
        AssetDisposal.all_objects.filter(pk=self.record.pk).update(deleted_at=timezone.now())
        self.record.refresh_from_db()
        self.client.force_login(self.tenant_user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

    def _login_as(self, user, tenant):
        self.client.force_login(user)
        session = self.client.session
        session["active_tenant_id"] = tenant.pk
        session.save()

    def test_active_disposal_property_agrees_with_the_canonical_definition(self):
        plain = Asset.all_objects.get(pk=self.asset.pk)
        self.assertIsNotNone(plain.active_disposal, "a tombstoned active record still owns the asset")
        self.assertEqual(plain.active_disposal.pk, self.record.pk)
        self.assertTrue(plain.is_disposed)

        prefetched = Asset.all_objects.get(pk=self.asset.pk)
        prefetched.prefetched_active_disposals = [self.record]
        self.assertEqual(prefetched.active_disposal.pk, self.record.pk)

    def test_financial_panel_shows_the_disposal_value_for_an_active_tombstone(self):
        response = self.client.get(self.asset.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Disposal value:", body)
        self.assertNotIn("Archival book value:", body)
        self.assertNotIn("no disposal recorded", body)

    def test_disposal_detail_link_resolves_for_a_tombstone(self):
        response = self.client.get(self.record.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertIn("Previously deleted", response.content.decode())

    def test_foreign_tenant_cannot_open_the_tombstone_detail(self):
        other = Tenant.objects.create(name="Cross Other", slug="cross-other")
        role = self.tenant_role.__class__.objects.create(tenant=other, name="Other Role", permissions=DISPOSAL_PERMS)
        user = User.objects.create_user(username="cross-other", email="cross-other@example.com", password="password")
        self.grant(user, other, role)
        self._login_as(user, other)

        self.assertEqual(self.client.get(self.record.get_absolute_url()).status_code, 404)

    def test_disposal_list_shows_tombstones_and_stays_tenant_scoped(self):
        response = self.client.get(reverse("assets:assetdisposal_list"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("CROSS-1", response.content.decode())

        other = Tenant.objects.create(name="Cross Other Two", slug="cross-other-two")
        other_asset = baker.make(Asset, name="Foreign Laptop", asset_tag="FOREIGN-9", tenant=other)
        with self.tenant_context(other):
            other_record = dispose_asset(
                asset=other_asset,
                disposal_method=DisposalMethodChoices.RECYCLE,
                disposal_date=datetime.date(2026, 6, 2),
                user=self.tenant_user,
            )
        AssetDisposal.all_objects.filter(pk=other_record.pk).update(deleted_at=timezone.now())

        body = self.client.get(reverse("assets:assetdisposal_list")).content.decode()
        self.assertNotIn("FOREIGN-9", body)

    def _report(self, asset_tag):
        template = baker.make(
            ReportTemplate,
            report_type="asset_disposal_eol",
            include_summary_cards=True,
            include_distribution_chart=True,
            tenant=self.tenant,
        )
        _headers, rows, cards, _grouped, chart_svg, _context = build_report_context(template, active_tenant=self.tenant)
        return rows, {card["label"]: card["value"] for card in cards}, chart_svg

    def test_report_treats_an_active_tombstone_as_active(self):
        rows, cards, chart_svg = self._report("CROSS-1")

        self.assertEqual(cards["Active Disposals"], "1")
        self.assertIn("42", cards["Proceeds from active disposals"])
        self.assertIn(str(DisposalMethodChoices.RECYCLE.label), chart_svg)
        self.assertTrue(any("CROSS-1" in (row.get("Asset") or "") for row in rows))

    def test_report_keeps_a_cancelled_tombstone_as_history_only(self):
        from assets.services import cancel_asset_disposal

        cancel_asset_disposal(self.record, user=self.tenant_user, reason="recorded in error")
        self.record.refresh_from_db()

        rows, cards, chart_svg = self._report("CROSS-1")

        self.assertEqual(cards["Active Disposals"], "0")
        self.assertNotIn("42", cards["Proceeds from active disposals"])
        self.assertNotIn(str(DisposalMethodChoices.RECYCLE.label), chart_svg)
        self.assertTrue(any("CROSS-1" in (row.get("Asset") or "") for row in rows), "history stays visible")
        self.assertIsNotNone(self.record.cancelled_at)

    def test_foreign_tombstone_is_not_in_the_report(self):
        other = Tenant.objects.create(name="Cross Report Other", slug="cross-report-other")
        other_asset = baker.make(Asset, name="Foreign Report Laptop", asset_tag="FOREIGN-RPT", tenant=other)
        with self.tenant_context(other):
            other_record = dispose_asset(
                asset=other_asset,
                disposal_method=DisposalMethodChoices.RECYCLE,
                disposal_date=datetime.date(2026, 6, 3),
                proceeds=Decimal("7.00"),
                currency="EUR",
                user=self.tenant_user,
            )
        AssetDisposal.all_objects.filter(pk=other_record.pk).update(deleted_at=timezone.now())

        rows, cards, _chart = self._report("CROSS-1")

        self.assertFalse(any("FOREIGN-RPT" in (row.get("Asset") or "") for row in rows))
        self.assertNotIn("7", cards["Proceeds from active disposals"].replace("42", ""))


class AssetDisposalAdminRejectionTests(TenantTestMixin, TestCase):
    """A service rejection in the admin must be a bound form error, never a success."""

    def setUp(self):
        self.setup_tenant_context(permissions=DISPOSAL_PERMS)
        self.set_active_tenant(self.tenant)
        self.deployable = baker.make(StatusLabel, type="deployable", name="Deployable")
        self.archived = baker.make(StatusLabel, type="archived", name="Archived")
        self.pending = baker.make(StatusLabel, type="pending", name="Pending")
        self.admin_user = User.objects.create_superuser(
            username="repair14-admin", email="repair14-admin@example.com", password="password"
        )
        self.asset = baker.make(
            Asset, name="Admin Laptop", asset_tag="ADMIN-1", status=self.deployable, tenant=self.tenant, currency="EUR"
        )
        self.client.force_login(self.admin_user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

    def _payload(self, asset, **overrides):
        payload = {
            "asset": str(asset.pk),
            "disposal_method": DisposalMethodChoices.RECYCLE,
            "disposal_date": "2026-06-01",
            "data_sanitization_method": "none",
            "sanitization_certificate": "CERT-ADMIN",
            "sanitized_by": "ops",
            "recipient": "",
            "proceeds": "10.00",
            "currency": "EUR",
            "notes": "",
            "_save": "Save",
        }
        payload.update(overrides)
        return payload

    def test_rejected_create_shows_a_form_error_and_writes_nothing(self):
        existing = dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 1),
            user=self.tenant_user,
        )
        before = AssetDisposal.all_objects.count()
        changes_before = ObjectChange.objects.count()

        response = self.client.post(reverse("admin:assets_assetdisposal_add"), data=self._payload(self.asset))

        self.assertEqual(response.status_code, 200, "a rejected save must re-render the form, not 500/redirect")
        body = response.content.decode()
        self.assertIn("already has an active disposal", body)
        self.assertNotIn("was added successfully", body)
        self.assertEqual(AssetDisposal.all_objects.count(), before)
        self.assertEqual(ObjectChange.objects.count(), changes_before)
        self.assertIsNotNone(existing.pk)

    def test_rejected_update_shows_a_form_error_and_keeps_stored_state(self):
        """A write-TIME service rejection (currency mismatch) must not become a success."""
        record = dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 1),
            proceeds=Decimal("11.00"),
            currency="EUR",
            user=self.tenant_user,
        )
        before = AssetDisposal.all_objects.get(pk=record.pk)
        changes_before = ObjectChange.objects.count()

        response = self.client.post(
            reverse("admin:assets_assetdisposal_change", args=[record.pk]),
            data=self._payload(self.asset, proceeds="10.00", currency="USD"),
        )

        self.assertEqual(response.status_code, 200, "a rejected update must re-render the form, not 500/redirect")
        body = response.content.decode()
        self.assertIn("must match the asset", body)
        self.assertNotIn("was changed successfully", body)
        after = AssetDisposal.all_objects.get(pk=record.pk)
        self.assertEqual(after.proceeds, before.proceeds)
        self.assertEqual(after.currency, before.currency)
        self.assertEqual(ObjectChange.objects.count(), changes_before)

    def test_form_level_rejection_also_stays_a_bound_form_error(self):
        record = dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 1),
            proceeds=Decimal("11.00"),
            currency="EUR",
            user=self.tenant_user,
        )

        response = self.client.post(
            reverse("admin:assets_assetdisposal_change", args=[record.pk]),
            data=self._payload(self.asset, proceeds="-5.00"),
        )

        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("errorlist", body)
        self.assertNotIn("was changed successfully", body)
        self.assertEqual(AssetDisposal.all_objects.get(pk=record.pk).proceeds, Decimal("11.00"))

    def test_valid_create_still_succeeds(self):
        response = self.client.post(reverse("admin:assets_assetdisposal_add"), data=self._payload(self.asset))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            AssetDisposal.all_objects.filter(asset=self.asset, cancelled_at__isnull=True).exists(),
            "the valid admin create must persist through the service",
        )
