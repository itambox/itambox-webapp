from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse

from assets.models import Asset, AssetRole, Category, Manufacturer
from core.tests.mixins import TenantTestMixin
from inventory.forms import ComponentAllocationForm
from inventory.models import Component, ComponentAllocation, ComponentStock
from inventory.models_assignment_write import authorized_assignment_write
from inventory.services import checkout_inventory_item, create_component_allocation
from inventory.tests.factories import create_assignment_fixture
from organization.models import AssetHolder, Location, Site, Tenant, TenantResourceGrant

User = get_user_model()


class ComponentModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name="Samsung", slug="samsung")
        self.category = Category.objects.create(name="Storage", slug="storage", applies_to={"component": True})

    def test_component_creation(self):
        comp = Component.objects.create(
            name="990 Pro 2TB",
            manufacturer=self.manufacturer,
            category=self.category,
            part_number="MZ-V9P2T0B",
            specs={"capacity_gb": 2000, "type": "NVMe", "interface": "PCIe 4.0"},
        )
        self.assertEqual(str(comp), "Samsung 990 Pro 2TB")
        self.assertEqual(comp.slug, "samsung-990-pro-2tb")

    def test_component_absolute_url(self):
        comp = Component.objects.create(name="980 Pro", manufacturer=self.manufacturer, category=self.category)
        url = comp.get_absolute_url()
        self.assertIn(str(comp.pk), url)

    def test_component_unique_per_manufacturer(self):
        Component.objects.create(name="Test RAM", manufacturer=self.manufacturer, category=self.category)
        with self.assertRaises(IntegrityError):
            Component.objects.create(name="Test RAM", manufacturer=self.manufacturer, category=self.category)

    def test_component_stock_computation(self):
        comp = Component.objects.create(name="990 Pro 2TB", manufacturer=self.manufacturer, category=self.category)
        tenant = Tenant.objects.create(name="Tenant Component Stock", slug="tenant-component-stock")
        site = Site.objects.create(name="Berlin HQ", slug="berlin-hq", tenant=tenant)
        location = Location.objects.create(name="Server Room A", slug="server-room-a", site=site, tenant=tenant)
        ComponentStock.objects.create(component=comp, location=location, qty=10)
        location2 = Location.objects.create(name="Server Room B", slug="server-room-b", site=site, tenant=tenant)
        ComponentStock.objects.create(component=comp, location=location2, qty=5)
        self.assertEqual(comp.total_stock, 15)
        self.assertEqual(comp.available_stock, 15)


class ComponentAllocationModelTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name="Intel", slug="intel")
        self.category = Category.objects.create(name="CPU", slug="cpu", applies_to={"component": True})
        self.component = Component.objects.create(
            name="Core i9-13900K", manufacturer=self.manufacturer, category=self.category
        )
        self.role = AssetRole.objects.create(name="Workstation", slug="workstation")
        self.asset = Asset.objects.create(name="WS-001", asset_tag="TAG-CPU-001", asset_role=self.role)

    def test_allocation_creation(self):
        alloc = create_assignment_fixture(
            ComponentAllocation,
            component=self.component,
            assigned_asset=self.asset,
            qty=1,
        )
        self.assertIn("Core i9-13900K", str(alloc))
        self.assertIn("WS-001", str(alloc))

    def test_allocation_absolute_url(self):
        alloc = create_assignment_fixture(
            ComponentAllocation, component=self.component, assigned_asset=self.asset, qty=1
        )
        url = alloc.get_absolute_url()
        self.assertIn(str(self.asset.pk), url)

    def test_allocation_default_qty(self):
        alloc = create_assignment_fixture(ComponentAllocation, component=self.component, assigned_asset=self.asset)
        self.assertEqual(alloc.qty, 1)


class ComponentWarehouseOriginTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name="Samsung", slug="samsung")
        self.category = Category.objects.create(name="Storage", slug="storage", applies_to={"component": True})
        self.component = Component.objects.create(
            name="990 Pro 2TB", manufacturer=self.manufacturer, category=self.category
        )
        self.role = AssetRole.objects.create(name="Server", slug="server")
        self.tenant = Tenant.objects.create(name="Tenant Warehouse Origin", slug="tenant-warehouse-origin")
        self.site = Site.objects.create(name="Munich HQ", slug="munich-hq", tenant=self.tenant)
        self.warehouse = Location.objects.create(
            name="Warehouse A", slug="warehouse-a", site=self.site, tenant=self.tenant
        )
        self.desk = Location.objects.create(name="Desk B", slug="desk-b", site=self.site, tenant=self.tenant)

        self.asset = Asset.objects.create(name="SRV-100", asset_tag="TAG-100", asset_role=self.role, location=self.desk)
        self.stock = ComponentStock.objects.create(component=self.component, location=self.warehouse, qty=10)

    def test_allocation_decrements_origin_stock(self):
        alloc = create_assignment_fixture(
            ComponentAllocation,
            component=self.component,
            assigned_asset=self.asset,
            from_location=self.warehouse,
            qty=2,
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 8)

        self.asset.location = self.desk
        self.asset.save()

        with authorized_assignment_write(alloc):
            alloc.delete()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 10)

        self.assertFalse(ComponentStock.objects.filter(component=self.component, location=self.desk).exists())

    def test_allocation_update_quantity_recalculates_stock(self):
        alloc = create_assignment_fixture(
            ComponentAllocation,
            component=self.component,
            assigned_asset=self.asset,
            from_location=self.warehouse,
            qty=2,
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 8)

        alloc.qty = 5
        with authorized_assignment_write(alloc):
            alloc.save()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 5)

        alloc.qty = 1
        with authorized_assignment_write(alloc):
            alloc.save()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 9)

    def test_allocation_update_location_reallocates_stock(self):
        desk_stock = ComponentStock.objects.create(component=self.component, location=self.desk, qty=5)

        alloc = create_assignment_fixture(
            ComponentAllocation,
            component=self.component,
            assigned_asset=self.asset,
            from_location=self.warehouse,
            qty=3,
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 7)

        alloc.from_location = self.desk
        with authorized_assignment_write(alloc):
            alloc.save()

        self.stock.refresh_from_db()
        desk_stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 10)
        self.assertEqual(desk_stock.qty, 2)

    def test_allocation_soft_delete_reverts_stock(self):
        alloc = create_assignment_fixture(
            ComponentAllocation,
            component=self.component,
            assigned_asset=self.asset,
            from_location=self.warehouse,
            qty=4,
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 6)

        with authorized_assignment_write(alloc):
            alloc.delete()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 10)

        with authorized_assignment_write(alloc):
            alloc.restore()
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 6)


class ComponentViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testadmin", password="testpassword", is_staff=True, is_superuser=True
        )
        self.client.login(username="testadmin", password="testpassword")
        self.manufacturer = Manufacturer.objects.create(name="Samsung", slug="samsung")
        self.category = Category.objects.create(name="Storage", slug="storage", applies_to={"component": True})
        self.component = Component.objects.create(
            name="990 Pro 2TB", manufacturer=self.manufacturer, category=self.category
        )

    def test_list_view(self):
        url = reverse("components:component_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 301)
        self.assertIn("/inventory/inventory/?type=components", response.url)

        # Test the unified view for components redirect
        unified_url = reverse("inventory:inventory_list") + "?type=components"
        response = self.client.get(unified_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/inventory/components/", response.url)

        # Test the direct component list view
        direct_url = reverse("inventory:component_list")
        response = self.client.get(direct_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "990 Pro 2TB")

    def test_detail_view(self):
        url = reverse("inventory:component_detail", kwargs={"pk": self.component.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "990 Pro 2TB")

    def test_create_view_get(self):
        url = reverse("inventory:component_create")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse("inventory:component_create")
        response = self.client.post(
            url,
            {
                "manufacturer": self.manufacturer.pk,
                "name": "980 Pro 1TB",
                "slug": "samsung-980-pro-1tb",
                "category": self.category.pk,
                "min_qty": 0,
                "specs": "{}",
                "tags": [],
            },
        )
        if response.status_code != 302:
            form = response.context.get("form")
            self.fail(f"Form invalid. Errors: {form.errors if form else 'no form in context'}")
        self.assertTrue(Component.objects.filter(name="980 Pro 1TB").exists())

    def test_edit_view_get(self):
        url = reverse("inventory:component_update", kwargs={"pk": self.component.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse("inventory:component_update", kwargs={"pk": self.component.pk})
        response = self.client.post(
            url,
            {
                "manufacturer": self.manufacturer.pk,
                "name": "990 Pro 4TB",
                "slug": "samsung-990-pro-4tb",
                "category": self.category.pk,
                "min_qty": 0,
                "specs": "{}",
                "tags": [],
            },
        )
        if response.status_code != 302:
            form = response.context.get("form")
            self.fail(f"Form invalid. Errors: {form.errors if form else 'no form in context'}")
        self.component.refresh_from_db()
        self.assertEqual(self.component.name, "990 Pro 4TB")

    def test_delete_view(self):
        url = reverse("inventory:component_delete", kwargs={"pk": self.component.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Component.objects.filter(pk=self.component.pk).exists())


class ComponentAllocationViewTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testadmin", password="testpassword", is_staff=True, is_superuser=True
        )
        self.client.login(username="testadmin", password="testpassword")
        self.tenant = Tenant.objects.create(name="Component Allocation Tenant", slug="component-allocation-tenant")
        self.manufacturer = Manufacturer.objects.create(name="Samsung", slug="samsung")
        self.category = Category.objects.create(name="Storage", slug="storage", applies_to={"component": True})
        self.component = Component.objects.create(
            name="990 Pro 2TB",
            manufacturer=self.manufacturer,
            category=self.category,
            allow_overallocate=True,
            tenant=self.tenant,
        )
        self.role = AssetRole.objects.create(name="Server", slug="server")
        self.asset = Asset.objects.create(name="SRV-001", asset_tag="SRV-001", asset_role=self.role, tenant=self.tenant)
        self.allocation = create_assignment_fixture(
            ComponentAllocation,
            component=self.component,
            assigned_asset=self.asset,
            qty=2,
            notes="Initial setup",
        )
        self.client_login_to_tenant(
            self.user,
            self.tenant,
            role_permissions=[
                "inventory.add_componentallocation",
                "inventory.change_componentallocation",
                "inventory.delete_componentallocation",
                "inventory.view_componentallocation",
            ],
        )

    def test_list_view(self):
        url = reverse("inventory:componentallocation_list")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_get(self):
        url = reverse("inventory:componentallocation_create")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse("inventory:componentallocation_create")
        response = self.client.post(
            url,
            {
                "component": self.component.pk,
                "assigned_asset": self.asset.pk,
                "qty": 1,
            },
        )
        self.assertEqual(response.status_code, 302, response.content.decode())

    def test_edit_view_get(self):
        url = reverse("inventory:componentallocation_update", kwargs={"pk": self.allocation.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_edit_view_post(self):
        url = reverse("inventory:componentallocation_update", kwargs={"pk": self.allocation.pk})
        response = self.client.post(
            url,
            {
                "component": self.component.pk,
                "assigned_asset": self.asset.pk,
                "qty": 3,
                "notes": "Updated allocation",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.qty, 3)
        self.assertEqual(self.allocation.notes, "Updated allocation")

    def test_delete_view(self):
        url = reverse("inventory:componentallocation_delete", kwargs={"pk": self.allocation.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ComponentAllocation.objects.filter(pk=self.allocation.pk).exists())


class Issue393ComponentAllocationContractTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(username="issue393-admin", password="x")
        self.tenant = Tenant.objects.create(name="Issue 393 Tenant", slug="issue-393-tenant")
        self.client_login_to_tenant(self.user, self.tenant)
        self.manufacturer = Manufacturer.objects.create(name="Issue 393 Mfg", slug="issue-393-mfg")
        self.category = Category.objects.create(
            name="Issue 393 Component",
            slug="issue-393-component",
            applies_to={"component": True},
        )
        self.site = Site.objects.create(name="Issue 393 Site", slug="issue-393-site", tenant=self.tenant)
        self.source_a = Location.objects.create(
            name="Issue 393 Source A",
            slug="issue-393-source-a",
            site=self.site,
            tenant=self.tenant,
        )
        self.source_b = Location.objects.create(
            name="Issue 393 Source B",
            slug="issue-393-source-b",
            site=self.site,
            tenant=self.tenant,
        )
        self.holder = AssetHolder.objects.create(
            first_name="Issue",
            last_name="Holder",
            upn="issue393-holder@example.test",
            tenant=self.tenant,
        )
        role = AssetRole.objects.create(name="Issue 393 Server", slug="issue-393-server", allows_components=True)
        self.asset = Asset.objects.create(
            name="Issue 393 Asset",
            asset_tag="ISSUE-393-ASSET",
            asset_role=role,
            tenant=self.tenant,
        )
        self.component = Component.objects.create(
            name="Issue 393 RAM",
            manufacturer=self.manufacturer,
            category=self.category,
            tenant=self.tenant,
        )
        self.stock_a = ComponentStock.objects.create(component=self.component, location=self.source_a, qty=5)
        self.stock_b = ComponentStock.objects.create(component=self.component, location=self.source_b, qty=3)
        self.create_url = reverse("inventory:componentallocation_create")

    def _target_only_payload(self, **overrides):
        payload = {
            "component": self.component.pk,
            "assigned_holder": self.holder.pk,
            "qty": 2,
            "notes": "Issue 393 target-only allocation",
        }
        payload.update(overrides)
        return payload

    def _source_backed_allocation(self):
        return checkout_inventory_item(
            self.component,
            2,
            asset=self.asset,
            source_location=self.source_a,
            user=self.user,
            notes="Issue 393 source-backed allocation",
        )

    def test_create_form_hides_source_and_rejects_tampered_source(self):
        form = ComponentAllocationForm()
        self.assertNotIn("from_location", form.fields)

        bound = ComponentAllocationForm(data=self._target_only_payload(from_location=self.source_a.pk))
        self.assertFalse(bound.is_valid())
        self.assertIn("only available through component checkout", str(bound.errors))

    def test_create_form_rejects_duplicate_source_when_last_value_is_empty(self):
        data = QueryDict("", mutable=True)
        for field_name, value in self._target_only_payload().items():
            data[field_name] = value
        data.setlist("from_location", [str(self.source_a.pk), ""])

        form = ComponentAllocationForm(data=data)

        self.assertFalse(form.is_valid())
        self.assertIn("only available through component checkout", str(form.errors))

    def test_update_form_rejects_duplicate_source_when_last_value_matches_persisted(self):
        allocation = self._source_backed_allocation()
        data = QueryDict("", mutable=True)
        data["qty"] = str(allocation.qty)
        data["notes"] = allocation.notes
        data.setlist("from_location", [str(self.source_b.pk), str(self.source_a.pk)])

        form = ComponentAllocationForm(data=data, instance=allocation)

        self.assertFalse(form.is_valid())
        self.assertIn("immutable", str(form.errors))

    def test_update_form_preserves_source_read_only_and_rejects_tampering(self):
        allocation = self._source_backed_allocation()
        form = ComponentAllocationForm(instance=allocation)
        self.assertIn("from_location", form.fields)
        self.assertTrue(form.fields["from_location"].disabled)

        bound = ComponentAllocationForm(
            data={
                "component": self.component.pk,
                "assigned_asset": self.asset.pk,
                "from_location": self.source_b.pk,
                "qty": allocation.qty,
                "notes": allocation.notes,
            },
            instance=allocation,
        )
        self.assertFalse(bound.is_valid())
        self.assertIn("immutable", str(bound.errors))

    def test_update_form_preserves_granted_cross_tenant_source(self):
        owner = Tenant.objects.create(name="Issue 393 Source Owner", slug="issue-393-source-owner")
        owner_site = Site.objects.create(name="Issue 393 Owner Site", slug="issue-393-owner-site", tenant=owner)
        owner_location = Location.objects.create(
            name="Issue 393 Owner Source",
            slug="issue-393-owner-source",
            site=owner_site,
            tenant=owner,
        )
        owner_stock = ComponentStock.objects.create(component=self.component, location=owner_location, qty=5)
        TenantResourceGrant.objects.create(
            tenant=owner,
            grantee_tenant=self.tenant,
            resource_type=ContentType.objects.get_for_model(ComponentStock),
            resource_id=owner_stock.pk,
            access_level=TenantResourceGrant.ACCESS_USE,
        )
        allocation = checkout_inventory_item(
            self.component,
            1,
            asset=self.asset,
            source_location=owner_location,
            user=self.user,
        )

        form = ComponentAllocationForm(
            data={"qty": 2, "notes": "Issue 393 cross-tenant update"},
            instance=allocation,
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["from_location"].pk, owner_location.pk)

    def test_full_page_create_rejects_explicit_source_without_mutation(self):
        response = self.client.post(self.create_url, self._target_only_payload(from_location=self.source_a.pk))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "only available through component checkout")
        self.assertEqual(ComponentAllocation._base_manager.count(), 0)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.qty, 5)

    def test_target_only_create_persists_exactly_one_and_reads_back(self):
        response = self.client.post(self.create_url, self._target_only_payload())

        self.assertEqual(response.status_code, 302, response.content)
        allocation = ComponentAllocation._base_manager.get()
        self.assertIsNone(allocation.from_location_id)
        self.assertEqual(allocation.assigned_holder_id, self.holder.pk)
        self.assertEqual(allocation.qty, 2)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.qty, 5)
        detail = self.client.get(self.component.get_absolute_url())
        self.assertEqual([row.record.pk for row in detail.context["allocations_table"].rows], [allocation.pk])

    def test_filtered_allocation_list_reads_back_without_distinct_union_500(self):
        allocation = create_component_allocation(
            self.component,
            1,
            holder=self.holder,
            user=self.user,
            notes="Issue 393 filtered readback",
        )
        self.client.raise_request_exception = False

        response = self.client.get(reverse("inventory:componentallocation_list"), {"q": "filtered readback"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row.record.pk for row in response.context["table"].rows], [allocation.pk])

    def test_filtered_component_stock_list_does_not_mix_distinct_querysets(self):
        self.client.raise_request_exception = False

        response = self.client.get(reverse("inventory:componentstock_list"), {"q": "Issue 393 Source A"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row.record.pk for row in response.context["table"].rows], [self.stock_a.pk])

    def test_asset_quick_add_uses_service_then_returns_hx_redirect(self):
        quick_add_url = f"{self.create_url}?asset={self.asset.pk}&_quickadd=1"
        response = self.client.post(
            quick_add_url,
            {
                "component": self.component.pk,
                "assigned_asset": self.asset.pk,
                "qty": 2,
                "notes": "Issue 393 asset quick-add",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers["HX-Redirect"], self.asset.get_absolute_url())
        allocation = ComponentAllocation._base_manager.get()
        self.assertEqual(allocation.assigned_asset_id, self.asset.pk)
        self.assertIsNone(allocation.from_location_id)

    def test_asset_quick_add_rejects_explicit_source_as_visible_422(self):
        quick_add_url = f"{self.create_url}?asset={self.asset.pk}&_quickadd=1"
        response = self.client.post(
            quick_add_url,
            {
                "component": self.component.pk,
                "assigned_asset": self.asset.pk,
                "from_location": self.source_a.pk,
                "qty": 2,
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "only available through component checkout", status_code=422)
        self.assertEqual(ComponentAllocation._base_manager.count(), 0)
        self.stock_a.refresh_from_db()
        self.assertEqual(self.stock_a.qty, 5)

    def test_update_source_tamper_is_bound_error_not_500(self):
        allocation = self._source_backed_allocation()
        before = (
            allocation.component_id,
            allocation.assigned_asset_id,
            allocation.from_location_id,
            allocation.qty,
            allocation.notes,
        )
        self.client.raise_request_exception = False
        response = self.client.post(
            reverse("inventory:componentallocation_update", kwargs={"pk": allocation.pk}),
            {
                "component": self.component.pk,
                "assigned_asset": self.asset.pk,
                "from_location": self.source_b.pk,
                "qty": allocation.qty,
                "notes": allocation.notes,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "immutable")
        allocation.refresh_from_db()
        after = (
            allocation.component_id,
            allocation.assigned_asset_id,
            allocation.from_location_id,
            allocation.qty,
            allocation.notes,
        )
        self.assertEqual(after, before)

    def test_post_validation_service_error_is_bound_without_mutation(self):
        allocation = create_component_allocation(
            self.component,
            1,
            holder=self.holder,
            user=self.user,
            notes="Issue 393 service-error baseline",
        )
        self.client.raise_request_exception = False
        with patch(
            "inventory.views.component_views.update_component_allocation",
            side_effect=ValidationError("Injected issue 393 service error"),
        ):
            response = self.client.post(
                reverse("inventory:componentallocation_update", kwargs={"pk": allocation.pk}),
                self._target_only_payload(qty=1, notes=allocation.notes),
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Injected issue 393 service error")
        allocation.refresh_from_db()
        self.assertEqual(allocation.qty, 1)
        self.assertEqual(allocation.notes, "Issue 393 service-error baseline")


class ComponentStockAdjustViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="testadmin", password="testpassword", is_staff=True, is_superuser=True
        )
        self.client.login(username="testadmin", password="testpassword")
        self.manufacturer = Manufacturer.objects.create(name="SamsungSym", slug="samsungsym")
        self.category = Category.objects.create(name="StorageSym", slug="storagesym", applies_to={"component": True})
        self.component = Component.objects.create(
            name="990 Pro 2TB Sym", manufacturer=self.manufacturer, category=self.category
        )
        self.tenant = Tenant.objects.create(name="Tenant Component Sym", slug="tenant-component-sym")
        self.site = Site.objects.create(name="OfficeSym", slug="officesym", tenant=self.tenant)
        self.location = Location.objects.create(name="DeskSym", slug="desksym", site=self.site, tenant=self.tenant)
        self.stock = ComponentStock.objects.create(component=self.component, location=self.location, qty=10)

    def test_component_stock_adjust_increment(self):
        url = reverse("inventory:componentstock_adjust", kwargs={"pk": self.stock.pk}) + "?action=increment"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 11)
        self.assertContains(response, "11")

    def test_component_stock_adjust_decrement(self):
        url = reverse("inventory:componentstock_adjust", kwargs={"pk": self.stock.pk}) + "?action=decrement"
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 9)
        self.assertContains(response, "9")
