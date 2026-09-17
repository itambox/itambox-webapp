"""Cancellation of an erroneous disposal, and the guards around disposal state (#496).

Decided product semantics exercised here:

* archiving is "out of operation" and freezes a book value — it is NOT a disposal;
* a disposal record, the asset state/stamps and the auto check-in are ONE atomic
  operation, on every entry point;
* a second disposal while an ACTIVE record exists is rejected, never replaced;
* correcting a mistaken disposal is an explicit cancellation that persists actor,
  time and reason and keeps the record visible as history;
* the asset returns to ``pending`` (never auto-deployable), and no prior
  assignment or requestability is restored.
"""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from model_bakery import baker
from rest_framework.test import APITestCase

from assets.admin import AssetDisposalAdmin
from assets.models import (
    Asset,
    AssetAssignment,
    AssetDisposal,
    AssetRequest,
    DisposalMethodChoices,
    StatusLabel,
)
from assets.services import cancel_asset_disposal, checkout_asset, dispose_asset
from assets.views.bulk_scan_views import asset_action_payload
from core.models import ObjectChange
from core.tests.mixins import TenantTestMixin
from organization.models import AssetHolder, Tenant
from users.models import Token

User = get_user_model()

DISPOSAL_PERMS = [
    "assets.view_asset",
    "assets.change_asset",
    # The create/edit form and the dedicated dispose action are `add/change`
    # operations on AssetDisposal; only the CANCELLATION reuses the existing
    # `assets.dispose_asset` authority (approved design).
    "assets.add_assetdisposal",
    "assets.view_assetdisposal",
    "assets.change_assetdisposal",
    "assets.delete_assetdisposal",
    "assets.dispose_asset",
]


