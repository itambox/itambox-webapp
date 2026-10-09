"""Per-aggregate archive for the inventory items (#619, step 4).

Component, Accessory and Consumable share one aggregate shape, so the same
behaviour table is exercised for each of them. The tests pin:

- the archive moves the item's empty stock rows with it (each with its own
  audited delete and the operation marker), unlinks its open asset requests and
  ends the subscriptions covering it;
- REFUSE (typed ``ArchiveBlocked``, no partial write) while a live assignment
  draws on the item, while a stock row still holds a positive quantity, or while
  a live kit item lists it;
- idempotent re-archive;
- restore brings the item and the stock rows an operation archived back, and
  never resurrects a stock row deleted on its own;
- a restore refused by the active-slug unique slot leaves everything archived;
- atomicity: a failure after the child writes rolls the whole archive back;
- tenant isolation of the service;
- that the UI delete and the recycle-bin restore reach the service.
"""

from contextlib import contextmanager
from unittest.mock import patch
from uuid import uuid4

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.urls import reverse

from assets.choices import RequestStatusChoices
from assets.models import AssetRequest, Manufacturer, Supplier
from core.archive_handlers import ArchiveBlocked
from core.models import ObjectChange
from core.tests.mixins import TenantTestMixin
from inventory.item_archive_services import ACCESSORY, COMPONENT, CONSUMABLE, HANDLERS
from inventory.models import (
    AccessoryAssignment,
    ComponentAllocation,
    ConsumableAssignment,
    Kit,
    KitItem,
)
from inventory.models_assignment_write import authorized_assignment_write
from itambox.middleware import _current_user, _request_id
from organization.models import AssetHolder, Location, Site, Tenant
from subscriptions.models import (
    BillingCycleChoices,
    Subscription,
    SubscriptionAssignment,
    SubscriptionStatusChoices,
    SubscriptionTypeChoices,
)


@contextmanager
def acting_as(user=None):
    """Enter the request audit context the archive service writes into."""
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


#: ``(aggregate, assignment model)`` for the three inventory item families.
FAMILIES = ((COMPONENT, ComponentAllocation), (ACCESSORY, AccessoryAssignment), (CONSUMABLE, ConsumableAssignment))


class InventoryItemArchiveFixtureMixin:
    """Fixtures shared by the service-level and surface-level suites."""

    def make_item(self, aggregate, name, **extra):
        values = {
            "name": name,
            "manufacturer": Manufacturer.objects.create(name=f"Mfg {name}", slug=f"mfg-{uuid4().hex[:8]}"),
            "tenant": self.tenant,
        }
        values.update(extra)
        return aggregate.model.objects.create(**values)

    def make_location(self):
        return Location.objects.create(
            name=f"Loc {uuid4().hex[:8]}", slug=f"loc-{uuid4().hex[:8]}", site=self.site, tenant=self.tenant
        )

    def make_stock(self, aggregate, item, qty=0, **extra):
        return aggregate.stock_model.objects.create(
            **{aggregate.item_attr: item}, location=self.make_location(), qty=qty, **extra
        )

    def make_assignment(self, aggregate, assignment_model, item, holder):
        """Create one open assignment row through the authorized-write context."""
        row = assignment_model(**{aggregate.item_attr: item}, assigned_holder=holder)
        with authorized_assignment_write(row):
            row.save()
        return row

    def make_holder(self):
        return AssetHolder.objects.create(
            first_name="Pia",
            last_name="Archiviert",
            tenant=self.tenant,
            upn=f"p619d.{uuid4().hex[:8]}@example.test",
        )

    def make_kit_item(self, aggregate, item):
        kit = Kit.objects.create(name=f"Kit {uuid4().hex[:8]}", tenant=self.tenant)
        return KitItem.objects.create(kit=kit, qty=1, **{aggregate.item_attr: item})

    def make_open_request(self, aggregate, item, status=RequestStatusChoices.PENDING):
        return AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.tenant_admin,
            status=status,
            **{aggregate.item_attr: item},
        )

    def make_subscription_assignment(self, aggregate, item):
        supplier = Supplier.objects.create(name=f"Supplier {uuid4().hex[:8]}", slug=f"sup-{uuid4().hex[:8]}")
        subscription = Subscription.objects.create(
            name=f"Subscription {uuid4().hex[:8]}",
            supplier=supplier,
            tenant=self.tenant,
            type=SubscriptionTypeChoices.SAAS,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_cost=49.99,
            currency="EUR",
            billing_cycle=BillingCycleChoices.ANNUAL,
            licensed_quantity=5,
        )
        return SubscriptionAssignment.objects.create(
            subscription=subscription,
            content_type=ContentType.objects.get_for_model(aggregate.model),
            object_id=item.pk,
        )


