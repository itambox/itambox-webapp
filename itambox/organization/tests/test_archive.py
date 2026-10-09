"""Per-aggregate archive pilot for AssetHolder (#619).

Deleting an asset holder is an archive of an aggregate root: it must refuse while
the person still carries an open obligation (the documented consequence of the
former generic cascade was a soft-deleted holder keeping an active asset
assignment — #608), detach the rows that must not keep pointing at an archived
holder, and reach the same service from every delete/restore surface.

These tests pin:

- the refusal per blocking obligation class, with no partial write and no audit
  row when the archive is refused;
- the detach of the holder's user link and of the subscriptions covering it;
- the archive/restore audit entries and the idempotent re-archive;
- the conditional unique slot revalidation on restore;
- atomicity when a detach fails halfway;
- tenant isolation of the service;
- that UI delete, bulk delete, API delete and the recycle-bin restore all reach
  the service instead of the generic single-row soft delete.
"""

from contextlib import contextmanager
from datetime import date, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from assets.choices import RequestStatusChoices
from assets.models import (
    Asset,
    AssetAssignment,
    AssetRequest,
    AssetReservation,
    AssetType,
    Category,
    Manufacturer,
    StatusLabel,
    Supplier,
)
from assets.models.choices import ReservationStatusChoices
from assets.services import checkout_asset
from compliance.models import CustodyReceipt, CustodySigningSession, CustodyTemplate
from core.models import ObjectChange
from core.tests.mixins import TenantTestMixin
from inventory.models import (
    Accessory,
    AccessoryAssignment,
    Component,
    ComponentAllocation,
    Consumable,
    ConsumableAssignment,
)
from inventory.models_assignment_write import authorized_assignment_write
from itambox.middleware import _current_user, _request_id
from licenses.models import License, LicenseSeatAssignment, Software
from organization.models import AssetHolder, Tenant
from organization.services.archive import ArchiveBlocked, archive_holder, restore_holder
from subscriptions.models import (
    BillingCycleChoices,
    Subscription,
    SubscriptionAssignment,
    SubscriptionStatusChoices,
    SubscriptionTypeChoices,
)

User = get_user_model()


@contextmanager
def acting_as(user=None):
    """Enter the request audit context the archive service writes into.

    Outside a request/task context ``save()`` silently skips the audit row, so
    assertions about ``ObjectChange`` need this explicitly.
    """
    request_token = _request_id.set(uuid4())
    user_token = _current_user.set(user)
    try:
        yield
    finally:
        _request_id.reset(request_token)
        _current_user.reset(user_token)


def holder_changes(holder, action=None):
    """Audit rows written for one holder."""
    content_type = ContentType.objects.get_for_model(AssetHolder)
    rows = ObjectChange._base_manager.filter(
        changed_object_type=content_type,
        changed_object_id=holder.pk,
    )
    return rows.filter(action=action) if action else rows


