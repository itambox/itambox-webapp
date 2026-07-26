import json

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from assets.models import Category, Manufacturer
from core.tests.mixins import TenantTestMixin
from inventory.models import (
    Accessory,
    AccessoryStock,
    Component,
    ComponentStock,
    Consumable,
    ConsumableStock,
)
from inventory.views import (
    AccessoryStockAdjustView,
    AccessoryStockCreateModalView,
    ComponentStockAdjustView,
    ComponentStockCreateModalView,
    ConsumableStockAdjustView,
    ConsumableStockCreateModalView,
)
from inventory.views.stock_actions import StockAdjustView, StockCreateModalView
from itambox.views.generic.authorization import SecuredObjectActionMixin
from organization.models import Location, Site, Tenant

User = get_user_model()


class StockActionPermissionTests(TenantTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Tenant.objects.create(name="Stock Owner", slug="stock-owner")
        cls.other = Tenant.objects.create(name="Other Stock Owner", slug="other-stock-owner")
        cls.owner_site = Site.objects.create(
            name="Owner Site",
            slug="owner-site",
            tenant=cls.owner,
        )
        cls.owner_location = Location.objects.create(
            name="Owner Stock",
            slug="owner-stock",
            site=cls.owner_site,
            tenant=cls.owner,
        )
        cls.create_location = Location.objects.create(
            name="Owner New Stock",
            slug="owner-new-stock",
            site=cls.owner_site,
            tenant=cls.owner,
        )
        cls.other_site = Site.objects.create(
            name="Other Site",
            slug="other-site",
            tenant=cls.other,
        )
        cls.other_location = Location.objects.create(
            name="Other Stock",
            slug="other-stock",
            site=cls.other_site,
            tenant=cls.other,
        )
        manufacturer = Manufacturer.objects.create(
            name="Stock Actions Manufacturer",
            slug="stock-actions-manufacturer",
        )
        categories = {
            "accessory": Category.objects.create(
                name="Stock Actions Accessory",
                slug="stock-actions-accessory",
                applies_to={"accessory": True},
            ),
            "consumable": Category.objects.create(
                name="Stock Actions Consumable",
                slug="stock-actions-consumable",
                applies_to={"consumable": True},
            ),
            "component": Category.objects.create(
                name="Stock Actions Component",
                slug="stock-actions-component",
                applies_to={"component": True},
            ),
        }
        cls.items = {}
        cls.stocks = {}
        cls.other_items = {}
        cls.other_stocks = {}
        definitions = (
            ("accessory", Accessory, AccessoryStock, "accessory"),
            ("consumable", Consumable, ConsumableStock, "consumable"),
            ("component", Component, ComponentStock, "component"),
        )
        for name, item_model, stock_model, item_field in definitions:
            item = item_model.objects.create(
                name=f"Owner {name}",
                manufacturer=manufacturer,
                category=categories[name],
                tenant=cls.owner,
            )
            stock = stock_model.objects.create(
                **{item_field: item},
                location=cls.owner_location,
                qty=5,
            )
            other_item = item_model.objects.create(
                name=f"Other {name}",
                manufacturer=manufacturer,
                category=categories[name],
                tenant=cls.other,
            )
            other_stock = stock_model.objects.create(
                **{item_field: other_item},
                location=cls.other_location,
                qty=7,
            )
            cls.items[name] = item
            cls.stocks[name] = stock
            cls.other_items[name] = other_item
            cls.other_stocks[name] = other_stock

    def setUp(self):
        self.families = (
            {
                "name": "accessory",
                "stock_model": AccessoryStock,
                "item_field": "accessory",
                "create_url": "inventory:accessory_add_stock",
                "adjust_url": "inventory:accessorystock_adjust",
                "add_perm": "inventory.add_accessorystock",
                "change_perm": "inventory.change_accessorystock",
                "create_view": AccessoryStockCreateModalView,
                "adjust_view": AccessoryStockAdjustView,
            },
            {
                "name": "consumable",
                "stock_model": ConsumableStock,
                "item_field": "consumable",
                "create_url": "inventory:consumable_add_stock",
                "adjust_url": "inventory:consumablestock_adjust",
                "add_perm": "inventory.add_consumablestock",
                "change_perm": "inventory.change_consumablestock",
                "create_view": ConsumableStockCreateModalView,
                "adjust_view": ConsumableStockAdjustView,
            },
            {
                "name": "component",
                "stock_model": ComponentStock,
                "item_field": "component",
                "create_url": "inventory:component_add_stock",
                "adjust_url": "inventory:componentstock_adjust",
                "add_perm": "inventory.add_componentstock",
                "change_perm": "inventory.change_componentstock",
                "create_view": ComponentStockCreateModalView,
                "adjust_view": ComponentStockAdjustView,
            },
        )

    def _login(self, permissions):
        user = User.objects.create_user(
            username=f"stock-user-{User.objects.count()}",
            password="password",
        )
        self.client_login_to_tenant(user, self.owner, role_permissions=permissions)
        return user

    def _create_url(self, family, item=None):
        item = item or self.items[family["name"]]
        return reverse(family["create_url"], kwargs={"pk": item.pk})

    def _adjust_url(self, family, stock=None, action="increment"):
        stock = stock or self.stocks[family["name"]]
        return reverse(family["adjust_url"], kwargs={"pk": stock.pk}) + f"?action={action}"

    def test_views_declare_explicit_model_permissions(self):
        for family in self.families:
            with self.subTest(family=family["name"], action="create"):
                self.assertTrue(issubclass(family["create_view"], PermissionRequiredMixin))
                self.assertEqual(family["create_view"].permission_required, family["add_perm"])
            with self.subTest(family=family["name"], action="adjust"):
                self.assertTrue(issubclass(family["adjust_view"], PermissionRequiredMixin))
                self.assertEqual(family["adjust_view"].permission_required, family["change_perm"])

    def test_six_views_share_the_secured_stock_action_implementations(self):
        for family in self.families:
            create_view = family["create_view"]
            adjust_view = family["adjust_view"]
            with self.subTest(family=family["name"], action="create"):
                self.assertTrue(issubclass(create_view, StockCreateModalView))
                self.assertIn(SecuredObjectActionMixin, create_view.__mro__)
                self.assertIs(create_view.get_permission_required, SecuredObjectActionMixin.get_permission_required)
                self.assertIs(create_view.has_permission, SecuredObjectActionMixin.has_permission)
                self.assertIs(create_view.get_queryset, SecuredObjectActionMixin.get_queryset)
                self.assertIs(create_view.get_object, SecuredObjectActionMixin.get_object)
                self.assertIs(create_view.get, StockCreateModalView.get)
                self.assertIs(create_view.post, StockCreateModalView.post)
            with self.subTest(family=family["name"], action="adjust"):
                self.assertTrue(issubclass(adjust_view, StockAdjustView))
                self.assertIn(SecuredObjectActionMixin, adjust_view.__mro__)
                self.assertIs(adjust_view.get_permission_required, SecuredObjectActionMixin.get_permission_required)
                self.assertIs(adjust_view.has_permission, SecuredObjectActionMixin.has_permission)
                self.assertIs(adjust_view.get_queryset, SecuredObjectActionMixin.get_queryset)
                self.assertIs(adjust_view.get_object, SecuredObjectActionMixin.get_object)
                self.assertIs(adjust_view.get_locked_object, StockAdjustView.get_locked_object)
                self.assertIs(adjust_view.post, StockAdjustView.post)

    def test_anonymous_create_and_adjust_requests_are_rejected(self):
        for family in self.families:
            with self.subTest(family=family["name"], action="create"):
                response = self.client.post(
                    self._create_url(family),
                    {"location": self.create_location.pk, "qty": 1},
                )
                self.assertEqual(response.status_code, 302)
            with self.subTest(family=family["name"], action="adjust"):
                response = self.client.post(self._adjust_url(family))
                self.assertEqual(response.status_code, 302)

    def test_authorized_create_get_renders_the_existing_prefilled_modal_contract(self):
        self._login([family["add_perm"] for family in self.families])
        for family in self.families:
            with self.subTest(family=family["name"]):
                response = self.client.get(
                    self._create_url(family),
                    {"location": self.create_location.pk},
                )
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "generic/includes/add_stock_modal.html")
                self.assertEqual(response.context["object"], self.items[family["name"]])
                self.assertEqual(response.context["post_url"], self._create_url(family))
                self.assertEqual(response.context["form"].initial["location"], str(self.create_location.pk))

    def test_authenticated_user_without_permissions_cannot_mutate_stock(self):
        self._login([])
        for family in self.families:
            stock = self.stocks[family["name"]]
            before_count = family["stock_model"]._base_manager.count()
            with self.subTest(family=family["name"], action="create"):
                response = self.client.post(
                    self._create_url(family),
                    {"location": self.create_location.pk, "qty": 2},
                )
                self.assertEqual(response.status_code, 403)
                self.assertEqual(family["stock_model"]._base_manager.count(), before_count)
            with self.subTest(family=family["name"], action="adjust"):
                response = self.client.post(self._adjust_url(family))
                self.assertEqual(response.status_code, 403)
                stock.refresh_from_db()
                self.assertEqual(stock.qty, 5)

    def test_authorized_same_tenant_create_and_adjust_preserve_htmx_contract(self):
        permissions = [
            permission for family in self.families for permission in (family["add_perm"], family["change_perm"])
        ]
        self._login(permissions)
        for family in self.families:
            with self.subTest(family=family["name"], action="create"):
                response = self.client.post(
                    self._create_url(family),
                    {"location": self.create_location.pk, "qty": 3},
                    HTTP_HX_REQUEST="true",
                )
                self.assertEqual(response.status_code, 204)
                triggers = json.loads(response["HX-Trigger"])
                self.assertEqual(
                    set(triggers),
                    {"closeModalEvent", "tableRefreshRequired", "showMessage"},
                )
                self.assertTrue(
                    family["stock_model"]
                    ._base_manager.filter(
                        **{
                            family["item_field"]: self.items[family["name"]],
                            "location": self.create_location,
                            "qty": 3,
                        }
                    )
                    .exists()
                )
            with self.subTest(family=family["name"], action="adjust"):
                stock = self.stocks[family["name"]]
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.post(
                        self._adjust_url(family),
                        HTTP_HX_REQUEST="true",
                    )
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("HX-Trigger", response)
                stock.refresh_from_db()
                self.assertEqual(stock.qty, 6)
                adjust_url = reverse(family["adjust_url"], kwargs={"pk": stock.pk})
                self.assertContains(response, f'hx-post="{adjust_url}?action=decrement"')
                self.assertContains(response, f'hx-post="{adjust_url}?action=increment"')
                self.assertContains(response, ">6</span>")
                locking_queries = [
                    query["sql"]
                    for query in queries.captured_queries
                    if "FOR UPDATE" in query["sql"].upper() and family["stock_model"]._meta.db_table in query["sql"]
                ]
                self.assertTrue(locking_queries, "stock adjustment must retain its SELECT FOR UPDATE row lock")

    def test_authorized_non_htmx_create_preserves_detail_redirect(self):
        self._login([family["add_perm"] for family in self.families])
        for family in self.families:
            with self.subTest(family=family["name"]):
                response = self.client.post(
                    self._create_url(family),
                    {"location": self.create_location.pk, "qty": 4},
                )
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], self.items[family["name"]].get_absolute_url())

    def test_cross_tenant_item_location_and_stock_identifiers_fail_closed(self):
        permissions = [
            permission for family in self.families for permission in (family["add_perm"], family["change_perm"])
        ]
        self._login(permissions)
        for family in self.families:
            before_count = family["stock_model"]._base_manager.count()
            with self.subTest(family=family["name"], identifier="item"):
                response = self.client.post(
                    self._create_url(family, self.other_items[family["name"]]),
                    {"location": self.owner_location.pk, "qty": 2},
                )
                self.assertEqual(response.status_code, 404)
            with self.subTest(family=family["name"], identifier="location"):
                response = self.client.post(
                    self._create_url(family),
                    {"location": self.other_location.pk, "qty": 2},
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Select a valid choice")
            self.assertEqual(family["stock_model"]._base_manager.count(), before_count)
            with self.subTest(family=family["name"], identifier="stock"):
                other_stock = self.other_stocks[family["name"]]
                response = self.client.post(self._adjust_url(family, other_stock))
                self.assertEqual(response.status_code, 404)
                other_stock.refresh_from_db()
                self.assertEqual(other_stock.qty, 7)

    def test_invalid_create_quantities_and_adjust_actions_do_not_mutate_stock(self):
        permissions = [
            permission for family in self.families for permission in (family["add_perm"], family["change_perm"])
        ]
        self._login(permissions)
        invalid_quantities = (0, -1, "not-a-number")
        for family in self.families:
            for index, qty in enumerate(invalid_quantities):
                location = Location.objects.create(
                    name=f"Invalid {family['name']} {index}",
                    slug=f"invalid-{family['name']}-{index}",
                    site=self.owner_site,
                    tenant=self.owner,
                )
                before_count = family["stock_model"]._base_manager.count()
                with self.subTest(family=family["name"], qty=qty):
                    response = self.client.post(
                        self._create_url(family),
                        {"location": location.pk, "qty": qty},
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(
                        family["stock_model"]._base_manager.count(),
                        before_count,
                    )
            stock = self.stocks[family["name"]]
            with self.subTest(family=family["name"], action="invalid"):
                response = self.client.post(
                    self._adjust_url(family, action="replace"),
                    {"qty": -100},
                )
                self.assertEqual(response.status_code, 200)
                stock.refresh_from_db()
                self.assertEqual(stock.qty, 5)

            stock.qty = 0
            stock.save()
            with self.subTest(family=family["name"], action="decrement-at-zero"):
                with CaptureQueriesContext(connection) as queries:
                    response = self.client.post(self._adjust_url(family, action="decrement"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, ">0</span>")
                stock_table = family["stock_model"]._meta.db_table
                updates = [
                    query["sql"]
                    for query in queries.captured_queries
                    if query["sql"].lstrip().upper().startswith("UPDATE") and stock_table in query["sql"]
                ]
                self.assertEqual(updates, [])
                stock.refresh_from_db()
                self.assertEqual(stock.qty, 0)