class DisposalCancellationTests(TenantTestMixin, TestCase):
    """Service-level contract of ``cancel_asset_disposal`` and the disposal guards."""

    def setUp(self):
        self.setup_tenant_context(permissions=DISPOSAL_PERMS)
        self.set_active_tenant(self.tenant)
        self.deployable = baker.make(StatusLabel, type="deployable", name="Deployable")
        self.deployed = baker.make(StatusLabel, type="deployed", name="Deployed")
        self.archived = baker.make(StatusLabel, type="archived", name="Archived")
        self.pending = baker.make(StatusLabel, type="pending", name="Pending")
        self.asset = baker.make(Asset, name="Cancel Laptop", status=self.deployable, tenant=self.tenant)
        self.disposal = dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 1),
            sanitization_certificate="CERT-1",
            user=self.tenant_user,
        )

    def _refresh(self):
        self.asset.refresh_from_db()
        self.disposal.refresh_from_db()

    def test_cancel_records_actor_time_and_reason(self):
        cancel_asset_disposal(self.disposal, user=self.tenant_user, reason="  recorded on the wrong asset  ")
        self._refresh()
        self.assertIsNotNone(self.disposal.cancelled_at)
        self.assertEqual(self.disposal.cancelled_by, self.tenant_user)
        self.assertEqual(self.disposal.cancellation_reason, "recorded on the wrong asset")
        self.assertFalse(self.disposal.is_active)
        self.assertTrue(self.disposal.is_cancelled)

    def test_cancel_returns_asset_to_pending_and_clears_the_freeze(self):
        cancel_asset_disposal(self.disposal, user=self.tenant_user, reason="wrong asset")
        self._refresh()
        # Intended canonical resolution: the FIRST pending-type label, exactly like
        # the existing checkin revert (`StatusLabel.objects.filter(type=...).first()`).
        # Asserting the fixture pk would impose an arbitrary id on that contract.
        self.assertEqual(self.asset.status.type, "pending")
        self.assertIsNotNone(self.pending.pk)
        self.assertIsNone(self.asset.disposed_at)
        self.assertIsNone(self.asset.disposal_value)
        self.assertIsNone(self.asset.active_disposal)
        # Never auto-deployed by the cancellation itself.
        self.assertFalse(AssetAssignment.objects.filter(asset=self.asset, is_active=True).exists())

    def test_cancel_keeps_the_record_and_its_evidence(self):
        pk = self.disposal.pk
        cancel_asset_disposal(self.disposal, user=self.tenant_user, reason="wrong asset")
        stored = AssetDisposal.all_objects.get(pk=pk)
        self.assertEqual(stored.disposal_method, DisposalMethodChoices.RECYCLE)
        self.assertEqual(stored.disposal_date, datetime.date(2026, 6, 1))
        self.assertEqual(stored.sanitization_certificate, "CERT-1")
        self.assertIsNone(stored.deleted_at)
        # Visible to the ordinary manager: history is not recycle-bin material.
        self.assertTrue(AssetDisposal.objects.filter(pk=pk).exists())

    def test_cancel_rejects_a_blank_reason_and_changes_nothing(self):
        with self.assertRaises(ValidationError):
            cancel_asset_disposal(self.disposal, user=self.tenant_user, reason="   ")
        self._refresh()
        self.assertIsNone(self.disposal.cancelled_at)
        self.assertEqual(self.asset.status_id, self.archived.pk)
        self.assertIsNotNone(self.asset.disposed_at)

    def test_cancel_rejects_a_missing_actor(self):
        with self.assertRaises(ValidationError):
            cancel_asset_disposal(self.disposal, user=None, reason="wrong asset")
        self._refresh()
        self.assertIsNone(self.disposal.cancelled_at)
        self.assertEqual(self.disposal.cancellation_reason, "")
        self.assertIsNone(self.disposal.cancelled_by_id)

    def test_second_cancel_is_rejected_without_mutation(self):
        cancel_asset_disposal(self.disposal, user=self.tenant_user, reason="first reason")
        with self.assertRaises(ValidationError):
            cancel_asset_disposal(self.disposal, user=self.tenant_user, reason="second reason")
        stored = AssetDisposal.all_objects.get(pk=self.disposal.pk)
        self.assertEqual(stored.cancellation_reason, "first reason")

    def test_later_disposal_creates_a_new_record_and_preserves_the_cancelled_one(self):
        original_pk = self.disposal.pk
        cancel_asset_disposal(self.disposal, user=self.tenant_user, reason="wrong asset")
        second = dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.DONATION,
            disposal_date=datetime.date(2026, 7, 1),
            user=self.tenant_user,
        )
        self.assertNotEqual(second.pk, original_pk)
        stored = list(AssetDisposal.all_objects.filter(asset=self.asset).order_by("pk"))
        self.assertEqual([row.pk for row in stored], [original_pk, second.pk])
        self.assertTrue(stored[0].is_cancelled)
        self.assertTrue(stored[1].is_active)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "archived")
        self.assertIsNotNone(self.asset.disposed_at)
        self.assertEqual(self.asset.active_disposal.pk, second.pk)

    def test_cancelled_record_does_not_block_a_later_checkout(self):
        cancel_asset_disposal(self.disposal, user=self.tenant_user, reason="wrong asset")
        self.asset.refresh_from_db()
        self.asset.status = self.deployable
        self.asset.save()
        holder = baker.make(AssetHolder, tenant=self.tenant)
        checkout_asset(asset=self.asset, holder=holder, user=self.tenant_user)
        self.assertTrue(AssetAssignment.objects.filter(asset=self.asset, is_active=True).exists())

    def test_active_disposal_blocks_checkout_even_when_the_status_drifted(self):
        """The RECORD owns the state: a drifted (or tampered) status label must not re-open the asset.

        ``QuerySet.update()`` bypasses ``save()``/``clean()`` exactly like raw SQL,
        so this is also the "ordinary status edit cannot bypass an active disposal"
        control.
        """
        Asset._base_manager.filter(pk=self.asset.pk).update(
            status=self.deployable, disposed_at=None, disposal_value=None
        )
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status_id, self.deployable.pk)
        self.assertIsNone(self.asset.disposed_at)
        holder = baker.make(AssetHolder, tenant=self.tenant)
        with self.assertRaises(ValidationError):
            checkout_asset(asset=self.asset, holder=holder, user=self.tenant_user)
        self.assertFalse(AssetAssignment.objects.filter(asset=self.asset, is_active=True).exists())

    def test_archive_only_asset_can_return_to_service_without_a_record(self):
        """Archiving is not a disposal: the archived->pending->deployable round trip stays available."""
        fresh = baker.make(Asset, name="Archived Only", status=self.deployable, tenant=self.tenant)
        fresh.status = self.archived
        fresh.save()
        fresh.refresh_from_db()
        self.assertIsNotNone(fresh.disposed_at)  # archival freeze
        self.assertIsNone(fresh.active_disposal)  # but no disposal evidence

        fresh.status = self.pending
        fresh.save()
        fresh.refresh_from_db()
        self.assertIsNone(fresh.disposed_at)
        fresh.status = self.deployable
        fresh.save()

        holder = baker.make(AssetHolder, tenant=self.tenant)
        checkout_asset(asset=fresh, holder=holder, user=self.tenant_user)
        self.assertTrue(AssetAssignment.objects.filter(asset=fresh, is_active=True).exists())

    def test_archive_only_asset_is_eligible_for_disposal_in_the_scan_payload(self):
        fresh = baker.make(Asset, name="Archived Eligible", status=self.deployable, tenant=self.tenant)
        fresh.status = self.archived
        fresh.save()
        fresh.refresh_from_db()
        self.assertTrue(asset_action_payload(fresh, "dispose")["eligible"])

        dispose_asset(
            asset=fresh,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 5),
            user=self.tenant_user,
        )
        payload = asset_action_payload(fresh, "dispose")
        self.assertFalse(payload["eligible"])

    def test_disposal_rejects_second_active_record_and_keeps_the_original(self):
        original_pk = self.disposal.pk
        with self.assertRaises(ValidationError):
            dispose_asset(
                asset=self.asset,
                disposal_method=DisposalMethodChoices.DONATION,
                disposal_date=datetime.date(2026, 7, 1),
                user=self.tenant_user,
            )
        stored = list(AssetDisposal.all_objects.filter(asset=self.asset))
        self.assertEqual([row.pk for row in stored], [original_pk])
        self.assertEqual(stored[0].disposal_method, DisposalMethodChoices.RECYCLE)

    def test_amending_metadata_syncs_the_asset_snapshot(self):
        from assets.services import update_asset_disposal

        update_asset_disposal(
            self.disposal,
            user=self.tenant_user,
            data={"proceeds": Decimal("250.00"), "recipient": "Recycler GmbH"},
        )
        self._refresh()
        self.disposal.refresh_from_db()
        self.assertEqual(self.disposal.recipient, "Recycler GmbH")
        self.assertEqual(self.asset.disposal_value, Decimal("250.00"))