class AssetHolderArchiveFixtureMixin:
    """Fixtures shared by the service-level and surface-level suites."""

    def make_holder(self, upn="p619.holder@example.test", **extra):
        values = {"first_name": "Pia", "last_name": "Archiviert", "tenant": self.tenant}
        values.update(extra)
        return AssetHolder.objects.create(upn=upn, **values)

    def make_asset(self, tag="P619-ASSET-001"):
        self.available_status = StatusLabel.objects.create(
            name=f"P619 Available {tag}", slug=f"p619-available-{tag.lower()}"
        )
        self.deployed_status = StatusLabel.objects.create(
            name=f"P619 In Use {tag}", slug=f"p619-in-use-{tag.lower()}", type="deployed"
        )
        return Asset.objects.create(
            name=f"P619 Asset {tag}",
            asset_tag=tag,
            status=self.available_status,
            tenant=self.tenant,
        )

    def checkout_asset_to_holder(self, asset, holder):
        """Check one asset out to the holder and return the resulting assignment.

        ``checkout_asset`` returns the checkout *target* (the holder), not the
        assignment row the obligations are read from.
        """
        with self.tenant_context(self.tenant):
            checkout_asset(asset, holder=holder, status=self.deployed_status)
            return AssetAssignment.objects.get(asset=asset, is_active=True)

    def make_subscription_assignment(self, holder):
        supplier = Supplier.objects.create(name="P619 Supplier", slug="p619-supplier")
        subscription = Subscription.objects.create(
            name="P619 Subscription",
            supplier=supplier,
            tenant=self.tenant,
            type=SubscriptionTypeChoices.SAAS,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_cost=49.99,
            currency="EUR",
            billing_cycle=BillingCycleChoices.ANNUAL,
            licensed_quantity=5,
        )
        holder_content_type = ContentType.objects.get(app_label="organization", model="assetholder")
        return SubscriptionAssignment.objects.create(
            subscription=subscription,
            content_type=holder_content_type,
            object_id=holder.pk,
        )

    def make_custody_receipt(self, holder):
        category = Category.objects.create(name="P619 Cat", slug="p619-cat")
        template = CustodyTemplate.objects.create(
            tenant=self.tenant,
            category=category,
            is_active=True,
            require_acceptance=True,
            email_signature_request=True,
            signature_provider="local",
            name="P619 EULA",
            eula_text="P619 EULA terms.",
            disclaimer="Sign here.",
        )
        return CustodyReceipt.objects.create(
            asset=self.make_asset("P619-ASSET-CUSTODY"),
            holder=holder,
            custody_template=template,
            eula_text="P619 custody terms.",
        )

    def make_signing_session(self, holder):
        receipt = self.make_custody_receipt(holder)
        return CustodySigningSession.objects.create(
            receipt=receipt,
            operator=self.tenant_admin,
            intended_holder=holder,
        )

    def make_inventory_assignment(self, holder, kind="accessory"):
        """Attach one open inventory checkout to the holder.

        Inventory assignment rows are write-protected, so they are created
        through the authorized-write context the product uses.
        """
        if kind == "accessory":
            item = Accessory.objects.create(
                name="P619 Dock",
                slug="p619-dock",
                manufacturer=Manufacturer.objects.create(name="P619 Acc Mfg", slug="p619-acc-mfg"),
                tenant=self.tenant,
            )
            row = AccessoryAssignment(accessory=item, assigned_holder=holder)
        elif kind == "component":
            item = Component.objects.create(
                name="P619 RAM",
                manufacturer=Manufacturer.objects.create(name="P619 Mfg", slug="p619-mfg"),
                tenant=self.tenant,
            )
            row = ComponentAllocation(component=item, assigned_holder=holder)
        else:
            item = Consumable.objects.create(
                name="P619 Cable",
                slug="p619-cable",
                manufacturer=Manufacturer.objects.create(name="P619 Consumable Mfg", slug="p619-consumable-mfg"),
                tenant=self.tenant,
            )
            row = ConsumableAssignment(consumable=item, assigned_holder=holder)
        with authorized_assignment_write(row):
            row.save()
        return row

    def make_license_seat(self, holder):
        software = Software.objects.create(
            name="P619 Software",
            manufacturer=Manufacturer.objects.create(name="P619 License Mfg", slug="p619-license-mfg"),
            tenant=self.tenant,
        )
        license_row = License.objects.create(name="P619 License", software=software, seats=10, tenant=self.tenant)
        return LicenseSeatAssignment.objects.create(license=license_row, assigned_holder=holder)

    def make_open_request(self, holder):
        """One open request the holder is the assigned user of.

        ``AssetRequest`` requires exactly one requested category, so the request
        names a requestable asset type.
        """
        asset_type = AssetType.objects.create(
            manufacturer=Manufacturer.objects.create(name="P619 Request Mfg", slug="p619-request-mfg"),
            model="P619 Requestable",
            slug="p619-requestable",
            requestable=True,
        )
        return AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.tenant_user,
            assigned_user=holder,
            asset_type=asset_type,
            status=RequestStatusChoices.APPROVED,
        )

    def make_reservation(self, holder, asset):
        today = date.today()
        return AssetReservation.objects.create(
            asset=asset,
            reserved_for=holder,
            start_date=today,
            end_date=today + timedelta(days=7),
            status=ReservationStatusChoices.ACTIVE,
        )


