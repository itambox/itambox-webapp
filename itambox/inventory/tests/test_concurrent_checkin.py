"""Concurrency proof for inventory check-in stock restoration."""

import queue
import threading

from django.contrib.auth import get_user_model
from django.db import connections
from django.http import Http404
from django.test import TransactionTestCase

from assets.models import Category, Manufacturer
from core.managers import set_current_tenant
from inventory.models import (
    Accessory,
    AccessoryAssignment,
    AccessoryStock,
    Component,
    ComponentAllocation,
    ComponentStock,
)
from inventory.services import checkin_accessory, checkin_component, checkout_inventory_item
from organization.models import AssetHolder, Location, Site, Tenant

User = get_user_model()


class ConcurrentCheckinTests(TransactionTestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Concurrent Tenant", slug="concurrent-tenant")
        site = Site.objects.create(name="Concurrent Site", slug="concurrent-site", tenant=self.tenant)
        self.location = Location.objects.create(
            name="Concurrent Store",
            slug="concurrent-store",
            site=site,
            tenant=self.tenant,
        )
        self.holder = AssetHolder.objects.create(
            first_name="Concurrent",
            last_name="Recipient",
            upn="concurrent.recipient@example.test",
            tenant=self.tenant,
        )
        self.user = User.objects.create_superuser(username="concurrent-checkin", password="x")
        self.manufacturer = Manufacturer.objects.create(name="Concurrent Mfg", slug="concurrent-mfg")

    def _race(self, checkin, assignment, stock):
        barrier = threading.Barrier(2)
        results = queue.Queue()

        def worker():
            connections.close_all()
            tenant = Tenant._base_manager.get(pk=self.tenant.pk)
            user = User.objects.get(pk=self.user.pk)
            set_current_tenant(tenant)
            barrier.wait(timeout=10)
            try:
                checkin(assignment.pk, user=user)
            except Http404:
                results.put("missing")
            else:
                results.put("success")
            finally:
                set_current_tenant(None)
                connections.close_all()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
            self.assertFalse(thread.is_alive(), "concurrent check-in thread did not finish")

        outcomes = sorted(results.get_nowait() for _ in range(2))
        self.assertEqual(outcomes, ["missing", "success"])
        stock.refresh_from_db()
        self.assertEqual(stock.qty, 10)
        self.assertFalse(type(assignment).objects.filter(pk=assignment.pk).exists())

    def test_accessory_checkin_restores_stock_once(self):
        category = Category.objects.create(
            name="Concurrent Accessory",
            slug="concurrent-accessory",
            applies_to={"accessory": True},
        )
        item = Accessory.objects.create(
            name="Concurrent Dock",
            manufacturer=self.manufacturer,
            category=category,
            tenant=self.tenant,
        )
        stock = AccessoryStock.objects.create(accessory=item, location=self.location, qty=10)
        set_current_tenant(self.tenant)
        assignment = checkout_inventory_item(
            item,
            2,
            holder=self.holder,
            source_location=self.location,
            user=self.user,
        )
        set_current_tenant(None)
        self.assertIsInstance(assignment, AccessoryAssignment)
        self._race(checkin_accessory, assignment, stock)

    def test_component_checkin_restores_stock_once(self):
        category = Category.objects.create(
            name="Concurrent Component",
            slug="concurrent-component",
            applies_to={"component": True},
        )
        item = Component.objects.create(
            name="Concurrent RAM",
            manufacturer=self.manufacturer,
            category=category,
            tenant=self.tenant,
        )
        stock = ComponentStock.objects.create(component=item, location=self.location, qty=10)
        set_current_tenant(self.tenant)
        assignment = checkout_inventory_item(
            item,
            2,
            holder=self.holder,
            source_location=self.location,
            user=self.user,
        )
        set_current_tenant(None)
        self.assertIsInstance(assignment, ComponentAllocation)
        self._race(checkin_component, assignment, stock)
