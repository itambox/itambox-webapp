"""Regression tests for the inventory stock-count multi-join fan-out bug.

Annotating two Sum() over two different multi-valued reverse relations in a
single .annotate() (e.g. Sum('stocks__qty') + Sum('assignments__qty')) builds
two independent LEFT JOINs that cross-join, so each Sum is multiplied by the
OTHER relation's row count. The fix replaces them with independent correlated
Subquery annotations via Accessory/Consumable.objects.with_counts(), used by
both the API viewsets and the HTML list views.
"""

from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from assets.models import Manufacturer
from core.tasks.alerts import _match_low_stock
from core.tests.mixins import TenantTestMixin
from extras.dashboard.widgets import LowStockWidget
from inventory.api.serializers import AccessorySerializer, ComponentSerializer, ConsumableSerializer
from inventory.api.views import AccessoryViewSet, ComponentViewSet, ConsumableViewSet
from inventory.models import (
    Accessory,
    AccessoryAssignment,
    AccessoryStock,
    Component,
    ComponentStock,
    Consumable,
    ConsumableAssignment,
    ConsumableStock,
)
from inventory.services import checkin_component, checkout_inventory_item, create_component_allocation
from inventory.tests.factories import create_assignment_fixture
from inventory.views.accessory_views import AccessoryListView
from inventory.views.component_views import ComponentListView
from inventory.views.consumable_views import ConsumableListView
from organization.models import AssetHolder, Location, Site, Tenant

User = get_user_model()