class AssetHolderArchiveServiceTests(AssetHolderArchiveFixtureMixin, TenantTestMixin, TestCase):
    """`archive_holder` / `restore_holder` on their own."""

    def setUp(self):
        self.setup_tenant_context(name="P619 Tenant", slug="p619-tenant")
        self.holder = self.make_holder()

    def archive(self, holder=None, actor=None):
        with self.tenant_context(self.tenant), acting_as(actor or self.tenant_admin):
            return archive_holder(holder or self.holder)

    def restore(self, holder=None, actor=None):
        with self.tenant_context(self.tenant), acting_as(actor or self.tenant_admin):
            return restore_holder(holder or self.holder)

    # ------------------------------------------------------------- happy path
    def test_clean_holder_is_archived_with_one_audit_row(self):
        result = self.archive()

        self.assertEqual((result.archived, result.detached, result.kept), (1, 0, 0))
        self.assertIsNotNone(result.operation_id)
        self.holder.refresh_from_db()
        self.assertIsNotNone(self.holder.deleted_at)
        self.assertFalse(AssetHolder.objects.filter(pk=self.holder.pk).exists())
        self.assertTrue(AssetHolder.all_objects.filter(pk=self.holder.pk).exists())
        rows = holder_changes(self.holder)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().action, "delete")

    def test_archive_is_idempotent_and_audits_once(self):
        self.archive()
        second = self.archive()

        self.assertEqual(second.archived, 0)
        self.assertEqual(holder_changes(self.holder, action="delete").count(), 1)

    def test_restore_brings_the_holder_back_with_an_update_row(self):
        self.archive()
        self.restore()

        self.holder.refresh_from_db()
        self.assertIsNone(self.holder.deleted_at)
        self.assertTrue(AssetHolder.objects.filter(pk=self.holder.pk).exists())
        self.assertEqual(holder_changes(self.holder, action="update").count(), 1)

    def test_restore_leaves_the_user_link_detached(self):
        self.holder.user = self.tenant_user
        self.holder.save(update_fields=["user"])

        self.archive()
        self.holder.refresh_from_db()
        self.assertIsNone(self.holder.user_id)

        self.restore()
        self.holder.refresh_from_db()
        self.assertIsNone(self.holder.user_id)

    # ------------------------------------------------------------- refusals (#608)
    def test_active_asset_assignment_refuses_the_archive(self):
        """The documented #608 consequence: an archived holder kept an active assignment."""
        self.holder.user = self.tenant_user
        self.holder.save(update_fields=["user"])
        asset = self.make_asset()
        with self.tenant_context(self.tenant):
            assignment = self.checkout_asset_to_holder(asset, self.holder)

        with self.assertRaises(ArchiveBlocked) as caught:
            self.archive()

        self.assertEqual([blocker.kind for blocker in caught.exception.blockers], ["asset_assignment"])
        self.assertIn("open obligation", caught.exception.headline)
        self.holder.refresh_from_db()
        self.assertIsNone(self.holder.deleted_at)
        assignment.refresh_from_db()
        self.assertTrue(assignment.is_active)
        self.assertEqual(holder_changes(self.holder).count(), 0)

    def test_every_open_obligation_class_refuses_the_archive(self):
        asset = self.make_asset("P619-ASSET-CLASSES")
        with self.tenant_context(self.tenant):
            self.checkout_asset_to_holder(asset, self.holder)
            self.make_inventory_assignment(self.holder, "accessory")
            self.make_inventory_assignment(self.holder, "component")
            self.make_inventory_assignment(self.holder, "consumable")
            self.make_license_seat(self.holder)
            self.make_open_request(self.holder)
            self.make_reservation(self.holder, asset)
            self.make_signing_session(self.holder)

            with self.assertRaises(ArchiveBlocked) as caught:
                archive_holder(self.holder)

        kinds = sorted(blocker.kind for blocker in caught.exception.blockers)
        self.assertEqual(
            kinds,
            [
                "accessory_assignment",
                "asset_assignment",
                "asset_request",
                "asset_reservation",
                "component_allocation",
                "consumable_assignment",
                "custody_signing_session",
                "license_seat",
            ],
        )
        self.holder.refresh_from_db()
        self.assertIsNone(self.holder.deleted_at)

    def test_closed_obligations_do_not_refuse_the_archive(self):
        """A closed assignment and a pending custody receipt are kept, not blocking."""
        asset = self.make_asset("P619-ASSET-CLOSED")
        with self.tenant_context(self.tenant):
            assignment = self.checkout_asset_to_holder(asset, self.holder)
            assignment.is_active = False
            assignment.save(update_fields=["is_active"])
            receipt = self.make_custody_receipt(self.holder)

            result = archive_holder(self.holder)

        self.assertEqual(result.archived, 1)
        self.assertEqual(result.kept, 1)
        receipt.refresh_from_db()
        self.assertEqual(receipt.holder_id, self.holder.pk)
        self.assertIsNotNone(AssetHolder.all_objects.get(pk=self.holder.pk).deleted_at)

    # ------------------------------------------------------------- detach
    def test_archive_detaches_the_user_link_and_ends_subscriptions(self):
        self.holder.user = self.tenant_user
        self.holder.save(update_fields=["user"])
        assignment = self.make_subscription_assignment(self.holder)

        result = self.archive()

        self.assertEqual(result.detached, 2)
        self.holder.refresh_from_db()
        self.assertIsNone(self.holder.user_id)
        self.assertFalse(SubscriptionAssignment._base_manager.filter(pk=assignment.pk).exists())
        self.assertEqual(holder_changes(self.holder, action="delete").count(), 1)

    def test_archive_is_atomic_when_a_detach_fails(self):
        self.holder.user = self.tenant_user
        self.holder.save(update_fields=["user"])

        with patch(
            "organization.services.archive._end_subscription_assignments",
            side_effect=RuntimeError("detach failed"),
        ):
            with self.assertRaises(RuntimeError):
                self.archive()

        self.holder.refresh_from_db()
        self.assertIsNone(self.holder.deleted_at)
        self.assertEqual(self.holder.user_id, self.tenant_user.pk)
        self.assertEqual(holder_changes(self.holder).count(), 0)

    # ------------------------------------------------------------- restore conflict
    def test_restore_refuses_when_another_active_holder_uses_the_upn(self):
        self.archive()
        self.make_holder(upn=self.holder.upn, first_name="Neue", last_name="Person")

        with self.assertRaises(ArchiveBlocked) as caught:
            self.restore()

        self.assertIn(self.holder.upn, caught.exception.headline)
        self.assertIsNotNone(AssetHolder.all_objects.get(pk=self.holder.pk).deleted_at)
        self.assertEqual(holder_changes(self.holder, action="update").count(), 0)

    def test_restore_refuses_cleanly_when_the_same_user_was_re_created(self):
        """The conditional user slot is only reachable through a fresh holder row."""
        self.holder.user = self.tenant_user
        self.holder.save(update_fields=["user"])
        self.archive()

        replacement = self.make_holder(upn="p619.replacement@example.test", user=self.tenant_user)
        self.assertIsNotNone(replacement.pk)

        self.restore()
        self.holder.refresh_from_db()
        self.assertIsNone(self.holder.deleted_at)
        # Restoring never re-attaches the detached user link, so the unique
        # (tenant, user) slot stays with the replacement.
        self.assertIsNone(self.holder.user_id)
        replacement.refresh_from_db()
        self.assertEqual(replacement.user_id, self.tenant_user.pk)

    # ------------------------------------------------------------- tenant boundary
    def test_archive_fails_closed_for_a_holder_outside_the_active_tenant(self):
        other_tenant = Tenant.objects.create(name="P619 Other", slug="p619-other")
        foreign = AssetHolder.objects.create(
            first_name="Fremd",
            last_name="Person",
            upn="p619.foreign@example.test",
            tenant=other_tenant,
        )

        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            with self.assertRaises(PermissionDenied):
                archive_holder(foreign)

        foreign.refresh_from_db()
        self.assertIsNone(foreign.deleted_at)


