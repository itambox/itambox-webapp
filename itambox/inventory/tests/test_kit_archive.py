"""Per-aggregate archive for Kit (#619 step 3).

Deleting a kit is an archive of an aggregate root: its items are the kit's
definition, so they move with it. The former generic cascade physically deleted
them (``KitItem.kit`` is ``CASCADE`` and ``KitItem`` had no ``deleted_at``), so a
soft-deleted kit lost its items for good. These tests pin:

- the archive moves the items with the kit, each with its own audited delete and
  the operation marker, and the kit's own delete row;
- idempotent re-archive;
- restore brings the kit and the items an operation archived back, and never
  resurrects an item that was deleted on its own;
- a restore refused by the active-name unique slot leaves everything archived
  (no partial restore);
- atomicity: a failure after the child writes rolls the whole archive back;
- tenant isolation of the service;
- that the UI delete, the REST API delete and the recycle-bin restore all reach
  the service instead of the generic single-row soft delete, and that the list
  surfaces count live items only.
"""

from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from assets.models import AssetType, Manufacturer
from core.archive_handlers import ArchiveBlocked
from core.models import ObjectChange
from core.tests.mixins import TenantTestMixin
from inventory.archive_services import archive_kit, restore_kit
from inventory.models import Kit, KitItem
from inventory.views.kit_views import KitListView
from itambox.middleware import _current_user, _request_id
from organization.models import Tenant


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


def object_changes(instance, action=None):
    """Audit rows written for one instance."""
    content_type = ContentType.objects.get_for_model(instance.__class__)
    rows = ObjectChange._base_manager.filter(
        changed_object_type=content_type,
        changed_object_id=instance.pk,
    )
    return rows.filter(action=action) if action else rows


class KitArchiveFixtureMixin:
    """Fixtures shared by the service-level and surface-level suites."""

    def make_kit(self, name="P619 Kit", **extra):
        values = {"name": name, "tenant": self.tenant}
        values.update(extra)
        return Kit.objects.create(**values)

    def make_asset_type(self, model="P619 Laptop", slug="p619-laptop"):
        return AssetType.objects.create(
            manufacturer=Manufacturer.objects.create(name=f"P619 Mfg {slug}", slug=f"p619-mfg-{slug}"),
            model=model,
            slug=slug,
        )

    def make_item(self, kit, **extra):
        values = {"kit": kit, "asset_type": self.make_asset_type(slug=f"p619-at-{uuid4().hex[:8]}"), "qty": 1}
        values.update(extra)
        return KitItem.objects.create(**values)