class InventoryItemArchiveServiceTests(InventoryItemArchiveFixtureMixin, TenantTestMixin, TestCase):
    """``archive_<item>`` / ``restore_<item>`` for each of the three families."""

    def setUp(self):
        self.setup_tenant_context(name="P619d Tenant", slug="p619d-tenant")
        self.site = Site.objects.create(name="P619d Site", slug="p619d-site", tenant=self.tenant)

    def archive(self, aggregate, item, actor=None):
        archive_handler, _restore = HANDLERS[aggregate.model._meta.label_lower]
        with self.tenant_context(self.tenant), acting_as(actor or self.tenant_admin):
            return archive_handler(item)

    def restore(self, aggregate, item, actor=None):
        _archive, restore_handler = HANDLERS[aggregate.model._meta.label_lower]
        with self.tenant_context(self.tenant), acting_as(actor or self.tenant_admin):
            return restore_handler(item)

    # ------------------------------------------------------------- happy path
    def test_archive_moves_the_empty_stock_with_the_item_and_audits_every_row(self):
        for aggregate in (COMPONENT, ACCESSORY, CONSUMABLE):
            with self.subTest(aggregate=aggregate.noun):
                item = self.make_item(aggregate, f"P619d Item {aggregate.noun}")
                stock = self.make_stock(aggregate, item, qty=0)

                result = self.archive(aggregate, item)

                self.assertEqual(result.archived, 2)
                self.assertIsNotNone(result.operation_id)
                item.refresh_from_db()
                self.assertIsNotNone(item.deleted_at)
                self.assertFalse(aggregate.model.objects.filter(pk=item.pk).exists())
                self.assertTrue(aggregate.model.all_objects.filter(pk=item.pk).exists())
                self.assertEqual(object_changes(item, action="delete").count(), 1)

                stock = aggregate.stock_model.all_objects.get(pk=stock.pk)
                self.assertIsNotNone(stock.deleted_at)
                self.assertEqual(stock.archive_operation_id, result.operation_id)
                self.assertEqual(object_changes(stock, action="delete").count(), 1)

    def test_archive_detaches_open_requests_and_ends_subscriptions(self):
        for aggregate in (COMPONENT, ACCESSORY, CONSUMABLE):
            with self.subTest(aggregate=aggregate.noun):
                item = self.make_item(aggregate, f"P619d Detach {aggregate.noun}")
                request = self.make_open_request(aggregate, item)
                assignment = self.make_subscription_assignment(aggregate, item)

                result = self.archive(aggregate, item)

                self.assertEqual(result.detached, 2)
                request.refresh_from_db()
                self.assertEqual(request.status, RequestStatusChoices.CANCELLED)
                self.assertEqual(getattr(request, f"{aggregate.item_attr}_id"), item.pk)
                self.assertEqual(object_changes(request, action="update").count(), 1)
                self.assertIsNotNone(SubscriptionAssignment.all_objects.get(pk=assignment.pk).deleted_at)

    def test_archive_keeps_the_evidence_and_reports_it(self):
        aggregate = COMPONENT
        item = self.make_item(aggregate, "P619d Keep")
        self.make_open_request(aggregate, item, status=RequestStatusChoices.FULFILLED)
        item.journal_entries.create(comment="P619d note", user=self.tenant_admin)

        result = self.archive(aggregate, item)

        self.assertEqual(result.kept, 2)
        self.assertEqual(object_changes(item, action="delete").count(), 1)

    def test_archive_is_idempotent_and_audits_once(self):
        aggregate = ACCESSORY
        item = self.make_item(aggregate, "P619d Idempotent")
        self.make_stock(aggregate, item, qty=0)

        self.archive(aggregate, item)
        second = self.archive(aggregate, item)

        self.assertEqual(second.archived, 0)
        self.assertEqual(object_changes(item, action="delete").count(), 1)

    # ------------------------------------------------------------- restore
    def test_restore_brings_back_the_item_and_the_archived_stock(self):
        for aggregate in (COMPONENT, ACCESSORY, CONSUMABLE):
            with self.subTest(aggregate=aggregate.noun):
                item = self.make_item(aggregate, f"P619d Restore {aggregate.noun}")
                stock = self.make_stock(aggregate, item, qty=0)

                self.archive(aggregate, item)
                self.restore(aggregate, item)

                item.refresh_from_db()
                self.assertIsNone(item.deleted_at)
                self.assertTrue(aggregate.model.objects.filter(pk=item.pk).exists())
                stock.refresh_from_db()
                self.assertIsNone(stock.deleted_at)
                self.assertIsNone(stock.archive_operation_id)

    def test_restore_does_not_resurrect_a_stock_row_deleted_on_its_own(self):
        aggregate = CONSUMABLE
        item = self.make_item(aggregate, "P619d Leaf")
        archived = self.make_stock(aggregate, item, qty=0)
        leaf = self.make_stock(aggregate, item, qty=0)
        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            leaf.delete()

        self.archive(aggregate, item)
        self.restore(aggregate, item)

        self.assertIsNone(aggregate.stock_model.all_objects.get(pk=archived.pk).deleted_at)
        self.assertIsNotNone(aggregate.stock_model.all_objects.get(pk=leaf.pk).deleted_at)

    def test_restore_refuses_when_another_active_item_uses_the_slug(self):
        aggregate = COMPONENT
        item = self.make_item(aggregate, "P619d Slug", slug="p619d-slug")
        stock = self.make_stock(aggregate, item, qty=0)
        self.archive(aggregate, item)
        self.make_item(aggregate, "P619d Slug Other", slug="p619d-slug")

        with self.assertRaises(ArchiveBlocked) as caught:
            self.restore(aggregate, item)

        self.assertIn("p619d-slug", caught.exception.headline)
        item.refresh_from_db()
        self.assertIsNotNone(item.deleted_at)
        stock.refresh_from_db()
        self.assertIsNotNone(stock.deleted_at)

    # ------------------------------------------------------------- refusal
    def test_open_assignment_refuses_without_writing(self):
        for aggregate, assignment_model in FAMILIES:
            with self.subTest(aggregate=aggregate.noun):
                item = self.make_item(aggregate, f"P619d Open {aggregate.noun}")
                stock = self.make_stock(aggregate, item, qty=0)
                self.make_assignment(aggregate, assignment_model, item, self.make_holder())

                with self.assertRaises(ArchiveBlocked) as caught:
                    self.archive(aggregate, item)

                self.assertIn("open assignments", repr(caught.exception.blockers))
                item.refresh_from_db()
                self.assertIsNone(item.deleted_at)
                stock.refresh_from_db()
                self.assertIsNone(stock.deleted_at)
                self.assertEqual(object_changes(item).count(), 0)

    def test_stock_with_quantity_refuses(self):
        for aggregate in (COMPONENT, ACCESSORY, CONSUMABLE):
            with self.subTest(aggregate=aggregate.noun):
                item = self.make_item(aggregate, f"P619d Qty {aggregate.noun}")
                stock = self.make_stock(aggregate, item, qty=3)

                with self.assertRaises(ArchiveBlocked) as caught:
                    self.archive(aggregate, item)

                self.assertIn("stock rows with remaining", repr(caught.exception.blockers))
                stock.refresh_from_db()
                self.assertIsNone(stock.deleted_at)

    def test_live_kit_item_refuses(self):
        for aggregate in (COMPONENT, ACCESSORY, CONSUMABLE):
            with self.subTest(aggregate=aggregate.noun):
                item = self.make_item(aggregate, f"P619d Kit {aggregate.noun}")
                kit_item = self.make_kit_item(aggregate, item)

                with self.assertRaises(ArchiveBlocked) as caught:
                    self.archive(aggregate, item)

                self.assertIn("live kit items", repr(caught.exception.blockers))
                kit_item.refresh_from_db()
                self.assertIsNone(kit_item.deleted_at)

    # ------------------------------------------------------------- atomicity
    def test_archive_is_atomic_when_a_later_step_fails(self):
        aggregate = COMPONENT
        item = self.make_item(aggregate, "P619d Atomic")
        stock = self.make_stock(aggregate, item, qty=0)

        with patch(
            "inventory.item_archive_services._kept_evidence_count",
            side_effect=RuntimeError("archive failed"),
        ):
            with self.assertRaises(RuntimeError):
                self.archive(aggregate, item)

        item.refresh_from_db()
        self.assertIsNone(item.deleted_at)
        stock.refresh_from_db()
        self.assertIsNone(stock.deleted_at)
        self.assertIsNone(stock.archive_operation_id)
        self.assertEqual(object_changes(item).count(), 0)
        self.assertEqual(object_changes(stock).count(), 0)

    # ------------------------------------------------------------- tenant boundary
    def test_archive_fails_closed_for_an_item_outside_the_active_tenant(self):
        aggregate = COMPONENT
        other = Tenant.objects.create(name="P619d Other", slug="p619d-other")
        foreign = aggregate.model.objects.create(
            name="P619d Foreign",
            manufacturer=Manufacturer.objects.create(name="Mfg Foreign", slug=f"mfg-{uuid4().hex[:8]}"),
            tenant=other,
        )

        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            with self.assertRaises(PermissionDenied):
                HANDLERS[aggregate.model._meta.label_lower][0](foreign)

        foreign.refresh_from_db()
        self.assertIsNone(foreign.deleted_at)