class DisposalCancellationViewTests(TenantTestMixin, TestCase):
    """HTTP surface: cancel action, reason enforcement, authority and tenant boundary."""

    def setUp(self):
        self.setup_tenant_context(permissions=DISPOSAL_PERMS)
        self.deployable = baker.make(StatusLabel, type="deployable", name="Deployable")
        self.archived = baker.make(StatusLabel, type="archived", name="Archived")
        baker.make(StatusLabel, type="pending", name="Pending")
        self.asset = baker.make(Asset, name="View Laptop", status=self.deployable, tenant=self.tenant)
        self.disposal = dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 2),
            user=self.tenant_user,
        )
        self.cancel_url = reverse("assets:assetdisposal_cancel", kwargs={"pk": self.disposal.pk})
        self.client_login_to_tenant(self.tenant_user, self.tenant, role_permissions=DISPOSAL_PERMS)

    def test_cancel_view_cancels_and_audits(self):
        reason = "Recorded against the wrong asset"
        response = self.client.post(self.cancel_url, {"reason": reason})
        self.assertEqual(response.status_code, 302)
        stored = AssetDisposal.all_objects.get(pk=self.disposal.pk)
        self.assertEqual(stored.cancellation_reason, reason)
        self.assertEqual(stored.cancelled_by_id, self.tenant_user.pk)
        self.assertIsNotNone(stored.cancelled_at)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "pending")

        # Audit attribution on the RECORD: actor, content type, object id and the
        # stored change snapshot. ObjectChange has NO free-text message field — the
        # pre/post snapshots are the audit evidence.
        record_changes = ObjectChange.objects.filter(
            changed_object_type=ContentType.objects.get_for_model(AssetDisposal),
            changed_object_id=stored.pk,
            user_id=self.tenant_user.pk,
        )
        self.assertEqual(record_changes.count(), 1)
        change = record_changes.get()
        self.assertEqual(change.action, "update")
        self.assertEqual(change.tenant_id, self.tenant.pk)
        self.assertEqual(change.postchange_data["cancellation_reason"], reason)
        self.assertTrue(change.postchange_data["cancelled_at"])
        self.assertTrue(change.postchange_data["cancelled_by"])
        self.assertFalse(change.prechange_data["cancelled_at"])
        self.assertFalse(change.prechange_data["cancellation_reason"])
        self.assertFalse(change.prechange_data["cancelled_by"])
        # ...and on the ASSET whose status/stamps the cancellation changed.
        asset_changes = ObjectChange.objects.filter(
            changed_object_type=ContentType.objects.get_for_model(Asset),
            changed_object_id=self.asset.pk,
            user_id=self.tenant_user.pk,
        )
        self.assertEqual(asset_changes.count(), 1)
        self.assertTrue(asset_changes.get().prechange_data["disposed_at"])
        self.assertFalse(asset_changes.get().postchange_data["disposed_at"])

    def test_cancel_view_requires_a_reason_and_leaves_the_record_active(self):
        response = self.client.post(self.cancel_url, {"reason": "   "})
        self.assertEqual(response.status_code, 200)
        stored = AssetDisposal.all_objects.get(pk=self.disposal.pk)
        self.assertIsNone(stored.cancelled_at)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "archived")

    def test_cancel_view_denies_a_user_without_the_disposal_authority(self):
        weak_role = self.tenant_role.__class__.objects.create(
            tenant=self.tenant,
            name="No disposal right",
            permissions=["assets.view_asset", "assets.change_asset"],
        )
        weak_user = User.objects.create_user(username="weak", email="weak@example.com", password="password")
        self.grant(weak_user, self.tenant, weak_role)
        self.client.force_login(weak_user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

        response = self.client.post(self.cancel_url, {"reason": "let me"})
        self.assertEqual(response.status_code, 403)
        stored = AssetDisposal.all_objects.get(pk=self.disposal.pk)
        self.assertIsNone(stored.cancelled_at)

    def test_cancel_view_hides_a_foreign_tenant_record(self):
        other = Tenant.objects.create(name="Other Tenant", slug="other-tenant")
        other_role = self.tenant_role.__class__.objects.create(
            tenant=other, name="Other Role", permissions=DISPOSAL_PERMS
        )
        other_asset = baker.make(Asset, name="Other Laptop", status=self.deployable, tenant=other)
        other_disposal = dispose_asset(
            asset=other_asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 3),
            user=self.tenant_user,
        )
        other_asset.refresh_from_db()
        before = (other_asset.status_id, other_asset.disposed_at, other_asset.disposal_value)
        other_grant = self.grant(self.tenant_user, other, other_role)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

        response = self.client.post(
            reverse("assets:assetdisposal_cancel", kwargs={"pk": other_disposal.pk}),
            {"reason": "cross tenant attempt"},
        )
        self.assertIn(response.status_code, (403, 404))
        # The record must be read through an EXPLICIT foreign-tenant context:
        # `all_objects` is tenant-scoped, so an ambient-scope readback raises
        # DoesNotExist (that is what made the earlier version of this test fail).
        with self.tenant_context(other, other_grant.membership):
            foreign = AssetDisposal.all_objects.get(pk=other_disposal.pk)
            self.assertIsNone(foreign.cancelled_at)
            self.assertIsNone(foreign.cancelled_by_id)
            self.assertEqual(foreign.cancellation_reason, "")
            other_asset.refresh_from_db()
            self.assertEqual((other_asset.status_id, other_asset.disposed_at, other_asset.disposal_value), before)
            self.assertFalse(AssetAssignment.objects.filter(asset=other_asset, is_active=True).exists())
            self.assertFalse(
                ObjectChange.objects.filter(
                    changed_object_type=ContentType.objects.get_for_model(AssetDisposal),
                    changed_object_id=other_disposal.pk,
                ).exists()
            )

            # Positive control with the SAME roles: inside that tenant's scope the
            # same user may cancel, so the denial above is scope-based and not a
            # broken/loosened permission.
            session["active_tenant_id"] = other.pk
            session.save()
            allowed = self.client.post(
                reverse("assets:assetdisposal_cancel", kwargs={"pk": other_disposal.pk}),
                {"reason": "legitimate cancellation"},
            )
            self.assertEqual(allowed.status_code, 302)
            cancelled = AssetDisposal.all_objects.get(pk=other_disposal.pk)
            self.assertEqual(cancelled.cancelled_by_id, self.tenant_user.pk)
            self.assertEqual(cancelled.cancellation_reason, "legitimate cancellation")

    def test_delete_route_refuses_and_keeps_the_record(self):
        response = self.client.get(reverse("assets:assetdisposal_delete", kwargs={"pk": self.disposal.pk}))
        self.assertEqual(response.status_code, 302)
        stored = AssetDisposal.all_objects.get(pk=self.disposal.pk)
        self.assertIsNone(stored.deleted_at)
        self.assertIsNone(stored.cancelled_at)

    def test_german_rendering_of_the_cancellation_surface(self):
        """The new product strings are translated and render in German (#496)."""
        from django.test import override_settings
        from django.utils import translation

        with override_settings(LANGUAGE_CODE="de"), translation.override("de"):
            page = self.client.get(self.cancel_url)
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, "Entsorgung stornieren")
            self.assertContains(page, "Stornierungsgrund")
            # The asset panel offers the same action with the German label.
            panel = self.client.get(reverse("assets:asset_detail", kwargs={"pk": self.asset.pk}))
            self.assertContains(panel, "Entsorgung stornieren")

    def test_model_delete_is_refused_for_evidence(self):
        with self.assertRaises(ValidationError):
            self.disposal.delete()
        with self.assertRaises(ValidationError):
            AssetDisposal.all_objects.get(pk=self.disposal.pk).delete(force_hard_delete=False)
        self.assertTrue(AssetDisposal.all_objects.filter(pk=self.disposal.pk).exists())

    def test_asset_identity_is_immutable_on_record_edit(self):
        other_asset = baker.make(Asset, name="Other Own Asset", status=self.deployable, tenant=self.tenant)
        response = self.client.post(
            reverse("assets:assetdisposal_update", kwargs={"pk": self.disposal.pk}),
            {
                "asset": other_asset.pk,
                "disposal_method": DisposalMethodChoices.DONATION,
                "disposal_date": "2026-06-02",
                "data_sanitization_method": "none",
                "recipient": "Recycler",
                "proceeds": "",
                "currency": "",
                "notes": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        stored = AssetDisposal.all_objects.get(pk=self.disposal.pk)
        self.assertEqual(stored.asset_id, self.asset.pk)
        self.assertEqual(stored.disposal_method, DisposalMethodChoices.DONATION)


class DisposalCreatePathParityTests(TenantTestMixin, TestCase):
    """Every record-creating entry point runs the lifecycle operation (issue #496)."""

    def setUp(self):
        self.setup_tenant_context(permissions=DISPOSAL_PERMS)
        self.deployable = baker.make(StatusLabel, type="deployable", name="Deployable")
        self.archived = baker.make(StatusLabel, type="archived", name="Archived")
        self.pending = baker.make(StatusLabel, type="pending", name="Pending")
        self.asset = baker.make(Asset, name="Quick Laptop", status=self.deployable, tenant=self.tenant)
        self.asset_b = baker.make(Asset, name="Quick Laptop B", status=self.deployable, tenant=self.tenant)
        self.client_login_to_tenant(self.tenant_user, self.tenant, role_permissions=DISPOSAL_PERMS)
        self.create_url = reverse("assets:assetdisposal_create") + "?_quickadd=1"

    def _payload(self, asset, **overrides):
        data = {
            "asset": asset.pk,
            "disposal_method": DisposalMethodChoices.RECYCLE,
            "disposal_date": "2026-06-20",
            "data_sanitization_method": "none",
            "sanitization_certificate": "",
            "sanitized_by": "",
            "recipient": "",
            "proceeds": "",
            "currency": "",
            "weee_compliant": "",
            "notes": "",
        }
        data.update(overrides)
        return data

    def test_quick_add_creates_record_and_lifecycle_state_atomically(self):
        response = self.client.post(self.create_url, self._payload(self.asset))
        # The submission must really succeed (204 + HX-Redirect) before any
        # persisted-state assertion is meaningful.
        self.assertEqual(response.status_code, 204)
        self.assertTrue(response.headers.get("HX-Redirect"))
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status_id, self.archived.pk)
        self.assertIsNotNone(self.asset.disposed_at)
        self.assertIsNotNone(self.asset.disposal_value)
        self.assertEqual(AssetDisposal.objects.filter(asset=self.asset).count(), 1)
        # Audit attribution for the creation itself, not only for changes.
        self.assertTrue(
            ObjectChange.objects.filter(
                changed_object_type=ContentType.objects.get_for_model(AssetDisposal),
                user_id=self.tenant_user.pk,
            ).exists()
        )

    def test_quick_add_second_disposal_is_rejected_without_record_loss(self):
        dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 10),
            sanitization_certificate="KEEP-ME",
            user=self.tenant_user,
        )
        response = self.client.post(self.create_url, self._payload(self.asset, recipient="Second try"))
        self.assertIn(response.status_code, (200, 422))
        stored = list(AssetDisposal.all_objects.filter(asset=self.asset))
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].sanitization_certificate, "KEEP-ME")
        self.assertEqual(stored[0].recipient, "")

    def test_dedicated_action_disposes_the_url_asset_only(self):
        """A forged asset field cannot re-point the dedicated dispose action."""
        response = self.client.post(
            reverse("assets:asset_dispose", kwargs={"pk": self.asset.pk}),
            self._payload(self.asset_b),
        )
        self.assertEqual(response.status_code, 302)
        self.asset.refresh_from_db()
        self.asset_b.refresh_from_db()
        self.assertEqual(AssetDisposal.objects.filter(asset=self.asset).count(), 1)
        self.assertEqual(AssetDisposal.objects.filter(asset=self.asset_b).count(), 0)
        self.assertEqual(self.asset.status_id, self.archived.pk)
        self.assertEqual(self.asset_b.status_id, self.deployable.pk)

    def test_record_creation_auto_checks_in_an_active_assignment(self):
        holder = baker.make(AssetHolder, tenant=self.tenant)
        AssetAssignment.objects.create(asset=self.asset, assigned_user=holder, is_active=True)
        response = self.client.post(self.create_url, self._payload(self.asset))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(AssetDisposal.objects.filter(asset=self.asset).count(), 1)
        self.assertFalse(AssetAssignment.objects.filter(asset=self.asset, is_active=True).exists())


