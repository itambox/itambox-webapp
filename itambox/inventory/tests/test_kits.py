from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models.signals import post_init
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from assets.models import Asset, AssetAssignment, AssetType, Category, Manufacturer, StatusLabel
from core.tasks.context import TaskContext
from core.tests.mixins import TenantTestMixin
from inventory.models import (
    Accessory,
    AccessoryAssignment,
    AccessoryStock,
    Component,
    ComponentAllocation,
    ComponentStock,
    Consumable,
    ConsumableAssignment,
    ConsumableStock,
    Kit,
    KitItem,
)
from licenses.models import License
from organization.models import AssetHolder, Location, Site, Tenant, TenantResourceGrant
from software.models import Software

User = get_user_model()


def _create_category(name, component=False, accessory=False, consumable=False):
    applies_to = {}
    if component:
        applies_to["component"] = True
    if accessory:
        applies_to["accessory"] = True
    if consumable:
        applies_to["consumable"] = True
    slug = name.lower().replace(" ", "-")
    cat, _ = Category.objects.get_or_create(slug=slug, defaults={"name": name, "applies_to": applies_to})
    return cat


class KitModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name="Apple", slug="apple")

    def test_kit_creation(self):
        kit = Kit.objects.create(name="New Hire Kit", description="Standard equipment for new employees")
        self.assertEqual(str(kit), "New Hire Kit")
        self.assertEqual(kit.description, "Standard equipment for new employees")

    def test_kit_absolute_url(self):
        kit = Kit.objects.create(name="Developer Kit")
        url = kit.get_absolute_url()
        self.assertIn(str(kit.pk), url)

    def test_kit_name_unique(self):
        Kit.objects.create(name="Unique Kit")
        from django.db import IntegrityError

        with self.assertRaises(IntegrityError):
            Kit.objects.create(name="Unique Kit")


class KitItemModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        self.kit = Kit.objects.create(name="IT Starter Kit")
        self.software = Software.objects.create(name="Windows", manufacturer=self.manufacturer)

    def test_kit_item_asset_type(self):
        asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer, model="Latitude 5550", slug="latitude-5550"
        )
        item = KitItem.objects.create(kit=self.kit, asset_type=asset_type)
        self.assertIn("Latitude 5550", str(item))

    def test_kit_item_accessory(self):
        acc = Accessory.objects.create(name="Dock", manufacturer=self.manufacturer)
        item = KitItem.objects.create(kit=self.kit, accessory=acc, qty=2)
        self.assertIn("Dock", str(item))

    def test_kit_item_license(self):
        lic = License.objects.create(name="M365 E5", software=self.software, seats=10)
        item = KitItem.objects.create(kit=self.kit, license=lic)
        self.assertIn("Windows", str(item))

    def test_kit_item_single_target_constraint(self):
        with self.assertRaises(ValidationError):
            KitItem.objects.create(kit=self.kit, asset_type=None, accessory=None, license=None)

    def test_kit_item_clean_no_target(self):
        item = KitItem(kit=self.kit)
        with self.assertRaises(ValidationError):
            item.clean()

    def test_kit_item_clean_multiple_targets(self):
        acc = Accessory.objects.create(name="Keyboard", manufacturer=self.manufacturer)
        lic = License.objects.create(name="Test License", software=self.software, seats=5)
        item = KitItem(kit=self.kit, accessory=acc, license=lic)
        with self.assertRaises(ValidationError):
            item.clean()


class KitViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testadmin", password="testpassword", is_staff=True, is_superuser=True
        )
        self.client.login(username="testadmin", password="testpassword")
        self.kit = Kit.objects.create(name="Standard Kit", description="Basic equipment")

    def test_list_view(self):
        url = reverse("inventory:kit_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Standard Kit")

    def test_detail_view(self):
        url = reverse("inventory:kit_detail", kwargs={"pk": self.kit.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Standard Kit")

    def test_create_view_get(self):
        url = reverse("inventory:kit_create")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse("inventory:kit_create")
        response = self.client.post(
            url,
            {
                "name": "Developer Bundle",
                "description": "Laptop + monitor",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Kit.objects.filter(name="Developer Bundle").exists())

    def test_edit_view_get(self):
        url = reverse("inventory:kit_update", kwargs={"pk": self.kit.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse("inventory:kit_update", kwargs={"pk": self.kit.pk})
        response = self.client.post(
            url,
            {
                "name": "Updated Kit",
                "description": "Renamed",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.kit.refresh_from_db()
        self.assertEqual(self.kit.name, "Updated Kit")

    def test_delete_view_get(self):
        url = reverse("inventory:kit_delete", kwargs={"pk": self.kit.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_delete_view_post(self):
        url = reverse("inventory:kit_delete", kwargs={"pk": self.kit.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Kit.objects.filter(pk=self.kit.pk).exists())


class KitItemViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testadmin", password="testpassword", is_staff=True, is_superuser=True
        )
        self.client.login(username="testadmin", password="testpassword")
        self.manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        self.kit = Kit.objects.create(name="Test Kit")
        self.asset_type = AssetType.objects.create(manufacturer=self.manufacturer, model="XPS 15", slug="xps-15")

    def test_kit_item_create_view_get(self):
        url = reverse("inventory:kititem_create") + f"?kit={self.kit.pk}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_kit_item_create_view_post(self):
        url = reverse("inventory:kititem_create")
        response = self.client.post(
            url,
            {
                "kit": self.kit.pk,
                "asset_type": self.asset_type.pk,
                "qty": 1,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(KitItem.objects.filter(kit=self.kit, asset_type=self.asset_type).exists())

    def test_kit_item_delete(self):
        item = KitItem.objects.create(kit=self.kit, asset_type=self.asset_type)
        url = reverse("inventory:kititem_delete", kwargs={"pk": item.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(KitItem.objects.filter(pk=item.pk).exists())


class KitConsumableFulfillmentTests(TestCase):
    def setUp(self):
        # checkout_kit needs a deployed-type StatusLabel. One normally exists via
        # the seed migration (assets 0003), but a TransactionTestCase flush earlier
        # in the run wipes seeded rows from a reused test DB — be self-sufficient.
        StatusLabel.objects.get_or_create(
            slug="in-use",
            defaults={"name": "In Use", "type": "deployed", "color": "007bff"},
        )
        self.tenant = Tenant.objects.create(name="Tenant Kit Fulfillment", slug="tenant-kit-fulfillment")
        self.manufacturer = Manufacturer.objects.create(name="Logitech", slug="logitech")
        self.site = Site.objects.create(name="Warehouse", slug="warehouse", tenant=self.tenant)
        self.location = Location.objects.create(name="Shelf A", slug="shelf-a", site=self.site, tenant=self.tenant)
        self.holder = AssetHolder.objects.create(
            first_name="John",
            last_name="Smith",
            upn="john.smith",
            tenant=self.tenant,
        )
        self.cat_cable = _create_category("Cable", consumable=True)
        self.consumable = Consumable.objects.create(
            name="Cat6 Cable", manufacturer=self.manufacturer, category=self.cat_cable
        )
        self.stock = ConsumableStock.objects.create(consumable=self.consumable, location=self.location, qty=50)
        self.kit = Kit.objects.create(name="Developer Starter Kit")

    def test_kit_item_consumable_creation_and_fulfillment(self):
        item = KitItem.objects.create(kit=self.kit, consumable=self.consumable, qty=3)
        self.assertEqual(str(item), "3x Consumable: Logitech Cat6 Cable")

        acc = Accessory.objects.create(name="Mouse", manufacturer=self.manufacturer)
        item.accessory = acc
        with self.assertRaises(ValidationError):
            item.clean()

        item.accessory = None
        item.clean()

        from assets.services import checkout_kit

        with TaskContext(tenant_id=self.tenant.pk, user_id=None) as task_context:
            authorization = task_context.authorize_system(
                permission="inventory.add_consumableassignment",
                operation="inventory.checkout",
                reason="Fulfill an approved actorless kit checkout",
            )
            checkout_kit(
                self.kit,
                holder=self.holder,
                source_location=self.location,
                system_authorizations={
                    "inventory.add_consumableassignment": authorization,
                },
            )

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 47)

        self.assertTrue(
            ConsumableAssignment.objects.filter(
                consumable=self.consumable, assigned_holder=self.holder, from_location=self.location, qty=3
            ).exists()
        )


class KitCheckoutItemSnapshotTests(TestCase):
    """checkout_kit must plan and allocate over one locked snapshot of kit.items."""

    def setUp(self):
        self.deployable_status = StatusLabel.objects.get_or_create(
            slug="in-stock-kit-snapshot",
            defaults={"name": "In Stock (Kit Snapshot)", "type": "deployable", "color": "6c757d"},
        )[0]
        StatusLabel.objects.get_or_create(
            slug="in-use-kit-snapshot",
            defaults={"name": "In Use (Kit Snapshot)", "type": "deployed", "color": "007bff"},
        )
        self.tenant = Tenant.objects.create(name="Tenant Kit Snapshot", slug="tenant-kit-snapshot")
        self.manufacturer = Manufacturer.objects.create(name="Snapshot Vendor", slug="snapshot-vendor")
        self.site = Site.objects.create(name="Snapshot Site", slug="snapshot-site", tenant=self.tenant)
        self.location = Location.objects.create(
            name="Snapshot Shelf", slug="snapshot-shelf", site=self.site, tenant=self.tenant
        )
        self.holder = AssetHolder.objects.create(
            first_name="Sam", last_name="Snapshot", upn="sam.snapshot", tenant=self.tenant
        )
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer, model="Snapshot 14", slug="snapshot-14"
        )
        self.asset = Asset.objects.create(
            name="Snapshot Laptop",
            asset_tag="SNAP-001",
            asset_type=self.asset_type,
            status=self.deployable_status,
            tenant=self.tenant,
        )
        self.consumable = Consumable.objects.create(
            name="Snapshot Cable",
            manufacturer=self.manufacturer,
            category=_create_category("Snapshot Cable", consumable=True),
        )
        self.stock = ConsumableStock.objects.create(consumable=self.consumable, location=self.location, qty=25)
        self.kit = Kit.objects.create(name="Snapshot Kit")
        KitItem.objects.create(kit=self.kit, asset_type=self.asset_type)

    def _inject_kit_item_during_locking_pass(self):
        """Add a kit item at the moment checkout_kit locks its first asset.

        post_init on Asset fires from the body of the locking pass -- after kit.items
        has been read and before the allocation pass -- which is exactly the window a
        concurrent transaction used to be able to commit into.
        """
        injected = {}

        def inject(sender, instance, **kwargs):
            if injected:
                return
            injected["item"] = KitItem.objects.create(kit=self.kit, consumable=self.consumable, qty=4)

        post_init.connect(inject, sender=Asset, weak=False)
        self.addCleanup(post_init.disconnect, inject, sender=Asset)
        return injected

    def test_kit_item_added_mid_checkout_is_not_allocated(self):
        injected = self._inject_kit_item_during_locking_pass()

        from assets.services import checkout_kit

        with TaskContext(tenant_id=self.tenant.pk, user_id=None) as task_context:
            authorization = task_context.authorize_system(
                permission="inventory.add_consumableassignment",
                operation="inventory.checkout",
                reason="Fulfill an approved actorless kit checkout",
            )
            checkout_kit(
                self.kit,
                holder=self.holder,
                source_location=self.location,
                system_authorizations={
                    "inventory.add_consumableassignment": authorization,
                },
            )

        # The window was genuinely exercised: the row exists, it was just never allocated.
        self.assertIn("item", injected)
        self.assertTrue(KitItem.objects.filter(pk=injected["item"].pk).exists())

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 25)
        self.assertFalse(ConsumableAssignment._base_manager.filter(consumable=self.consumable).exists())

        # The item that WAS planned is still allocated.
        self.asset.refresh_from_db()
        self.assertEqual(self.asset.status.type, "deployed")
        self.assertTrue(AssetAssignment._base_manager.filter(asset=self.asset, assigned_user=self.holder).exists())

    def test_kit_items_are_read_once_under_a_row_lock(self):
        from assets.services import checkout_kit

        with TaskContext(tenant_id=self.tenant.pk, user_id=None):
            with CaptureQueriesContext(connection) as captured:
                checkout_kit(self.kit, holder=self.holder, source_location=self.location)

        item_table = KitItem._meta.db_table
        quoted_item_table = connection.ops.quote_name(item_table)
        item_reads = [
            query["sql"]
            for query in captured.captured_queries
            if query["sql"].startswith("SELECT") and quoted_item_table in query["sql"]
        ]
        self.assertEqual(len(item_reads), 1, item_reads)

        select = item_reads[0]
        self.assertIn("FOR UPDATE", select)
        quoted_pk_column = connection.ops.quote_name(KitItem._meta.pk.column)
        self.assertIn(f"ORDER BY {quoted_item_table}.{quoted_pk_column} ASC", select)
        for related in (AssetType, Accessory, License, Consumable, Component):
            self.assertIn(connection.ops.quote_name(related._meta.db_table), select)


class CrossTenantActorlessKitFulfillmentTests(TenantTestMixin, TestCase):
    def setUp(self):
        StatusLabel.objects.get_or_create(
            slug="in-use-cross-tenant-kit",
            defaults={"name": "In Use Cross Tenant Kit", "type": "deployed", "color": "007bff"},
        )
        self.owner = Tenant.objects.create(name="Kit Pool Owner", slug="kit-pool-owner")
        self.grantee = Tenant.objects.create(name="Kit Recipient", slug="kit-recipient")
        owner_site = Site.objects.create(name="Owner Site", slug="kit-owner-site", tenant=self.owner)
        self.source = Location.objects.create(
            name="Owner Warehouse",
            slug="kit-owner-warehouse",
            site=owner_site,
            tenant=self.owner,
        )
        self.holder = AssetHolder.objects.create(
            first_name="Kit",
            last_name="Recipient",
            upn="kit.recipient@xt",
            tenant=self.grantee,
        )
        manufacturer = Manufacturer.objects.create(name="Kit Vendor", slug="kit-vendor")
        accessory_category = _create_category("Kit Accessory", accessory=True)
        consumable_category = _create_category("Kit Consumable", consumable=True)
        component_category = _create_category("Kit Component", component=True)
        self.accessory = Accessory.objects.create(
            name="Kit Dock",
            manufacturer=manufacturer,
            category=accessory_category,
            tenant=self.owner,
        )
        self.consumable = Consumable.objects.create(
            name="Kit Cable",
            manufacturer=manufacturer,
            category=consumable_category,
            tenant=self.owner,
        )
        self.component = Component.objects.create(
            name="Kit RAM",
            manufacturer=manufacturer,
            category=component_category,
            tenant=self.owner,
        )
        self.stocks = (
            AccessoryStock.objects.create(accessory=self.accessory, location=self.source, qty=5),
            ConsumableStock.objects.create(consumable=self.consumable, location=self.source, qty=5),
            ComponentStock.objects.create(component=self.component, location=self.source, qty=5),
        )
        self.kit = Kit.objects.create(name="Cross-Tenant Three-Model Kit", tenant=self.grantee)
        KitItem.objects.create(kit=self.kit, accessory=self.accessory, qty=1)
        KitItem.objects.create(kit=self.kit, consumable=self.consumable, qty=1)
        KitItem.objects.create(kit=self.kit, component=self.component, qty=1)
        self.grants = []
        for stock in self.stocks:
            self.grants.append(
                TenantResourceGrant.objects.create(
                    tenant=self.owner,
                    grantee_tenant=self.grantee,
                    resource_type=ContentType.objects.get_for_model(type(stock)),
                    resource_id=stock.pk,
                    access_level=TenantResourceGrant.ACCESS_USE,
                )
            )

    def test_public_wrapper_fulfills_all_three_shared_stock_families(self):
        permissions = (
            "inventory.add_accessoryassignment",
            "inventory.add_consumableassignment",
            "inventory.add_componentallocation",
        )
        with TaskContext(tenant_id=self.grantee.pk, user_id=None) as task_context:
            authorizations = {
                permission: task_context.authorize_system(
                    permission=permission,
                    operation="inventory.checkout",
                    reason="Approved cross-tenant three-model kit fulfillment",
                )
                for permission in permissions
            }
            self.kit.checkout_to_holder(
                self.holder,
                self.source,
                system_authorizations=authorizations,
            )

        assignments = (
            AccessoryAssignment._base_manager.get(accessory=self.accessory),
            ConsumableAssignment._base_manager.get(consumable=self.consumable),
            ComponentAllocation._base_manager.get(component=self.component),
        )
        self.assertEqual(
            [assignment.resource_grant_id for assignment in assignments],
            [grant.pk for grant in self.grants],
        )
        self.assertTrue(all(assignment.target_tenant_id == self.grantee.pk for assignment in assignments))

    def test_human_request_workflow_reaches_all_three_shared_stock_families(self):
        user = User.objects.create_user(username="three-model-request-user", password="x")
        families = (
            (
                self.accessory,
                self.stocks[0],
                self.grants[0],
                AccessoryAssignment,
                "accessorystock_list",
                "accessory_checkout",
                "inventory.view_accessorystock",
                "inventory.add_accessoryassignment",
            ),
            (
                self.consumable,
                self.stocks[1],
                self.grants[1],
                ConsumableAssignment,
                "consumablestock_list",
                "consumable_checkout",
                "inventory.view_consumablestock",
                "inventory.add_consumableassignment",
            ),
            (
                self.component,
                self.stocks[2],
                self.grants[2],
                ComponentAllocation,
                "componentstock_list",
                "component_checkout",
                "inventory.view_componentstock",
                "inventory.add_componentallocation",
            ),
        )
        permissions = [permission for family in families for permission in family[6:8]]
        self.client_login_to_tenant(user, self.grantee, role_permissions=permissions)

        for item, _stock, grant_row, assignment_model, list_name, checkout_name, _view_perm, _add_perm in families:
            with self.subTest(family=item.__class__.__name__):
                checkout_url = reverse(f"inventory:{checkout_name}", kwargs={"pk": item.pk})
                listing = self.client.get(reverse(f"inventory:{list_name}"))
                self.assertEqual(listing.status_code, 200)
                self.assertContains(listing, f"{checkout_url}?from_location={self.source.pk}")
                modal = self.client.get(
                    f"{checkout_url}?from_location={self.source.pk}",
                    HTTP_HX_REQUEST="true",
                )
                self.assertEqual(modal.status_code, 200)
                response = self.client.post(
                    checkout_url,
                    {
                        "from_location": self.source.pk,
                        "assigned_holder": self.holder.pk,
                        "assigned_location": "",
                        "assigned_asset": "",
                        "qty": 1,
                        "notes": "Three-model request workflow",
                    },
                    HTTP_HX_REQUEST="true",
                )
                self.assertEqual(response.status_code, 204, response.content.decode())
                assignment = assignment_model._base_manager.get(
                    assigned_holder=self.holder,
                    **{item.__class__.__name__.lower(): item},
                )
                self.assertEqual(assignment.resource_grant_id, grant_row.pk)