class InventoryItemArchiveSurfaceTests(InventoryItemArchiveFixtureMixin, TenantTestMixin, TestCase):
    """The delete view and the recycle-bin restore reach the aggregate service."""

    ITEM_PERMISSIONS = [
        "inventory.view_component",
        "inventory.delete_component",
        "inventory.view_accessory",
        "inventory.delete_accessory",
        "inventory.view_consumable",
        "inventory.delete_consumable",
        "core.view_recyclebin",
        "core.change_recyclebin",
    ]

    def setUp(self):
        self.setup_tenant_context(
            name="P619d Surface",
            slug="p619d-surface",
            permissions=list(self.ITEM_PERMISSIONS),
        )
        self.site = Site.objects.create(name="P619d Surface Site", slug="p619d-surface-site", tenant=self.tenant)
        self.client.force_login(self.tenant_admin)

    def test_ui_delete_archives_each_item_family(self):
        cases = (
            (COMPONENT, "component_delete"),
            (ACCESSORY, "accessory_delete"),
            (CONSUMABLE, "consumable_delete"),
        )
        for aggregate, url_name in cases:
            with self.subTest(aggregate=aggregate.noun):
                item = self.make_item(aggregate, f"P619d UI {aggregate.noun}")
                stock = self.make_stock(aggregate, item, qty=0)

                response = self.client.post(reverse(f"inventory:{url_name}", kwargs={"pk": item.pk}))

                self.assertEqual(response.status_code, 302)
                self.assertIsNotNone(aggregate.model.all_objects.get(pk=item.pk).deleted_at)
                self.assertIsNotNone(aggregate.stock_model.all_objects.get(pk=stock.pk).deleted_at)

    def test_recycle_bin_restore_uses_the_service(self):
        aggregate = COMPONENT
        item = self.make_item(aggregate, "P619d Recycle")
        stock = self.make_stock(aggregate, item, qty=0)
        with self.tenant_context(self.tenant), acting_as(self.tenant_admin):
            HANDLERS[aggregate.model._meta.label_lower][0](item)
        content_type = ContentType.objects.get_for_model(aggregate.model)

        response = self.client.post(
            reverse("object_restore", kwargs={"content_type_id": content_type.pk, "object_id": item.pk})
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(aggregate.model.all_objects.get(pk=item.pk).deleted_at)
        self.assertIsNone(aggregate.stock_model.all_objects.get(pk=stock.pk).deleted_at)