class DisposalLifecycleGuardTests(TenantTestMixin, TestCase):
    """An ordinary status edit may not leave `archived` while a record is active (#496)."""

    def setUp(self):
        self.setup_tenant_context(permissions=DISPOSAL_PERMS)
        self.set_active_tenant(self.tenant)
        self.deployable = baker.make(StatusLabel, type="deployable", name="Deployable")
        self.archived = baker.make(StatusLabel, type="archived", name="Archived")
        self.pending = baker.make(StatusLabel, type="pending", name="Pending")
        self.asset = baker.make(Asset, name="Guard Laptop", status=self.deployable, tenant=self.tenant)
        self.disposal = dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 4),
            user=self.tenant_user,
        )

    def test_request_form_excludes_an_asset_with_an_active_disposal(self):
        """The request form's asset field hides a disposed asset (#496).

        The ambient tenant context is set explicitly because the form resolves it via
        ``core.managers.get_current_tenant``. The route-level behaviour is covered by
        the POST regression below; the model-level guard by the model test.
        """
        from assets.forms.request_forms import AssetRequestForm

        Asset._base_manager.filter(pk=self.asset.pk).update(status=self.deployable.pk, requestable=True)
        self.asset.refresh_from_db()
        clean_asset = baker.make(
            Asset, name="Requestable Laptop", status=self.deployable, requestable=True, tenant=self.tenant
        )

        with self.tenant_context(self.tenant):
            eligible = set(AssetRequestForm(request=None).fields["asset"].queryset.values_list("pk", flat=True))
        self.assertIn(clean_asset.pk, eligible)
        self.assertNotIn(self.asset.pk, eligible)

    def _requester_with_request_rights(self):
        role = self.tenant_role.__class__.objects.create(
            tenant=self.tenant,
            name="Requester with request rights",
            permissions=["assets.add_assetrequest", "assets.view_assetrequest", "assets.view_asset"],
        )
        user = User.objects.create_user(username="requester496", email="requester496@example.com", password="password")
        self.grant(user, self.tenant, role)
        self.client.force_login(user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()
        return user

    def test_request_route_rejects_a_disposed_asset_and_accepts_a_permitted_one(self):
        """Real request POST: the permitted asset persists, the disposed one is rejected.

        Both assets are deployable and requestable, so the active disposal record is the
        ONLY difference between the accepted and the rejected POST.
        """
        self._requester_with_request_rights()
        Asset._base_manager.filter(pk=self.asset.pk).update(status=self.deployable.pk, requestable=True)
        self.asset.refresh_from_db()
        clean_asset = baker.make(
            Asset, name="Requestable Laptop", status=self.deployable, requestable=True, tenant=self.tenant
        )

        permitted = self.client.post(
            reverse("assets:request_create"),
            data={"asset_type": "", "asset": str(clean_asset.pk), "notes": "issue496 permitted"},
        )
        self.assertEqual(permitted.status_code, 302)
        self.assertEqual(AssetRequest.objects.filter(notes="issue496 permitted").count(), 1)
        stored = AssetRequest.objects.get(notes="issue496 permitted")
        self.assertEqual(stored.asset_id, clean_asset.pk)

        before = (self.asset.status_id, self.asset.disposed_at, self.disposal.cancelled_at)
        rejected = self.client.post(
            reverse("assets:request_create"),
            data={"asset_type": "", "asset": str(self.asset.pk), "notes": "issue496 rejected"},
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertFalse(AssetRequest.objects.filter(notes="issue496 rejected").exists())
        self.asset.refresh_from_db()
        self.disposal.refresh_from_db()
        self.assertEqual((self.asset.status_id, self.asset.disposed_at, self.disposal.cancelled_at), before)

    def test_asset_request_model_rejects_a_disposed_asset(self):
        """The request model refuses a disposed asset on every surface, not only the form."""
        Asset._base_manager.filter(pk=self.asset.pk).update(status=self.deployable.pk, requestable=True)
        self.asset.refresh_from_db()
        clean_asset = baker.make(
            Asset, name="Requestable Laptop", status=self.deployable, requestable=True, tenant=self.tenant
        )

        with self.assertRaises(ValidationError) as rejected:
            AssetRequest(
                asset=self.asset, requester=self.tenant_user, tenant=self.tenant, notes="issue496 model"
            ).full_clean()
        self.assertIn("disposed", str(rejected.exception).lower())
        self.assertFalse(AssetRequest.objects.filter(notes="issue496 model").exists())

        # Positive control: the same model state passes for an undisposed asset.
        AssetRequest(
            asset=clean_asset, requester=self.tenant_user, tenant=self.tenant, notes="issue496 model clean"
        ).full_clean()

    def test_archived_asset_without_a_record_is_visibly_labeled_as_not_disposed(self):
        """The archived panel must not claim a disposal (issue #496 AC)."""
        archive_only = baker.make(Asset, name="Archive Only", status=self.deployable, tenant=self.tenant)
        archive_only.status = self.archived
        archive_only.save()
        self.client_login_to_tenant(self.tenant_user, self.tenant, role_permissions=DISPOSAL_PERMS)

        response = self.client.get(reverse("assets:asset_detail", kwargs={"pk": archive_only.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["disposal_obj"])
        self.assertEqual(list(response.context["disposal_history"]), [])
        # The panel separates the archival freeze from a disposal sign-off.
        self.assertContains(response, "no disposal recorded")
        # ...while a genuinely disposed asset shows the disposal wording.
        disposed = self.client.get(reverse("assets:asset_detail", kwargs={"pk": self.asset.pk}))
        self.assertEqual(set(disposed.context["disposal_history"]), {self.disposal})
        self.assertContains(disposed, "Disposal sign-off value")

    def test_ordinary_status_edit_out_of_archived_is_refused(self):
        self.asset.status = self.pending
        with self.assertRaises(ValidationError):
            self.asset.save()
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "archived")
        self.assertIsNotNone(self.asset.disposed_at)
        self.assertIsNotNone(self.asset.active_disposal)

    def test_soft_deleted_uncancelled_tombstone_still_blocks_reactivation(self):
        """A hidden tombstone must not release the asset: it stays blocking evidence."""
        AssetDisposal.all_objects.filter(pk=self.disposal.pk).update(deleted_at=timezone.now())
        self.assertFalse(AssetDisposal.objects.filter(pk=self.disposal.pk).exists())
        self.asset.refresh_from_db()
        self.asset.status = self.pending
        with self.assertRaises(ValidationError):
            self.asset.save()

    def test_cancelled_record_releases_the_guard(self):
        cancel_asset_disposal(self.disposal, user=self.tenant_user, reason="wrong asset")
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "pending")


class DisposalConstraintAuthorityTests(TenantTestMixin, TestCase):
    """The conditional unique constraint is the authority, not only the service pre-check."""

    def setUp(self):
        self.setup_tenant_context(permissions=DISPOSAL_PERMS)
        self.set_active_tenant(self.tenant)
        self.deployable = baker.make(StatusLabel, type="deployable", name="Deployable")
        baker.make(StatusLabel, type="archived", name="Archived")
        baker.make(StatusLabel, type="pending", name="Pending")
        self.asset = baker.make(Asset, name="Constraint Laptop", status=self.deployable, tenant=self.tenant)
        self.disposal = dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 6),
            sanitization_certificate="ORIGINAL-EVIDENCE",
            user=self.tenant_user,
        )

    def test_raw_second_active_insert_is_rejected_by_the_database(self):
        """A bypassing ORM insert (no service pre-check) still cannot create a second owner."""
        with self.assertRaises(IntegrityError):
            # Nested atomic: the failed INSERT rolls back to a savepoint, so the
            # surrounding test transaction stays usable for the readback below.
            with transaction.atomic():
                AssetDisposal.objects.create(
                    asset=self.asset,
                    disposal_method=DisposalMethodChoices.DONATION,
                    disposal_date=datetime.date(2026, 8, 1),
                )
        self.assertEqual(AssetDisposal.all_objects.filter(asset=self.asset).count(), 1)

    def test_cancelled_record_does_not_occupy_the_active_slot(self):
        cancel_asset_disposal(self.disposal, user=self.tenant_user, reason="wrong asset")
        again = AssetDisposal.objects.create(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.DONATION,
            disposal_date=datetime.date(2026, 8, 2),
        )
        self.assertIsNotNone(again.pk)
        self.assertEqual(AssetDisposal.all_objects.filter(asset=self.asset, cancelled_at__isnull=True).count(), 1)

    def test_lost_insert_race_becomes_a_clean_rejection(self):
        """Injected failure: the pre-check read stale data, the INSERT lost the race."""

        def _explode(*args, **kwargs):
            raise IntegrityError("duplicate key value violates unique constraint")

        from assets import services as asset_services

        real_check = asset_services._active_disposal_error
        calls = {"count": 0}

        def stale_then_real(asset):
            calls["count"] += 1
            return None if calls["count"] == 1 else real_check(asset)

        with patch.object(asset_services, "_active_disposal_error", stale_then_real):
            with patch.object(AssetDisposal, "save", _explode):
                with self.assertRaises(ValidationError):
                    dispose_asset(
                        asset=self.asset,
                        disposal_method=DisposalMethodChoices.DONATION,
                        disposal_date=datetime.date(2026, 9, 1),
                        user=self.tenant_user,
                    )

        stored = AssetDisposal.all_objects.filter(asset=self.asset, cancelled_at__isnull=True).get()
        self.assertEqual(stored.pk, self.disposal.pk)
        self.assertEqual(stored.sanitization_certificate, "ORIGINAL-EVIDENCE")

    def test_unrelated_integrity_failure_is_not_masked(self):
        clean_asset = baker.make(Asset, name="Clean Laptop", status=self.deployable, tenant=self.tenant)
        with patch.object(AssetDisposal, "save", side_effect=IntegrityError("boom")):
            with self.assertRaises(IntegrityError):
                dispose_asset(
                    asset=clean_asset,
                    disposal_method=DisposalMethodChoices.RECYCLE,
                    disposal_date=datetime.date(2026, 9, 2),
                    user=self.tenant_user,
                )
        self.assertIsNone(clean_asset.active_disposal)