class KitArchiveServiceTests(KitArchiveFixtureMixin, TenantTestMixin, TestCase):
    """`archive_kit` / `restore_kit` on their own."""

    def setUp(self):
        self.setup_tenant_context(name="P619 Kit Tenant", slug="p619-kit-tenant")
        self.kit = self.make_kit()
        self.first_item = self.make_item(self.kit)
        self.second_item = self.make_item(self.kit)

    def archive(self, kit=None, actor=None):
        with self.tenant_context(self.tenant), acting_as(actor or self.tenant_admin):
            return archive_kit(kit or self.kit)

    def restore(self, kit=None, actor=None):
        with self.tenant_context(self.tenant), acting_as(actor or self.tenant_admin):
            return restore_kit(kit or self.kit)

    # ------------------------------------------------------------- happy path
    def test_archive_moves_the_items_with_the_kit_and_audits_every_row(self):
        result = self.archive()

        # One archived root plus both items.
        self.assertEqual(result.archived, 3)
        self.assertEqual(result.detached, 0)
        self.assertIsNotNone(result.operation_id)

        self.kit.refresh_from_db()
        self.assertIsNotNone(self.kit.deleted_at)
        self.assertFalse(Kit.objects.filter(pk=self.kit.pk).exists())
        self.assertTrue(Kit.all_objects.filter(pk=self.kit.pk).exists())
        self.assertEqual(object_changes(self.kit, action="delete").count(), 1)

        for item in (self.first_item, self.second_item):
            item.refresh_from_db()
            self.assertIsNotNone(item.deleted_at)
            self.assertEqual(item.archive_operation_id, result.operation_id)
            self.assertEqual(object_changes(item, action="delete").count(), 1)
        self.assertEqual(KitItem.objects.filter(kit=self.kit).count(), 0)
        self.assertEqual(self.kit.items.count(), 0)
        self.assertEqual(KitItem.all_objects.filter(kit=self.kit).count(), 2)

    def test_archive_is_idempotent_and_audits_once(self):
        self.archive()
        second = self.archive()

        self.assertEqual(second.archived, 0)
        self.assertEqual(object_changes(self.kit, action="delete").count(), 1)
        self.assertEqual(object_changes(self.first_item, action="delete").count(), 1)

    def test_archive_keeps_the_journal_as_evidence(self):
        self.kit.journal_entries.create(comment="P619 journal note", user=self.tenant_admin)
        self.kit.journal_entries.create(comment="P619 second note", user=self.tenant_admin)

        result = self.archive()

        self.assertEqual(result.kept, 2)
        self.assertEqual(self.kit.journal_entries.count(), 2)

    def test_restore_brings_back_the_kit_and_the_archived_items(self):
        self.archive()
        self.restore()

        self.kit.refresh_from_db()
        self.assertIsNone(self.kit.deleted_at)
        self.assertTrue(Kit.objects.filter(pk=self.kit.pk).exists())
        self.assertEqual(object_changes(self.kit, action="update").count(), 1)

        for item in (self.first_item, self.second_item):
            item.refresh_from_db()
            self.assertIsNone(item.deleted_at)
            self.assertIsNone(item.archive_operation_id)
            self.assertEqual(object_changes(item, action="update").count(), 1)
        self.assertEqual(self.kit.items.count(), 2)

    def test_restore_leaves_an_individually_deleted_item_deleted(self):
        """Only rows an archive operation moved come back: a leaf delete has no marker."""
        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            self.second_item.delete()
        self.second_item.refresh_from_db()
        self.assertIsNotNone(self.second_item.deleted_at)
        self.assertIsNone(self.second_item.archive_operation_id)

        self.archive()
        self.restore()

        self.first_item.refresh_from_db()
        self.assertIsNone(self.first_item.deleted_at)
        self.second_item.refresh_from_db()
        self.assertIsNotNone(self.second_item.deleted_at)
        self.assertEqual(self.kit.items.count(), 1)

    # ------------------------------------------------------------- refusal
    def test_restore_refuses_when_another_active_kit_uses_the_name(self):
        self.archive()
        self.make_kit(name=self.kit.name)

        with self.assertRaises(ArchiveBlocked) as caught:
            self.restore()

        self.assertIn(self.kit.name, caught.exception.headline)
        self.kit.refresh_from_db()
        self.assertIsNotNone(self.kit.deleted_at)
        self.assertEqual(object_changes(self.kit, action="update").count(), 0)
        # The refusal happens before the items are restored: nothing moved.
        for item in (self.first_item, self.second_item):
            item.refresh_from_db()
            self.assertIsNotNone(item.deleted_at)

    # ------------------------------------------------------------- atomicity
    def test_archive_is_atomic_when_a_later_step_fails(self):
        with patch(
            "inventory.archive_services._kept_evidence_count",
            side_effect=RuntimeError("archive failed"),
        ):
            with self.assertRaises(RuntimeError):
                self.archive()

        self.kit.refresh_from_db()
        self.assertIsNone(self.kit.deleted_at)
        for item in (self.first_item, self.second_item):
            item.refresh_from_db()
            self.assertIsNone(item.deleted_at)
            self.assertIsNone(item.archive_operation_id)
        self.assertEqual(object_changes(self.kit).count(), 0)
        self.assertEqual(object_changes(self.first_item).count(), 0)

    # ------------------------------------------------------------- tenant boundary
    def test_archive_fails_closed_for_a_kit_outside_the_active_tenant(self):
        other_tenant = Tenant.objects.create(name="P619 Kit Other", slug="p619-kit-other")
        foreign = Kit.objects.create(name="P619 Foreign Kit", tenant=other_tenant)
        foreign_item = self.make_item(foreign)

        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            with self.assertRaises(PermissionDenied):
                archive_kit(foreign)

        foreign.refresh_from_db()
        foreign_item.refresh_from_db()
        self.assertIsNone(foreign.deleted_at)
        self.assertIsNone(foreign_item.deleted_at)


