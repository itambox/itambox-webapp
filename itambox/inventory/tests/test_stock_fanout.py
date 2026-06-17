"""Regression tests for the inventory stock-count multi-join fan-out bug.

Annotating two Sum() over two different multi-valued reverse relations in a
single .annotate() (e.g. Sum('stocks__qty') + Sum('assignments__qty')) builds
two independent LEFT JOINs that cross-join, so each Sum is multiplied by the
OTHER relation's row count. The fix replaces them with independent correlated
Subquery annotations via Accessory/Consumable.objects.with_counts(), used by
both the API viewsets and the HTML list views.
"""
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model

from assets.models import Manufacturer
from organization.models import Site, Location, AssetHolder
from inventory.models import (
    Accessory, AccessoryStock, AccessoryAssignment,
    Consumable, ConsumableStock, ConsumableAssignment,
)
from inventory.api.serializers import AccessorySerializer, ConsumableSerializer
from inventory.api.views import AccessoryViewSet, ConsumableViewSet
from inventory.views.accessory_views import AccessoryListView
from inventory.views.consumable_views import ConsumableListView

User = get_user_model()


class AccessoryStockFanoutTests(TestCase):
    """An accessory with 2 stock rows and 2 assignments must not inflate either
    aggregate by the other relation's row count."""

    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Logitech', slug='logitech')
        self.site = Site.objects.create(name='Warehouse', slug='warehouse')
        self.loc_a = Location.objects.create(name='Shelf A', slug='shelf-a', site=self.site)
        self.loc_b = Location.objects.create(name='Shelf B', slug='shelf-b', site=self.site)
        self.holder1 = AssetHolder.objects.create(first_name='Jane', last_name='Doe', upn='jane.doe')
        self.holder2 = AssetHolder.objects.create(first_name='John', last_name='Roe', upn='john.roe')

        self.accessory = Accessory.objects.create(name='MX Keys', manufacturer=self.manufacturer)

        # 2 stock rows at two locations: 10 + 20 = 30 total stock.
        AccessoryStock.objects.create(accessory=self.accessory, location=self.loc_a, qty=10)
        AccessoryStock.objects.create(accessory=self.accessory, location=self.loc_b, qty=20)

        # 2 assignments (no from_location => stock untouched): 3 + 4 = 7 checked out.
        AccessoryAssignment.objects.create(accessory=self.accessory, assigned_holder=self.holder1, qty=3)
        AccessoryAssignment.objects.create(accessory=self.accessory, assigned_holder=self.holder2, qty=4)

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
        self.assertEqual(data['total_stock'], 30)
        self.assertEqual(data['checked_out_qty'], 7)
        self.assertEqual(data['available'], 23)

    def test_list_view_context_counts_correct(self):
        request = RequestFactory().get('/inventory/accessories/')
        request.user = User.objects.create_user(
            username='admin', password='pw', is_staff=True, is_superuser=True
        )
        view = AccessoryListView()
        view.setup(request)
        obj = view.get_queryset().get(pk=self.accessory.pk)
        self.assertEqual(obj.total_stock, 30)
        self.assertEqual(obj.checked_out_qty, 7)
        self.assertEqual(obj.available, 23)


class ConsumableStockFanoutTests(TestCase):
    """Consumable has the same bug across stocks x consumptions."""

    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='HP', slug='hp')
        self.site = Site.objects.create(name='Depot', slug='depot')
        self.loc_a = Location.objects.create(name='Bin A', slug='bin-a', site=self.site)
        self.loc_b = Location.objects.create(name='Bin B', slug='bin-b', site=self.site)
        self.holder1 = AssetHolder.objects.create(first_name='Amy', last_name='Lee', upn='amy.lee')
        self.holder2 = AssetHolder.objects.create(first_name='Bob', last_name='Kim', upn='bob.kim')

        self.consumable = Consumable.objects.create(name='Toner 26A', manufacturer=self.manufacturer)

        ConsumableStock.objects.create(consumable=self.consumable, location=self.loc_a, qty=10)
        ConsumableStock.objects.create(consumable=self.consumable, location=self.loc_b, qty=20)

        ConsumableAssignment.objects.create(consumable=self.consumable, assigned_holder=self.holder1, qty=3)
        ConsumableAssignment.objects.create(consumable=self.consumable, assigned_holder=self.holder2, qty=4)

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
        self.assertEqual(data['total_stock'], 30)
        self.assertEqual(data['consumed_qty'], 7)
        self.assertEqual(data['available'], 23)

    def test_list_view_context_counts_correct(self):
        request = RequestFactory().get('/inventory/consumables/')
        request.user = User.objects.create_user(
            username='admin', password='pw', is_staff=True, is_superuser=True
        )
        view = ConsumableListView()
        view.setup(request)
        obj = view.get_queryset().get(pk=self.consumable.pk)
        self.assertEqual(obj.total_stock, 30)
        self.assertEqual(obj.consumed_qty, 7)
        self.assertEqual(obj.available, 23)