class DisposalAdminSurfaceTests(TenantTestMixin, TestCase):
    """The admin has no second, weaker write path (#496)."""

    def setUp(self):
        self.setup_tenant_context(permissions=DISPOSAL_PERMS)
        self.set_active_tenant(self.tenant)
        self.deployable = baker.make(StatusLabel, type="deployable", name="Deployable")
        self.archived = baker.make(StatusLabel, type="archived", name="Archived")
        baker.make(StatusLabel, type="pending", name="Pending")
        self.asset = baker.make(Asset, name="Admin Laptop", status=self.deployable, tenant=self.tenant)
        self.admin = AssetDisposalAdmin(AssetDisposal, AdminSite())
        self.request = RequestFactory().post("/admin/assets/assetdisposal/add/")
        self.request.user = self.tenant_user
        self.request.session = {}
        self.request._messages = []

    def test_admin_never_deletes_a_disposal_record(self):
        self.assertFalse(self.admin.has_delete_permission(self.request))

    def test_admin_marks_cancellation_and_identity_readonly(self):
        disposal = dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 7),
            user=self.tenant_user,
        )
        existing = self.admin.get_readonly_fields(self.request, obj=disposal)
        self.assertIn("asset", existing)
        self.assertIn("cancelled_at", existing)
        self.assertIn("cancelled_by", existing)
        self.assertIn("cancellation_reason", existing)
        # A new record still needs an asset choice.
        self.assertNotIn("asset", self.admin.get_readonly_fields(self.request, obj=None))

    def test_admin_create_runs_the_lifecycle_operation(self):
        form_obj = AssetDisposal(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 8),
            data_sanitization_method="none",
            currency="",
            notes="",
        )
        self.admin.save_model(self.request, form_obj, form=None, change=False)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "archived")
        self.assertIsNotNone(self.asset.disposed_at)
        self.assertEqual(self.asset.active_disposal.pk, form_obj.pk)
        self.assertIsNotNone(form_obj.pk)

    def test_admin_update_syncs_the_asset_snapshot(self):
        dispose_asset(
            asset=self.asset,
            disposal_method=DisposalMethodChoices.RECYCLE,
            disposal_date=datetime.date(2026, 6, 9),
            user=self.tenant_user,
        )
        stored = AssetDisposal.all_objects.get(asset=self.asset)
        stored.proceeds = Decimal("321.00")
        self.admin.save_model(self.request, stored, form=None, change=True)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.disposal_value, Decimal("321.00"))
        self.assertEqual(AssetDisposal.all_objects.get(pk=stored.pk).proceeds, Decimal("321.00"))