class AssetHolderArchiveSurfaceTests(AssetHolderArchiveFixtureMixin, TenantTestMixin, TestCase):
    """Every delete/restore surface reaches the aggregate service."""

    HOLDER_PERMISSIONS = [
        "organization.view_assetholder",
        "organization.change_assetholder",
        "organization.delete_assetholder",
        "core.view_recyclebin",
        "core.change_recyclebin",
    ]

    def setUp(self):
        self.setup_tenant_context(
            name="P619 Surface Tenant",
            slug="p619-surface",
            permissions=list(self.HOLDER_PERMISSIONS),
        )
        self.holder = self.make_holder()
        self.client_login_to_tenant(self.tenant_user, self.tenant)

    def _messages(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def _archive_holder_row(self, holder):
        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            return archive_holder(holder)

    # ------------------------------------------------------------- UI delete
    def test_ui_delete_archives_through_the_service(self):
        response = self.client.post(reverse("organization:assetholder_delete", kwargs={"pk": self.holder.pk}))

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(AssetHolder.all_objects.get(pk=self.holder.pk).deleted_at)

    def test_ui_delete_reports_the_refusal_and_keeps_the_holder(self):
        asset = self.make_asset("P619-ASSET-UI")
        with self.tenant_context(self.tenant):
            assignment = self.checkout_asset_to_holder(asset, self.holder)

        response = self.client.post(
            reverse("organization:assetholder_delete", kwargs={"pk": self.holder.pk}), follow=True
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[-1][0], self.holder.get_absolute_url())
        messages = self._messages(response)
        self.assertTrue(any("open obligation" in message for message in messages), messages)
        self.assertIsNone(AssetHolder.all_objects.get(pk=self.holder.pk).deleted_at)
        assignment.refresh_from_db()
        self.assertTrue(assignment.is_active)

    # ------------------------------------------------------------- bulk delete
    def test_bulk_delete_archives_through_the_service(self):
        response = self.client.post(
            reverse("organization:assetholder_bulk_delete"),
            {
                "_confirm": "1",
                "pk": [self.holder.pk],
                "return_url": reverse("organization:assetholder_list"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(AssetHolder.all_objects.get(pk=self.holder.pk).deleted_at)

    def test_bulk_delete_refusal_rolls_back_the_whole_batch(self):
        clean = self.make_holder(upn="p619.clean@example.test")
        asset = self.make_asset("P619-ASSET-BULK")
        with self.tenant_context(self.tenant):
            assignment = self.checkout_asset_to_holder(asset, self.holder)

        response = self.client.post(
            reverse("organization:assetholder_bulk_delete"),
            {
                "_confirm": "1",
                "pk": [self.holder.pk, clean.pk],
                "return_url": reverse("organization:assetholder_list"),
            },
        )

        self.assertEqual(response.status_code, 302)
        messages = self._messages(response)
        self.assertTrue(any("open obligation" in message for message in messages), messages)
        self.assertIsNone(AssetHolder.all_objects.get(pk=self.holder.pk).deleted_at)
        self.assertIsNone(AssetHolder.all_objects.get(pk=clean.pk).deleted_at)
        assignment.refresh_from_db()
        self.assertTrue(assignment.is_active)

    # ------------------------------------------------------------- API delete
    def _api_detail_url(self, holder):
        return reverse("api:organization_api:assetholder-detail", kwargs={"pk": holder.pk})

    def test_api_delete_archives_through_the_service(self):
        detail_url = self._api_detail_url(self.holder)
        current = self.client.get(detail_url)

        response = self.client.delete(detail_url, HTTP_IF_MATCH=current["ETag"])

        self.assertEqual(response.status_code, 204)
        self.assertIsNotNone(AssetHolder.all_objects.get(pk=self.holder.pk).deleted_at)

    def test_api_delete_refusal_is_a_400_and_keeps_the_holder(self):
        asset = self.make_asset("P619-ASSET-API")
        with self.tenant_context(self.tenant):
            self.checkout_asset_to_holder(asset, self.holder)

        detail_url = self._api_detail_url(self.holder)
        current = self.client.get(detail_url)
        response = self.client.delete(detail_url, HTTP_IF_MATCH=current["ETag"])

        self.assertEqual(response.status_code, 400)
        self.assertIn("open obligation", str(response.json()))
        self.assertIsNone(AssetHolder.all_objects.get(pk=self.holder.pk).deleted_at)

    def test_api_delete_of_another_tenants_holder_is_404(self):
        other = Tenant.objects.create(name="P619 API Other", slug="p619-api-other")
        foreign = AssetHolder.objects.create(
            first_name="Fremd",
            last_name="Person",
            upn="p619.api-foreign@example.test",
            tenant=other,
        )

        response = self.client.delete(self._api_detail_url(foreign))

        self.assertEqual(response.status_code, 404)
        self.assertIsNone(AssetHolder.all_objects.get(pk=foreign.pk).deleted_at)

    # ------------------------------------------------------------- recycle bin
    def test_recycle_bin_restore_uses_the_service(self):
        self._archive_holder_row(self.holder)
        content_type = ContentType.objects.get_for_model(AssetHolder)

        response = self.client.post(
            reverse(
                "object_restore",
                kwargs={"content_type_id": content_type.pk, "object_id": self.holder.pk},
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(AssetHolder.all_objects.get(pk=self.holder.pk).deleted_at)

    def test_recycle_bin_restore_refusal_keeps_the_holder_archived(self):
        self._archive_holder_row(self.holder)
        self.make_holder(upn=self.holder.upn, first_name="Neue", last_name="Person")
        content_type = ContentType.objects.get_for_model(AssetHolder)

        response = self.client.post(
            reverse(
                "object_restore",
                kwargs={"content_type_id": content_type.pk, "object_id": self.holder.pk},
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        messages = self._messages(response)
        self.assertTrue(any("another active asset holder" in message for message in messages), messages)
        self.assertIsNotNone(AssetHolder.all_objects.get(pk=self.holder.pk).deleted_at)
