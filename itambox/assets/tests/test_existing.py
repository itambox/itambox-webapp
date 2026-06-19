from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from assets.models import Manufacturer, Asset, AssetType, AssetRole, StatusLabel, Depreciation, Supplier, Category, AssetRequest
from inventory.models import Accessory, AccessoryAssignment, Consumable, ConsumableAssignment, Kit, KitItem, Component, ComponentAllocation
from compliance.models import CustodyReceipt
from assets.models import AssetMaintenance
from extras.models import CustomField, CustomFieldset
from django.contrib.contenttypes.models import ContentType
from organization.models import Contact, ContactRole, ContactAssignment

from decimal import Decimal

User = get_user_model()

class ComponentTrackingTestCase(TransactionTestCase):

    def setUp(self):
        self.user = User.objects.create_user(username='testadmin', password='testpassword', is_staff=True, is_superuser=True)
        self.client.login(username='testadmin', password='testpassword')

        self.manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        self.category = Category.objects.create(name="Memory", slug="memory", applies_to={"component": True})

        from organization.models import Site, Location
        self.site = Site.objects.create(name="HQ", slug="hq")
        self.location = Location.objects.create(name="Warehouse", slug="warehouse", site=self.site)
        self.acc_category = Category.objects.create(name="Keyboard", slug="keyboard", applies_to={"accessory": True})
        self.con_category = Category.objects.create(name="Thermal Paste", slug="thermal-paste", applies_to={"consumable": True})

        self.component = Component.objects.create(
            manufacturer=self.manufacturer,
            name="16GB DDR5 RAM",
            slug="dell-16gb-ddr5-ram",
            category=self.category,
            specs={"capacity_gb": 16, "type": "DDR5", "speed_mhz": 4800},
            part_number="RAM-16G-D5",
            allow_overallocate=True
        )

        self.role = AssetRole.objects.create(name="Server", slug="server")
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="PowerEdge R750",
            slug="dell-poweredge-r750"
        )

        self.status = StatusLabel.objects.get_or_create(
            slug='available',
            defaults={'name': 'Available', 'type': StatusLabel.TYPE_DEPLOYABLE, 'color': '28a745'}
        )[0]

        self.asset = Asset.objects.create(
            name="Web Server 01",
            asset_tag="SRV-001",
            asset_type=self.asset_type,
            asset_role=self.role,
            status=self.status
        )

        self.allocation = ComponentAllocation.objects.create(
            component=self.component,
            assigned_asset=self.asset,
            qty=2,
            notes="Initial RAM allocation"
        )

    def test_component_detail_view(self):
        # We replace any reference to specific request views that might be affected
        response = self.client.get(reverse('inventory:component_detail', kwargs={'pk': self.component.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "16GB DDR5 RAM")

    def test_component_list_view(self):
        response = self.client.get(reverse('components:component_list'))
        self.assertEqual(response.status_code, 301)
        self.assertEqual(response.url, reverse('inventory:inventory_list') + '?type=components')
        
        response = self.client.get(response.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('inventory:component_list'))
        
        response = self.client.get(response.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "16GB DDR5 RAM")

    def test_component_create_view(self):
        response = self.client.get(reverse('inventory:component_create'))
        self.assertEqual(response.status_code, 200)

        post_data = {
            'manufacturer': self.manufacturer.pk,
            'name': '2TB NVMe SSD',
            'slug': 'dell-2tb-nvme-ssd',
            'category': self.category.pk,
            'part_number': 'SSD-2TB-NVME',
            'min_qty': 0,
            'specs': '{}',
            'notes': 'Samsung SSD for server storage',
            'tags': [],
        }
        response = self.client.post(reverse('inventory:component_create'), data=post_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Component.objects.filter(name='2TB NVMe SSD').exists())

    def test_component_update_view(self):
        response = self.client.get(reverse('inventory:component_update', kwargs={'pk': self.component.pk}))
        self.assertEqual(response.status_code, 200)

        post_data = {
            'manufacturer': self.manufacturer.pk,
            'name': '16GB DDR5 RAM (Updated)',
            'slug': 'dell-16gb-ddr5-ram',
            'category': self.category.pk,
            'part_number': 'RAM-16G-D5-UPDATED',
            'min_qty': 0,
            'specs': '{}',
            'notes': 'Updated spec RAM',
            'tags': [],
        }
        response = self.client.post(reverse('inventory:component_update', kwargs={'pk': self.component.pk}), data=post_data)
        self.assertEqual(response.status_code, 302)
        self.component.refresh_from_db()
        self.assertEqual(self.component.name, '16GB DDR5 RAM (Updated)')

    def test_component_delete_view(self):
        self.allocation.delete(force_hard_delete=True)
        response = self.client.post(reverse('inventory:component_delete', kwargs={'pk': self.component.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Component.objects.filter(pk=self.component.pk).exists())


    def test_componentallocation_list_view(self):
        response = self.client.get(reverse('inventory:componentallocation_list'))
        self.assertEqual(response.status_code, 200)

    def test_componentallocation_create_view(self):
        response = self.client.get(reverse('inventory:componentallocation_create'))
        self.assertEqual(response.status_code, 200)

        post_data = {
            'component': self.component.pk,
            'assigned_asset': self.asset.pk,
            'qty': 1,
            'notes': 'Secondary RAM stick'
        }
        response = self.client.post(reverse('inventory:componentallocation_create'), data=post_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(ComponentAllocation.objects.filter(qty=1, notes='Secondary RAM stick').exists())

    def test_componentallocation_update_view(self):
        response = self.client.get(reverse('inventory:componentallocation_update', kwargs={'pk': self.allocation.pk}))
        self.assertEqual(response.status_code, 200)

        post_data = {
            'component': self.component.pk,
            'assigned_asset': self.asset.pk,
            'qty': 3,
            'notes': 'Updated RAM allocation'
        }
        response = self.client.post(reverse('inventory:componentallocation_update', kwargs={'pk': self.allocation.pk}), data=post_data)
        self.assertEqual(response.status_code, 302)
        self.allocation.refresh_from_db()
        self.assertEqual(self.allocation.qty, 3)
        self.assertEqual(self.allocation.notes, 'Updated RAM allocation')

    def test_componentallocation_delete_view(self):
        response = self.client.post(reverse('inventory:componentallocation_delete', kwargs={'pk': self.allocation.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ComponentAllocation.objects.filter(pk=self.allocation.pk).exists())

    def test_checkout_checkin_button_visibility(self):
        from assets.services import checkout_asset
        from organization.models import AssetHolder

        # 1. When the asset is available (not checked out)
        response = self.client.get(reverse('assets:asset_detail', kwargs={'pk': self.asset.pk}))
        self.assertEqual(response.status_code, 200)
        # Check Out... button is visible, but Check In button is NOT visible
        self.assertContains(response, "Check Out...")
        self.assertNotContains(response, "Check In")

        # 2. Check out the asset to a holder
        holder = AssetHolder.objects.create(first_name="John", last_name="Doe", upn="john@example.com")
        checkout_asset(self.asset, holder=holder, user=self.user)

        # 3. Reload detail page: asset is now checked out
        response = self.client.get(reverse('assets:asset_detail', kwargs={'pk': self.asset.pk}))
        self.assertEqual(response.status_code, 200)
        # Check In button is visible, but Check Out... button is NOT visible
        self.assertContains(response, "Check In")
        self.assertNotContains(response, "Check Out...")

    def test_asset_detail_view_components_integration(self):
        response = self.client.get(reverse('assets:asset_detail', kwargs={'pk': self.asset.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Installed Physical Modules / Components")

        self.assertIn('components_table', response.context)
        comp_table = response.context['components_table']
        self.assertEqual(len(comp_table.rows), 1)

    def test_asset_detail_shows_allocated_components_in_specs_card(self):
        response = self.client.get(reverse('assets:asset_detail', kwargs={'pk': self.asset.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Assigned System Hardware Specifications")
        self.assertContains(response, "Active Hardware Modifications &amp; Upgrades")
        self.assertContains(response, "16GB DDR5 RAM")
        self.assertContains(response, "Qty: <strong>2</strong>")

    def test_asset_detail_without_custom_fieldset_shows_specs_card(self):
        self.assertIsNone(self.asset_type.custom_fieldset)
        response = self.client.get(reverse('assets:asset_detail', kwargs={'pk': self.asset.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Assigned System Hardware Specifications")
        
        self.asset.component_allocations.all().delete()
        response = self.client.get(reverse('assets:asset_detail', kwargs={'pk': self.asset.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Assigned System Hardware Specifications")

    def test_accessory_crud_and_checkout_views(self):
        from inventory.models import AccessoryStock
        # Create Accessory
        acc = Accessory.objects.create(
            manufacturer=self.manufacturer,
            name="Wired Keyboard KB216",
            slug="dell-wired-keyboard-kb216",
            category=self.acc_category,
            min_qty=2,
            allow_overallocate=False
        )
        AccessoryStock.objects.create(
            accessory=acc,
            location=self.location,
            qty=10
        )

        # 1. List View
        response = self.client.get(reverse('inventory:accessory_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wired Keyboard KB216")

        # 2. Detail View
        response = self.client.get(reverse('inventory:accessory_detail', kwargs={'pk': acc.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wired Keyboard KB216")

        # 3. Create View
        post_data = {
            'manufacturer': self.manufacturer.pk,
            'name': 'Wireless Mouse WM126',
            'slug': 'dell-wireless-mouse-wm126',
            'category': self.acc_category.pk,
            'part_number': 'MS-WM126',
            'min_qty': 3,
            'allow_overallocate': True,
            'notes': 'Office standard mouse'
        }
        response = self.client.post(reverse('inventory:accessory_create'), data=post_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Accessory.objects.filter(name='Wireless Mouse WM126').exists())

        # 4. Strict Checkout Limit Validation (qty=11 > remaining=10, overallocate=False)
        from organization.models import AssetHolder
        holder = AssetHolder.objects.create(first_name="John", last_name="Doe", email="john@example.com")
        
        checkout_data = {
            'from_location': self.location.pk,
            'assigned_holder': holder.pk,
            'assigned_location': '',
            'qty': 11,
            'notes': 'Over-allocate attempt'
        }
        response = self.client.post(reverse('inventory:accessory_checkout', kwargs={'pk': acc.pk}), data=checkout_data)
        self.assertEqual(AccessoryAssignment.objects.filter(accessory=acc).count(), 0)

        # 5. Successful Checkout (qty=5 <= 10)
        checkout_data['qty'] = 5
        response = self.client.post(reverse('inventory:accessory_checkout', kwargs={'pk': acc.pk}), data=checkout_data, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 204) # 204 No Content for success HTMX modal
        self.assertEqual(AccessoryAssignment.objects.filter(accessory=acc).count(), 1)
        self.assertEqual(acc.available, 5)

        # 6. Check In (Checkin Assignment deletes it)
        assignment = AccessoryAssignment.objects.get(accessory=acc)
        response = self.client.post(reverse('inventory:accessory_checkin', kwargs={'pk': assignment.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AccessoryAssignment.objects.filter(accessory=acc).count(), 0)
        self.assertEqual(acc.available, 10)

    def test_asset_audit_view(self):
        # GET renders the modal form.
        response = self.client.get(reverse('assets:asset_audit', kwargs={'pk': self.asset.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'asset-audit-modal')

        # POST without required fields returns form error (422).
        response = self.client.post(
            reverse('assets:asset_audit', kwargs={'pk': self.asset.pk}),
            data={},
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 422)
        self.asset.refresh_from_db()
        self.assertIsNone(self.asset.last_audited)

        # POST with location + status succeeds and records the audit.
        response = self.client.post(
            reverse('assets:asset_audit', kwargs={'pk': self.asset.pk}),
            data={'location': self.location.pk, 'status': self.asset.status.pk},
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)

        self.asset.refresh_from_db()
        self.assertIsNotNone(self.asset.last_audited)
        self.assertEqual(self.asset.last_audited_by, self.user)

    def test_asset_label_print_view(self):
        response = self.client.get(reverse('assets:asset_label_print', kwargs={'pk': self.asset.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thermal Label Preview")
        self.assertContains(response, "data:image/png")  # engine-rendered QR card (matches bulk output)

    def test_custody_receipt_signoff(self):
        from organization.models import AssetHolder
        # Link the holder to the logged-in user so the view's holder-identity
        # check passes without weakening the security control.
        holder = AssetHolder.objects.create(
            first_name="Alice", last_name="Wonder", upn="alice@example.com",
            user=self.user,
        )

        receipt = CustodyReceipt.objects.create(
            asset=self.asset,
            holder=holder,
        )
        token = receipt.token

        sign_url = reverse('compliance:custody_eula_sign', kwargs={'token': token})
        response = self.client.get(sign_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "custody-sign-form")

        invalid_url = reverse('compliance:custody_eula_sign', kwargs={'token': "invalid-token-value"})
        response = self.client.get(invalid_url)
        self.assertEqual(response.status_code, 404)

        post_data = {
            'signature_canvas': 'data:image/png;base64,drawingdata123',
            'action': 'accept',
        }
        response = self.client.post(sign_url, data=post_data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Secured")

        receipt.refresh_from_db()
        self.assertTrue(receipt.accepted)
        self.assertEqual(receipt.acceptance_status, CustodyReceipt.STATUS_ACCEPTED)
        self.assertIsNotNone(receipt.accepted_date)
        self.assertEqual(receipt.acceptance_method, 'digital')
        self.assertEqual(receipt.signature_canvas, 'data:image/png;base64,drawingdata123')
        self.assertEqual(len(receipt.verification_hash), 64)

    def test_custody_receipt_decline(self):
        from organization.models import AssetHolder
        # Link the holder to the logged-in user so the view's holder-identity
        # check passes without weakening the security control.
        holder = AssetHolder.objects.create(
            first_name="Bob", last_name="Builder", upn="bob@example.com",
            user=self.user,
        )

        receipt = CustodyReceipt.objects.create(
            asset=self.asset,
            holder=holder,
        )

        sign_url = reverse('compliance:custody_eula_sign', kwargs={'token': receipt.token})
        post_data = {
            'action': 'decline',
        }
        response = self.client.post(sign_url, data=post_data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "declined")

        receipt.refresh_from_db()
        self.assertEqual(receipt.acceptance_status, CustodyReceipt.STATUS_DECLINED)
        self.assertFalse(receipt.accepted)


class AssetProcurementTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testadmin', password='testpassword', is_staff=True, is_superuser=True)
        self.client.login(username='testadmin', password='testpassword')
        
        self.manufacturer = Manufacturer.objects.create(name="Lenovo", slug="lenovo")
        self.role = AssetRole.objects.create(name="Laptop", slug="laptop")
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="ThinkPad T14",
            slug="lenovo-thinkpad-t14"
        )

    def test_asset_procurement_fields_save_and_display(self):
        from assets.models import Supplier
        supplier = Supplier.objects.create(name='Lenovo Germany GmbH', slug='lenovo-germany-gmbh')
        # Create asset with procurement details
        asset = Asset.objects.create(
            name="Developer ThinkPad",
            asset_tag="LAP-001",
            asset_type=self.asset_type,
            asset_role=self.role,
            purchase_cost=Decimal("1249.99"),
            order_number="PO-998877",
            supplier=supplier
        )
        
        # Verify saved correctly in DB
        asset.refresh_from_db()
        self.assertEqual(asset.purchase_cost, Decimal("1249.99"))
        self.assertEqual(asset.order_number, "PO-998877")
        self.assertEqual(asset.supplier.name, "Lenovo Germany GmbH")

        # Verify detail page displays them
        response = self.client.get(reverse('assets:asset_detail', kwargs={'pk': asset.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "1,249.99")
        self.assertContains(response, "PO-998877")
        self.assertContains(response, "Lenovo Germany GmbH")

    def test_asset_form_procurement_fields(self):
        from assets.models import Supplier
        supplier = Supplier.objects.create(name='Bechtle AG', slug='bechtle-ag')
        # Test creating new asset via POST
        post_data = {
            'name': 'Sales ThinkPad',
            'asset_tag': 'LAP-002',
            'asset_type': self.asset_type.pk,
            'asset_role': self.role.pk,
            'status': 'available',  # legacy free-text value, exercises the form's tolerance
            'purchase_cost': '999.50',
            'order_number': 'PO-112233',
            'supplier': supplier.pk,
            'notes': 'Sales laptop standard spec',
            'tags': []
        }
        response = self.client.post(reverse('assets:asset_create'), data=post_data)
        self.assertEqual(response.status_code, 302) # Redirects on success
        
        # Verify created asset
        new_asset = Asset.objects.get(asset_tag='LAP-002')
        self.assertEqual(new_asset.name, 'Sales ThinkPad')
        self.assertEqual(new_asset.purchase_cost, Decimal('999.50'))
        self.assertEqual(new_asset.order_number, 'PO-112233')
        self.assertEqual(new_asset.supplier.name, 'Bechtle AG')

        # Test fields are optional
        post_data_optional = {
            'name': 'Minimal ThinkPad',
            'asset_tag': 'LAP-003',
            'asset_type': self.asset_type.pk,
            'asset_role': self.role.pk,
            'status': 'available',  # legacy free-text value, exercises the form's tolerance
            'purchase_cost': '',
            'order_number': '',
            'supplier': '',
            'notes': '',
            'tags': []
        }
        response = self.client.post(reverse('assets:asset_create'), data=post_data_optional)
        self.assertEqual(response.status_code, 302)
        
        minimal_asset = Asset.objects.get(asset_tag='LAP-003')
        self.assertIsNone(minimal_asset.purchase_cost)
        self.assertEqual(minimal_asset.order_number, '')
        self.assertEqual(minimal_asset.supplier, None)


class StatusLabelTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testadmin', password='testpassword', is_staff=True, is_superuser=True)
        self.client.login(username='testadmin', password='testpassword')
        
    def test_status_label_defaults_exist(self):
        # Default labels created by migration should exist
        self.assertTrue(StatusLabel.objects.filter(slug='available').exists())
        self.assertTrue(StatusLabel.objects.filter(slug='in-use').exists())
        self.assertTrue(StatusLabel.objects.filter(slug='pending-repair').exists())
        self.assertTrue(StatusLabel.objects.filter(slug='retired').exists())

    def test_status_label_crud_views(self):
        # 1. List View
        response = self.client.get(reverse('assets:statuslabel_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Available")
        self.assertContains(response, "In Use")

        # 2. Detail View
        label = StatusLabel.objects.get(slug='available')
        response = self.client.get(reverse('assets:statuslabel_detail', kwargs={'pk': label.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Available")

        # 3. Create View
        post_data = {
            'name': 'Archived (Awaiting Disposal)',
            'slug': 'archived-awaiting-disposal',
            'type': StatusLabel.TYPE_ARCHIVED,
            'description': 'Out of service assets waiting for disposal',
            'color': '333333'
        }
        response = self.client.post(reverse('assets:statuslabel_create'), data=post_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(StatusLabel.objects.filter(slug='archived-awaiting-disposal').exists())

        # 4. Update View
        new_label = StatusLabel.objects.get(slug='archived-awaiting-disposal')
        update_data = {
            'name': 'Archived (Awaiting Disposal) Updated',
            'slug': 'archived-awaiting-disposal',
            'type': StatusLabel.TYPE_ARCHIVED,
            'description': 'Updated description',
            'color': '444444'
        }
        response = self.client.post(reverse('assets:statuslabel_update', kwargs={'pk': new_label.pk}), data=update_data)
        self.assertEqual(response.status_code, 302)
        new_label.refresh_from_db()
        self.assertEqual(new_label.name, 'Archived (Awaiting Disposal) Updated')
        self.assertEqual(new_label.color, '444444')

        # 5. Delete View
        response = self.client.post(reverse('assets:statuslabel_delete', kwargs={'pk': new_label.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(StatusLabel.objects.filter(slug='archived-awaiting-disposal').exists())


class AssetMaintenanceAndLifecycleTestCase(TestCase):
    def setUp(self):
        # Create user
        self.user = User.objects.create_user(username='testadmin', password='testpassword', is_staff=True, is_superuser=True)
        self.client.login(username='testadmin', password='testpassword')
        
        # Create manufacturer and role
        self.manufacturer = Manufacturer.objects.create(name="Lenovo", slug="lenovo")
        self.role = AssetRole.objects.create(name="Laptop", slug="laptop")
        self.status = StatusLabel.objects.get(slug="available")
        
        # Create asset type with 24 months EOL
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="ThinkPad T14",
            slug="lenovo-thinkpad-t14",
            eol_months=24
        )

        # Create asset type with no EOL
        self.asset_type_no_eol = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="ThinkPad T15",
            slug="lenovo-thinkpad-t15"
        )

    def test_eol_date_calculations(self):
        import datetime
        
        # 1. Standard calculation: Purchase today + 24 months = today + 2 years
        today = datetime.date.today()
        asset = Asset.objects.create(
            name="Developer ThinkPad",
            asset_tag="LAP-101",
            asset_type=self.asset_type,
            asset_role=self.role,
            purchase_date=today,
            status=self.status
        )
        expected_year = today.year + 2
        try:
            expected_eol = datetime.date(expected_year, today.month, today.day)
        except ValueError:
            expected_eol = datetime.date(expected_year, today.month + 1, 1) - datetime.timedelta(days=1)
            
        self.assertEqual(asset.eol_date, expected_eol)
        self.assertIn("2 year", asset.time_to_eol)
        
        # 2. Month-end overflow calculation: Purchase Aug 31 2025 + 6 months -> Feb 31 -> Feb 28 2026 (non-leap)
        self.asset_type.eol_months = 6
        self.asset_type.save()
        asset.purchase_date = datetime.date(2025, 8, 31)
        asset.save()
        self.assertEqual(asset.eol_date, datetime.date(2026, 2, 28))
        
        # 3. Leap year overflow: Purchase Aug 31 2023 + 6 months -> Feb 29 2024 (leap year)
        asset.purchase_date = datetime.date(2023, 8, 31)
        asset.save()
        self.assertEqual(asset.eol_date, datetime.date(2024, 2, 29))

        # 4. No EOL months defined
        asset_no_eol = Asset.objects.create(
            name="Developer ThinkPad No EOL",
            asset_tag="LAP-102",
            asset_type=self.asset_type_no_eol,
            asset_role=self.role,
            purchase_date=datetime.date(2025, 1, 15),
            status=self.status
        )
        self.assertIsNone(asset_no_eol.eol_date)
        self.assertEqual(asset_no_eol.time_to_eol, "—")

    def test_total_cost_of_ownership_aggregation(self):
        import datetime
        asset = Asset.objects.create(
            name="Developer ThinkPad",
            asset_tag="LAP-201",
            asset_type=self.asset_type,
            asset_role=self.role,
            purchase_cost=Decimal("1200.00"),
            purchase_date=datetime.date(2025, 1, 15),
            status=self.status
        )
        
        # Initial TCO should be purchase cost
        self.assertEqual(asset.total_cost_of_ownership, Decimal("1200.00"))
        
        supplier = Supplier.objects.create(name="Lenovo Support", slug="lenovo-support")
        
        # Record maintenance 1 costing $150.00
        AssetMaintenance.objects.create(
            asset=asset,
            title="Screen replacement",
            status="completed",
            supplier=supplier,
            maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_REPAIR,
            cost=Decimal("150.00"),
            start_date=datetime.date(2025, 3, 1),
            completion_date=datetime.date(2025, 3, 5),
            notes="Screen replacement"
        )
        
        # Record maintenance 2 costing $50.00
        AssetMaintenance.objects.create(
            asset=asset,
            title="RAM upgrade",
            status="completed",
            supplier=supplier,
            maintenance_type=AssetMaintenance.MAINTENANCE_TYPE_UPGRADE,
            cost=Decimal("50.00"),
            start_date=datetime.date(2025, 4, 1),
            completion_date=datetime.date(2025, 4, 2),
            notes="RAM upgrade"
        )
        
        # Recalculate TCO: 1200 + 150 + 50 = 1400.00
        self.assertEqual(asset.total_cost_of_ownership, Decimal("1400.00"))

    def test_asset_maintenance_crud_views(self):
        import datetime
        from assets.models import AssetMaintenance
        
        asset = Asset.objects.create(
            name="Developer ThinkPad",
            asset_tag="LAP-301",
            asset_type=self.asset_type,
            asset_role=self.role,
            purchase_cost=Decimal("1200.00"),
            purchase_date=datetime.date(2025, 1, 15),
            status=self.status
        )

        supplier = Supplier.objects.create(name="Local Repair Shop", slug="local-repair-shop")
        supplier_premium = Supplier.objects.create(name="Local Repair Shop (Premium Center)", slug="local-repair-shop-premium-center")

        # 1. Create maintenance via View POST
        post_data = {
            'asset': asset.pk,
            'title': 'Fixed motherboard logic board issue',
            'status': 'completed',
            'supplier': supplier.pk,
            'maintenance_type': AssetMaintenance.MAINTENANCE_TYPE_REPAIR,
            'cost': '250.00',
            'start_date': '2025-05-10',
            'completion_date': '2025-05-15',
            'notes': 'Fixed motherboard logic board issue'
        }
        
        response = self.client.post(reverse('assets:assetmaintenance_create'), data=post_data)
        self.assertEqual(response.status_code, 302)
        
        # Verify created
        maint = AssetMaintenance.objects.get(supplier=supplier)
        self.assertEqual(maint.cost, Decimal('250.00'))
        self.assertEqual(maint.downtime_days, 5)
        
        # 2. List View
        response = self.client.get(reverse('assets:assetmaintenance_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Local Repair Shop")
        self.assertContains(response, "Repair")
        self.assertContains(response, "$250.00")
        
        # 3. Detail View
        response = self.client.get(reverse('assets:assetmaintenance_detail', kwargs={'pk': maint.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Local Repair Shop")
        self.assertContains(response, "Fixed motherboard logic board issue")
        self.assertContains(response, "5 Days")

        # 4. Update View
        update_data = {
            'asset': asset.pk,
            'title': 'Fixed motherboard logic board issue and cleaned thermal paste',
            'status': 'completed',
            'supplier': supplier_premium.pk,
            'maintenance_type': AssetMaintenance.MAINTENANCE_TYPE_REPAIR,
            'cost': '280.00',
            'start_date': '2025-05-10',
            'completion_date': '2025-05-16',
            'notes': 'Fixed motherboard logic board issue and cleaned thermal paste'
        }
        response = self.client.post(reverse('assets:assetmaintenance_update', kwargs={'pk': maint.pk}), data=update_data)
        self.assertEqual(response.status_code, 302)
        maint.refresh_from_db()
        self.assertEqual(maint.supplier, supplier_premium)
        self.assertEqual(maint.cost, Decimal('280.00'))
        self.assertEqual(maint.downtime_days, 6)

        # 5. Delete View
        response = self.client.post(reverse('assets:assetmaintenance_delete', kwargs={'pk': maint.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AssetMaintenance.objects.filter(pk=maint.pk).exists())

    def test_manufacturer_support_contacts(self):
        # 1. Create a Manufacturer via the CRUD views
        post_data = {
            'name': 'Dell Technologies',
            'slug': 'dell-technologies',
            'description': 'Premium enterprise servers and hardware supplier',
        }
        
        response = self.client.post(reverse('assets:manufacturer_create'), data=post_data)
        self.assertEqual(response.status_code, 302)
        
        # Verify created in DB
        dell = Manufacturer.objects.get(slug='dell-technologies')
        
        # Create dynamic support contact and role and assignment
        support_role, _ = ContactRole.objects.get_or_create(name='Technical Support')
        contact = Contact.objects.create(
            name='Dell Enterprise Support',
            phone='+1 (800) 456-3355',
            email='enterprise_support@dell.com',
            web_url='https://support.dell.com'
        )
        ContactAssignment.objects.create(
            contact=contact,
            role=support_role,
            content_type=ContentType.objects.get_for_model(dell),
            object_id=dell.pk,
            priority='primary'
        )

        # 2. View details in ManufacturerDetailView
        response = self.client.get(reverse('assets:manufacturer_detail', kwargs={'pk': dell.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'dell-technologies')
        self.assertContains(response, '+1 (800) 456-3355')
        self.assertContains(response, 'enterprise_support@dell.com')
        self.assertContains(response, 'https://support.dell.com')

        # 3. Create an asset under this manufacturer and view AssetDetailView
        optiplex_type = AssetType.objects.create(
            manufacturer=dell,
            model="OptiPlex 7090",
            slug="dell-optiplex-7090"
        )
        
        asset = Asset.objects.create(
            name="Reception Desk Desktop",
            asset_tag="TAG-DELL-99",
            asset_type=optiplex_type,
            serial_number="DELL-SN-12345",
            status=self.status
        )

        response = self.client.get(reverse('assets:asset_detail', kwargs={'pk': asset.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Support &amp; Warranty Details')
        self.assertContains(response, 'enterprise_support@dell.com')
        self.assertContains(response, 'DELL-SN-12345')
        self.assertContains(response, 'Dell Technologies')


class EnterpriseITAMTestCase(TestCase):
    def setUp(self):
        # Create superuser to bypass permission checks in CBVs
        self.user = User.objects.create_user(username='testadmin', password='testpassword', is_staff=True, is_superuser=True)
        self.client.login(username='testadmin', password='testpassword')

        # Retrieve default status labels populated by migrations
        self.available_status = StatusLabel.objects.get(slug='available')
        self.in_use_status = StatusLabel.objects.get(slug='in-use')

        # Create basic manufacturer and roles
        self.manufacturer = Manufacturer.objects.create(name="Apple", slug="apple")
        self.role = AssetRole.objects.create(name="Mobile Phone", slug="mobile-phone")

    def test_dynamic_custom_fieldsets_and_form_saving(self):
        # 1. Create custom fields
        sim_field = CustomField.objects.create(
            name="sim_number",
            label="SIM Number",
            field_type=CustomField.FIELD_TYPE_TEXT,
            required=True
        )
        screen_field = CustomField.objects.create(
            name="screen_size",
            label="Screen Size",
            field_type=CustomField.FIELD_TYPE_NUMBER,
            required=False
        )

        # object_types must be set so CF applies to Asset
        asset_ct = ContentType.objects.get_for_model(Asset)
        sim_field.object_types.add(asset_ct)
        screen_field.object_types.add(asset_ct)

        # 2. Create fieldset and link fields
        fieldset = CustomFieldset.objects.create(name="Phone Specs")
        fieldset.fields.add(sim_field, screen_field)

        # 3. Create asset type with fieldset
        asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="iPhone 15",
            slug="apple-iphone-15",
            custom_fieldset=fieldset
        )

        # 4. Bind and save AssetForm with custom fields
        form_data = {
            'name': 'CEO Phone',
            'asset_tag': 'PHN-001',
            'asset_type': asset_type.pk,
            'asset_role': self.role.pk,
            'status': self.available_status.pk,
            'cf_sim_number': '8904903200001234567',
            'cf_screen_size': '6.1',
            'notes': 'Dynamic specs test',
            'tags': []
        }

        # POST via view
        response = self.client.post(reverse('assets:asset_create'), data=form_data)
        self.assertEqual(response.status_code, 302)

        # Verify custom values JSON saved correctly in DB
        asset = Asset.objects.get(asset_tag='PHN-001')
        self.assertEqual(asset.custom_field_data.get('sim_number'), '8904903200001234567')
        self.assertEqual(asset.custom_field_data.get('screen_size'), '6.1')

        # Verify values display on the detail page
        detail_url = reverse('assets:asset_detail', kwargs={'pk': asset.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SIM Number")
        self.assertContains(response, "8904903200001234567")
        self.assertContains(response, "Screen Size")
        self.assertContains(response, "6.1")

    def test_straight_line_depreciation_math(self):
        import datetime
        from decimal import Decimal

        deprec = Depreciation.objects.create(
            name="10 Months Schedule", months=10,
            convention='exclude_purchase_month',
        )

        asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="MacBook Air",
            slug="apple-macbook-air",
            depreciation=deprec
        )

        today = datetime.date.today()
        purchase_date_4m = today - datetime.timedelta(days=4 * 30)

        asset_mid = Asset.objects.create(
            name="Developer MacBook",
            asset_tag="MAC-001",
            asset_type=asset_type,
            purchase_cost=Decimal("1000.00"),
            salvage_value=Decimal("0.00"),
            purchase_date=purchase_date_4m,
            status=self.available_status
        )

        months_held = (today.year - purchase_date_4m.year) * 12 + today.month - purchase_date_4m.month
        expected_val = Decimal("1000.00") - (Decimal("100.00") * Decimal(str(months_held)))
        self.assertEqual(asset_mid.current_value, expected_val)

        purchase_date_12m = today - datetime.timedelta(days=12 * 30)
        asset_expired = Asset.objects.create(
            name="Old MacBook",
            asset_tag="MAC-002",
            asset_type=asset_type,
            purchase_cost=Decimal("1000.00"),
            salvage_value=Decimal("100.00"),
            purchase_date=purchase_date_12m,
            status=self.available_status
        )
        self.assertEqual(asset_expired.current_value, Decimal("100.00"))

        asset_free = Asset.objects.create(
            name="Free MacBook",
            asset_tag="MAC-003",
            asset_type=asset_type,
            status=self.available_status
        )
        self.assertIsNone(asset_free.current_value)

        response = self.client.get(reverse('assets:asset_detail', kwargs={'pk': asset_mid.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Estimated value (indicative)")

    def test_atomic_kit_checkout_flow(self):
        from organization.models import AssetHolder, Site, Location
        from software.models import Software
        from licenses.models import License
        from inventory.models import AccessoryStock

        laptop_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="MacBook Pro",
            slug="apple-macbook-pro"
        )
        
        acc_cat = Category.objects.create(
            name="Chargers",
            slug="chargers",
            applies_to={"accessory": True}
        )

        site = Site.objects.create(name="HQ", slug="hq")
        location = Location.objects.create(name="Warehouse", slug="warehouse", site=site)

        charger = Accessory.objects.create(
            manufacturer=self.manufacturer,
            name="USB-C 96W Charger",
            slug="apple-usb-c-96w-charger",
            category=acc_cat
        )
        AccessoryStock.objects.create(
            accessory=charger,
            location=location,
            qty=5
        )

        sw = Software.objects.create(manufacturer=self.manufacturer, name="Office 365")
        license_obj = License.objects.create(software=sw, name="O365 Enterprise Seat", seats=2)

        kit = Kit.objects.create(name="Developer Onboarding Kit", description="MacBook, Charger, and O365")
        
        KitItem.objects.create(kit=kit, asset_type=laptop_type)
        KitItem.objects.create(kit=kit, accessory=charger, qty=1)
        KitItem.objects.create(kit=kit, license=license_obj)

        holder = AssetHolder.objects.create(first_name="René", last_name="Rettig", upn="rene@example.com")

        checkout_data = {
            'source_location': location.pk,
            'assigned_holder': holder.pk,
            'assigned_location': '',
            'notes': 'Onboarding René'
        }
        
        response = self.client.post(reverse('inventory:kit_checkout_modal', kwargs={'pk': kit.pk}), data=checkout_data, HTTP_HX_REQUEST='true')
        # Validation failures on HTMX form posts answer 422 with the re-rendered
        # form fragment (swapped back into the modal body by the client).
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "No available assets of type", status_code=422)

        self.assertEqual(AccessoryAssignment.objects.filter(accessory=charger).count(), 0)
        self.assertEqual(license_obj.assignments.count(), 0)

        Asset.objects.create(
            name="René MacBook Pro 16",
            asset_tag="LT-PRO-001",
            asset_type=laptop_type,
            status=self.available_status
        )

        response = self.client.post(reverse('inventory:kit_checkout_modal', kwargs={'pk': kit.pk}), data=checkout_data, HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 204)

        asset = Asset.objects.get(asset_tag="LT-PRO-001")
        self.assertEqual(asset.status, self.in_use_status)
        
        self.assertEqual(AccessoryAssignment.objects.filter(accessory=charger).count(), 1)
        self.assertEqual(charger.available, 4)

        self.assertEqual(license_obj.assignments.count(), 1)
        self.assertEqual(license_obj.available_seats, 1)

    def test_itam_layouts(self):
        supplier = Supplier.objects.create(
            name="Bechtle IT-Services",
            slug="bechtle-it-services",
            website="https://www.bechtle.com",
        )
        # Create a contact via the shared Contact system
        role, _ = ContactRole.objects.get_or_create(
            slug='primary-contact',
            defaults={'name': 'Primary Contact', 'description': 'Primary Contact'},
        )
        contact = Contact.objects.create(
            name="Markus Müller",
            email="sales@bechtle.com",
        )
        supplier_ct = ContentType.objects.get_for_model(Supplier)
        ContactAssignment.objects.create(
            contact=contact,
            role=role,
            content_type=supplier_ct,
            object_id=supplier.pk,
            priority='primary',
        )

        detail_url = reverse('assets:supplier_detail', kwargs={'pk': supplier.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bechtle IT-Services")
        self.assertContains(response, "https://www.bechtle.com")
        self.assertContains(response, "Markus Müller")
        self.assertContains(response, "Supplied Assets")
        self.assertIn('assets_table', response.context)

        category = Category.objects.create(
            name="Enterprise Laptops",
            slug="enterprise-laptops",
            color="00ff00",
            applies_to=["asset", "accessory"]
        )
        
        detail_url = reverse('assets:category_detail', kwargs={'pk': category.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enterprise Laptops")
        self.assertContains(response, "#00ff00")
        self.assertContains(response, "Asset Types")
        self.assertContains(response, "Accessories")
        self.assertIn('asset_types_table', response.context)
        self.assertIn('accessories_table', response.context)

        asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="MacBook Pro 14",
            slug="apple-macbook-pro-14",
            requestable=True
        )
        request_obj = AssetRequest.objects.create(
            requester=self.user,
            asset_type=asset_type,
            notes="Need a development machine.",
            status="approved"
        )
        
        detail_url = reverse('assets:assetrequest_detail', kwargs={'pk': request_obj.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Asset Request Details")
        self.assertContains(response, "Decision &amp; Response Details")
        self.assertContains(response, "Need a development machine.")
        self.assertContains(response, "Approved")

    def test_tenant_scoped_checkout_holders(self):
        from organization.models import Tenant, AssetHolder
        from assets.forms import AssetCheckOutForm
        from inventory.forms import AccessoryCheckoutForm, ConsumableCheckoutForm, KitCheckoutForm

        # Create two tenants
        tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")

        # Create asset holders for each tenant
        holder_a = AssetHolder.objects.create(first_name="Alice", last_name="A", upn="alice.a@example.com", tenant=tenant_a)
        holder_b = AssetHolder.objects.create(first_name="Bob", last_name="B", upn="bob.b@example.com", tenant=tenant_b)

        # 1. Test Asset checkout form filtering
        asset_a = Asset.objects.create(
            name="Laptop A",
            asset_tag="TAG-A",
            status=self.available_status,
            tenant=tenant_a
        )
        form = AssetCheckOutForm(asset=asset_a)
        holders_qs = form.fields['asset_holder'].queryset
        self.assertIn(holder_a, holders_qs)
        self.assertNotIn(holder_b, holders_qs)

        # 2. Test Accessory checkout form filtering
        from inventory.models import Accessory
        acc = Accessory.objects.create(
            manufacturer=self.manufacturer,
            name="Accessory A",
            slug="acc-a",
            tenant=tenant_b
        )
        form_acc = AccessoryCheckoutForm(accessory=acc)
        holders_qs_acc = form_acc.fields['assigned_holder'].queryset
        self.assertIn(holder_b, holders_qs_acc)
        self.assertNotIn(holder_a, holders_qs_acc)

        # 3. Test Consumable checkout form filtering
        from inventory.models import Consumable
        con = Consumable.objects.create(
            manufacturer=self.manufacturer,
            name="Consumable A",
            slug="con-a",
            tenant=tenant_a
        )
        form_con = ConsumableCheckoutForm(consumable=con)
        holders_qs_con = form_con.fields['assigned_holder'].queryset
        self.assertIn(holder_a, holders_qs_con)
        self.assertNotIn(holder_b, holders_qs_con)

        # 4. Test Kit checkout form filtering
        from inventory.models import Kit
        kit = Kit.objects.create(
            name="Kit A",
            tenant=tenant_b
        )
        form_kit = KitCheckoutForm(kit=kit)
        holders_qs_kit = form_kit.fields['assigned_holder'].queryset
        self.assertIn(holder_b, holders_qs_kit)
        self.assertNotIn(holder_a, holders_qs_kit)


class CategoryTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testadmin', password='testpassword', is_staff=True, is_superuser=True)
        self.client.login(username='testadmin', password='testpassword')

    def test_category_list_view_and_color_rendering(self):
        # Create a category with color code
        category = Category.objects.create(name="Laptop Category", slug="laptop-category", color="ff0000")

        response = self.client.get(reverse('assets:category_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Laptop Category")
        # Check that the color hex is displayed, and the badge span contains the background-color style
        self.assertContains(response, 'background-color: #ff0000')
        self.assertContains(response, '#ff0000')