class DisposalApiSurfaceTests(TenantTestMixin, APITestCase):
    """REST: create is atomic, cancellation is explicit, evidence is never deletable (#496)."""

    def setUp(self):
        self.setup_tenant_context(permissions=DISPOSAL_PERMS)
        self.set_active_tenant(self.tenant)
        self.deployable = baker.make(StatusLabel, type="deployable", name="Deployable")
        self.archived = baker.make(StatusLabel, type="archived", name="Archived")
        baker.make(StatusLabel, type="pending", name="Pending")
        self.asset = baker.make(Asset, name="API Laptop", status=self.deployable, tenant=self.tenant)
        token = Token.objects.create(user=self.tenant_user, tenant=self.tenant)
        self.client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
        # The assets API is mounted under /api/assets/ (itambox/api/urls.py).
        self.list_url = "/api/assets/asset-disposals/"

    def _payload(self, asset, **overrides):
        data = {
            "asset_id": asset.pk,
            "disposal_method": DisposalMethodChoices.RECYCLE,
            "disposal_date": "2026-06-20",
            "data_sanitization_method": "none",
            "weee_compliant": False,
        }
        data.update(overrides)
        return data

    def _etag(self, detail):
        """Mutating API requests require the object's ETag (If-Match precondition)."""
        response = self.client.get(detail)
        self.assertEqual(response.status_code, 200, getattr(response, "data", None))
        etag = response.headers.get("ETag")
        self.assertTrue(etag)
        return etag

    def test_api_create_is_the_lifecycle_operation(self):
        response = self.client.post(self.list_url, self._payload(self.asset), format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "archived")
        self.assertIsNotNone(self.asset.disposed_at)
        self.assertIsNotNone(self.asset.disposal_value)
        self.assertEqual(AssetDisposal.objects.filter(asset=self.asset).count(), 1)
        self.assertEqual(response.data["is_active"], True)

    def test_api_second_active_disposal_is_rejected(self):
        first = self.client.post(self.list_url, self._payload(self.asset), format="json")
        self.assertEqual(first.status_code, 201, first.data)
        response = self.client.post(self.list_url, self._payload(self.asset, recipient="second try"), format="json")
        self.assertEqual(response.status_code, 400, response.data)
        records = list(AssetDisposal.all_objects.filter(asset=self.asset))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].pk, first.data["id"])

    def test_api_cancel_requires_authority_and_a_reason(self):
        created = self.client.post(self.list_url, self._payload(self.asset), format="json")
        detail = f"{self.list_url}{created.data['id']}/"

        blank = self.client.post(f"{detail}cancel/", {"reason": "  "}, format="json")
        self.assertEqual(blank.status_code, 400, blank.data)
        self.assertIsNone(AssetDisposal.all_objects.get(pk=created.data["id"]).cancelled_at)

        # Same actor, same token, authority withdrawn: the check must be the
        # disposal authority itself, never a staff/superuser shortcut.
        self.tenant_role.permissions = [p for p in DISPOSAL_PERMS if p != "assets.dispose_asset"]
        self.tenant_role.save()
        denied = self.client.post(f"{detail}cancel/", {"reason": "let me"}, format="json")
        self.assertEqual(denied.status_code, 403, denied.data)
        self.assertIsNone(AssetDisposal.all_objects.get(pk=created.data["id"]).cancelled_at)

        # Authorized positive path with the authority restored.
        self.tenant_role.permissions = list(DISPOSAL_PERMS)
        self.tenant_role.save()
        allowed = self.client.post(f"{detail}cancel/", {"reason": "recorded in error"}, format="json")
        self.assertEqual(allowed.status_code, 200, allowed.data)
        stored = AssetDisposal.all_objects.get(pk=created.data["id"])
        self.assertEqual(stored.cancellation_reason, "recorded in error")
        self.assertEqual(stored.cancelled_by_id, self.tenant_user.pk)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "pending")

    def test_api_patch_cannot_change_asset_identity_or_cancellation_state(self):
        created = self.client.post(self.list_url, self._payload(self.asset), format="json")
        detail = f"{self.list_url}{created.data['id']}/"
        etag = self._etag(detail)
        other_asset = baker.make(Asset, name="API Other", status=self.deployable, tenant=self.tenant)
        response = self.client.patch(
            detail,
            {"asset_id": other_asset.pk, "recipient": "Recycler", "cancellation_reason": "sneaky"},
            format="json",
            HTTP_IF_MATCH=etag,
        )
        self.assertEqual(response.status_code, 400, response.data)
        stored = AssetDisposal.all_objects.get(pk=created.data["id"])
        self.assertEqual(stored.asset_id, self.asset.pk)
        self.assertIsNone(stored.cancelled_at)
        self.assertEqual(stored.cancellation_reason, "")

    def test_api_metadata_patch_syncs_the_asset_snapshot(self):
        created = self.client.post(self.list_url, self._payload(self.asset), format="json")
        detail = f"{self.list_url}{created.data['id']}/"
        etag = self._etag(detail)
        response = self.client.patch(
            detail, {"proceeds": "150.00", "recipient": "Recycler"}, format="json", HTTP_IF_MATCH=etag
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.disposal_value, Decimal("150.00"))

    def test_api_delete_is_refused_and_keeps_the_record(self):
        created = self.client.post(self.list_url, self._payload(self.asset), format="json")
        detail = f"{self.list_url}{created.data['id']}/"
        etag = self._etag(detail)
        response = self.client.delete(detail, HTTP_IF_MATCH=etag)
        self.assertEqual(response.status_code, 400)
        stored = AssetDisposal.all_objects.get(pk=created.data["id"])
        self.assertIsNone(stored.deleted_at)
        self.assertIsNone(stored.cancelled_at)