class AccessoryStockFanoutTests(TestCase):
    """An accessory with 2 stock rows and 2 assignments must not inflate either
    aggregate by the other relation's row count."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant Fanout Acc", slug="tenant-fanout-acc")
        self.manufacturer = Manufacturer.objects.create(name="Logitech", slug="logitech")
        self.site = Site.objects.create(name="Warehouse", slug="warehouse", tenant=self.tenant)
        self.loc_a = Location.objects.create(name="Shelf A", slug="shelf-a", site=self.site, tenant=self.tenant)
        self.loc_b = Location.objects.create(name="Shelf B", slug="shelf-b", site=self.site, tenant=self.tenant)
        self.holder1 = AssetHolder.objects.create(first_name="Jane", last_name="Doe", upn="jane.doe")
        self.holder2 = AssetHolder.objects.create(first_name="John", last_name="Roe", upn="john.roe")

        self.accessory = Accessory.objects.create(name="MX Keys", manufacturer=self.manufacturer)

        # 2 stock rows at two locations: 10 + 20 = 30 total stock.
        AccessoryStock.objects.create(accessory=self.accessory, location=self.loc_a, qty=10)
        AccessoryStock.objects.create(accessory=self.accessory, location=self.loc_b, qty=20)

        # 2 assignments (no from_location => stock untouched): 3 + 4 = 7 checked out.
        create_assignment_fixture(AccessoryAssignment, accessory=self.accessory, assigned_holder=self.holder1, qty=3)
        create_assignment_fixture(AccessoryAssignment, accessory=self.accessory, assigned_holder=self.holder2, qty=4)

    def test_with_counts_annotation_not_inflated(self):
        acc = Accessory.objects.with_counts().get(pk=self.accessory.pk)
        # Without the fix these would be 30*2=60 and 7*2=14.
        self.assertEqual(acc.total_stock, 30)
        self.assertEqual(acc.checked_out_qty, 7)
        self.assertEqual(acc._total_stock, 30)
        self.assertEqual(acc._checked_out, 7)
        # available = total_stock - undeducted (assignments w/o from_location) = 30 - 7
        self.assertEqual(acc.available, 23)

    def test_api_serializer_counts_correct(self):
        qs = AccessoryViewSet.queryset
        acc = qs.get(pk=self.accessory.pk)
        data = AccessorySerializer(acc).data
        self.assertEqual(data["total_stock"], 30)
        self.assertEqual(data["checked_out_qty"], 7)
        self.assertEqual(data["available"], 23)

    def test_list_view_context_counts_correct(self):
        request = RequestFactory().get("/inventory/accessories/")
        request.user = User.objects.create_user(username="admin", password="pw", is_staff=True, is_superuser=True)
        view = AccessoryListView()
        view.setup(request)
        obj = view.get_queryset().get(pk=self.accessory.pk)
        self.assertEqual(obj.total_stock, 30)
        self.assertEqual(obj.checked_out_qty, 7)
        self.assertEqual(obj.available, 23)


class ConsumableStockFanoutTests(TestCase):
    """Consumable has the same bug across stocks x consumptions."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant Fanout Con", slug="tenant-fanout-con")
        self.manufacturer = Manufacturer.objects.create(name="HP", slug="hp")
        self.site = Site.objects.create(name="Depot", slug="depot", tenant=self.tenant)
        self.loc_a = Location.objects.create(name="Bin A", slug="bin-a", site=self.site, tenant=self.tenant)
        self.loc_b = Location.objects.create(name="Bin B", slug="bin-b", site=self.site, tenant=self.tenant)
        self.holder1 = AssetHolder.objects.create(first_name="Amy", last_name="Lee", upn="amy.lee")
        self.holder2 = AssetHolder.objects.create(first_name="Bob", last_name="Kim", upn="bob.kim")

        self.consumable = Consumable.objects.create(name="Toner 26A", manufacturer=self.manufacturer)

        ConsumableStock.objects.create(consumable=self.consumable, location=self.loc_a, qty=10)
        ConsumableStock.objects.create(consumable=self.consumable, location=self.loc_b, qty=20)

        create_assignment_fixture(ConsumableAssignment, consumable=self.consumable, assigned_holder=self.holder1, qty=3)
        create_assignment_fixture(ConsumableAssignment, consumable=self.consumable, assigned_holder=self.holder2, qty=4)

    def test_with_counts_annotation_not_inflated(self):
        con = Consumable.objects.with_counts().get(pk=self.consumable.pk)
        self.assertEqual(con.total_stock, 30)
        self.assertEqual(con.consumed_qty, 7)
        self.assertEqual(con._total_stock, 30)
        self.assertEqual(con._consumed, 7)
        self.assertEqual(con.available, 23)

    def test_api_serializer_counts_correct(self):
        qs = ConsumableViewSet.queryset
        con = qs.get(pk=self.consumable.pk)
        data = ConsumableSerializer(con).data
        self.assertEqual(data["total_stock"], 30)
        self.assertEqual(data["consumed_qty"], 7)
        self.assertEqual(data["available"], 23)

    def test_list_view_context_counts_correct(self):
        request = RequestFactory().get("/inventory/consumables/")
        request.user = User.objects.create_user(username="admin", password="pw", is_staff=True, is_superuser=True)
        view = ConsumableListView()
        view.setup(request)
        obj = view.get_queryset().get(pk=self.consumable.pk)
        self.assertEqual(obj.total_stock, 30)
        self.assertEqual(obj.consumed_qty, 7)
        self.assertEqual(obj.available, 23)


class ComponentStockFanoutTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant Fanout Component", slug="tenant-fanout-component")
        self.user = User.objects.create_superuser(username="component-fanout", password="x")
        self.set_active_tenant(self.tenant)
        self.manufacturer = Manufacturer.objects.create(name="Kingston", slug="kingston-fanout")
        self.site = Site.objects.create(name="Component Depot", slug="component-depot", tenant=self.tenant)
        self.loc_a = Location.objects.create(
            name="Component Bin A", slug="component-bin-a", site=self.site, tenant=self.tenant
        )
        self.loc_b = Location.objects.create(
            name="Component Bin B", slug="component-bin-b", site=self.site, tenant=self.tenant
        )
        self.holder = AssetHolder.objects.create(
            first_name="Component",
            last_name="Holder",
            upn="component-holder@example.test",
            tenant=self.tenant,
        )
        self.component = Component.objects.create(
            name="DDR5 Kit",
            manufacturer=self.manufacturer,
            tenant=self.tenant,
        )
        self.stock_a = ComponentStock.objects.create(component=self.component, location=self.loc_a, qty=10)
        self.stock_b = ComponentStock.objects.create(component=self.component, location=self.loc_b, qty=20)

    def tearDown(self):
        self.clear_tenant_context()
        super().tearDown()

    def test_target_only_allocation_reduces_availability_without_stock_mutation(self):
        create_component_allocation(self.component, 7, holder=self.holder, user=self.user)

        self.stock_a.refresh_from_db()
        self.stock_b.refresh_from_db()
        self.assertEqual((self.stock_a.qty, self.stock_b.qty), (10, 20))
        self.component.refresh_from_db()
        self.assertEqual(self.component.total_allocated, 7)
        self.assertEqual(self.component.available_stock, 23)

    def test_source_backed_checkout_reduces_availability_once_across_surfaces(self):
        checkout_inventory_item(
            self.component,
            7,
            holder=self.holder,
            source_location=self.loc_a,
            user=self.user,
        )

        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.qty, 3)
        self.component.refresh_from_db()
        self.assertEqual(self.component.total_stock, 23)
        self.assertEqual(self.component.total_allocated, 7)
        self.assertEqual(self.component.available_stock, 23)

        annotated = Component.objects.with_counts().get(pk=self.component.pk)
        self.assertEqual(annotated.total_stock, 23)
        self.assertEqual(annotated.total_allocated, 7)
        self.assertEqual(annotated.available_stock, 23)
        request = RequestFactory().get("/inventory/components/")
        request.user = self.user
        self.assertEqual(
            ComponentSerializer(annotated, context={"request": request}).data["available_stock"],
            23,
        )

        view = ComponentListView()
        view.setup(request)
        listed = view.get_queryset().get(pk=self.component.pk)
        self.assertEqual(listed.available_stock, 23)

    def test_source_backed_checkin_restores_stock_and_availability(self):
        allocation = checkout_inventory_item(
            self.component,
            7,
            holder=self.holder,
            source_location=self.loc_a,
            user=self.user,
        )
        checkin_component(allocation.pk, user=self.user)

        self.stock_a.refresh_from_db()
        self.component.refresh_from_db()
        self.assertEqual(self.stock_a.qty, 10)
        self.assertEqual(self.component.total_stock, 30)
        self.assertEqual(self.component.total_allocated, 0)
        self.assertEqual(self.component.available_stock, 30)

    def test_component_api_queryset_uses_correct_availability_annotation(self):
        checkout_inventory_item(
            self.component,
            7,
            holder=self.holder,
            source_location=self.loc_a,
            user=self.user,
        )

        request = RequestFactory().get("/api/inventory/components/")
        request.user = self.user
        serialized = ComponentSerializer(
            ComponentViewSet.queryset.get(pk=self.component.pk),
            context={"request": request},
        ).data
        self.assertEqual(serialized["total_stock"], 23)
        self.assertEqual(serialized["total_allocated"], 7)
        self.assertEqual(serialized["available_stock"], 23)

    def test_source_backed_checkout_does_not_trigger_false_low_stock_surfaces(self):
        self.component.min_qty = 20
        self.component.save(update_fields=["min_qty"])
        checkout_inventory_item(
            self.component,
            7,
            holder=self.holder,
            source_location=self.loc_a,
            user=self.user,
        )

        alert_matches = _match_low_stock(SimpleNamespace(tenant=None, threshold_value=2))
        self.assertNotIn(self.component.pk, [match["obj"].pk for match in alert_matches])

        request = RequestFactory().get("/dashboard/")
        request.user = self.user
        widget_rows = LowStockWidget()._low_stock_components(request, target_id=None)
        self.assertNotIn(self.component.pk, [row["component"].pk for row in widget_rows])

        self.component.min_qty = 24
        self.component.save(update_fields=["min_qty"])
        matching_alerts = [
            match
            for match in _match_low_stock(SimpleNamespace(tenant=None, threshold_value=24))
            if match["obj"].pk == self.component.pk
        ]
        self.assertEqual(len(matching_alerts), 1)
        self.assertIn("23", matching_alerts[0]["message"])