class KitArchiveSurfaceTests(KitArchiveFixtureMixin, TenantTestMixin, TestCase):
    """Every delete/restore surface reaches the aggregate service."""

    KIT_PERMISSIONS = [
        "inventory.view_kit",
        "inventory.change_kit",
        "inventory.delete_kit",
        "inventory.view_kititem",
        "inventory.change_kititem",
        "inventory.delete_kititem",
        "core.view_recyclebin",
        "core.change_recyclebin",
    ]

    def setUp(self):
        self.setup_tenant_context(
            name="P619 Kit Surface Tenant",
            slug="p619-kit-surface",
            permissions=list(self.KIT_PERMISSIONS),
        )
        self.kit = self.make_kit()
        self.item = self.make_item(self.kit)
        self.client_login_to_tenant(self.tenant_user, self.tenant)

    def _messages(self, response):
        return [str(message) for message in get_messages(response.wsgi_request)]

    def _archive_kit_row(self, kit):
        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            return archive_kit(kit)

    def _archived_item(self, item):
        return KitItem.all_objects.get(pk=item.pk)

    # ------------------------------------------------------------- UI delete
    def test_ui_delete_archives_the_kit_and_its_items(self):
        response = self.client.post(reverse("inventory:kit_delete", kwargs={"pk": self.kit.pk}))

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(Kit.all_objects.get(pk=self.kit.pk).deleted_at)
        self.assertIsNotNone(self._archived_item(self.item).deleted_at)

    def test_ui_item_delete_is_a_leaf_delete(self):
        """The kit item's own delete stays a leaf delete (no operation marker)."""
        response = self.client.post(reverse("inventory:kititem_delete", kwargs={"pk": self.item.pk}))

        self.assertEqual(response.status_code, 302)
        archived = self._archived_item(self.item)
        self.assertIsNotNone(archived.deleted_at)
        self.assertIsNone(archived.archive_operation_id)
        self.assertIsNone(Kit.all_objects.get(pk=self.kit.pk).deleted_at)

    # ------------------------------------------------------------- API delete
    def _api_detail_url(self, kit):
        return reverse("api:inventory_api:kit-detail", kwargs={"pk": kit.pk})

    def test_api_delete_archives_the_kit_and_its_items(self):
        detail_url = self._api_detail_url(self.kit)
        current = self.client.get(detail_url)

        response = self.client.delete(detail_url, HTTP_IF_MATCH=current["ETag"])

        self.assertEqual(response.status_code, 204)
        self.assertIsNotNone(Kit.all_objects.get(pk=self.kit.pk).deleted_at)
        self.assertIsNotNone(self._archived_item(self.item).deleted_at)

    def test_api_delete_of_another_tenants_kit_is_404(self):
        other = Tenant.objects.create(name="P619 Kit API Other", slug="p619-kit-api-other")
        foreign = Kit.objects.create(name="P619 Foreign API Kit", tenant=other)

        response = self.client.delete(self._api_detail_url(foreign))

        self.assertEqual(response.status_code, 404)
        self.assertIsNone(Kit.all_objects.get(pk=foreign.pk).deleted_at)

    # ------------------------------------------------------------- recycle bin
    def test_recycle_bin_restore_uses_the_service(self):
        self._archive_kit_row(self.kit)
        content_type = ContentType.objects.get_for_model(Kit)

        response = self.client.post(
            reverse(
                "object_restore",
                kwargs={"content_type_id": content_type.pk, "object_id": self.kit.pk},
            )
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(Kit.all_objects.get(pk=self.kit.pk).deleted_at)
        self.assertIsNone(self._archived_item(self.item).deleted_at)
        self.assertEqual(self.kit.items.count(), 1)

    def test_recycle_bin_restore_refusal_keeps_everything_archived(self):
        self._archive_kit_row(self.kit)
        self.make_kit(name=self.kit.name)
        content_type = ContentType.objects.get_for_model(Kit)

        response = self.client.post(
            reverse(
                "object_restore",
                kwargs={"content_type_id": content_type.pk, "object_id": self.kit.pk},
            ),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(any("another active kit" in message for message in self._messages(response)))
        self.assertIsNotNone(Kit.all_objects.get(pk=self.kit.pk).deleted_at)
        self.assertIsNotNone(self._archived_item(self.item).deleted_at)

    # ------------------------------------------------------------- list surfaces
    def test_kit_list_counts_live_items_only(self):
        """The join-based row counter must not count an item deleted on its own."""
        self.make_item(self.kit)
        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            self.item.delete()

        counts = {row.pk: row.item_count for row in KitListView.queryset.all()}
        self.assertEqual(counts[self.kit.pk], 1)

    def test_kit_list_hides_an_archived_kit(self):
        self._archive_kit_row(self.kit)

        listed = {row.pk for row in KitListView.queryset.all()}
        self.assertNotIn(self.kit.pk, listed)
