from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.utils import timezone
from procurement.models import PurchaseOrder, PurchaseOrderLine, FulfillmentLink
from assets.models import Supplier, AssetRequest, AssetType, Manufacturer
from organization.models import Location, Site
from software.models import Software
from licenses.models import License

User = get_user_model()

class ProcurementStatusTransitionTests(TestCase):
    def setUp(self):
        # Create user
        self.user = User.objects.create_superuser(username='testuser', email='test@example.com', password='password')
        
        # Create a site and location
        self.site = Site.objects.create(name='Test Site', slug='test-site')
        self.location = Location.objects.create(name='Test Location', slug='test-location', site=self.site)
        
        # Create a supplier
        self.supplier = Supplier.objects.create(name='Test Supplier', slug='test-supplier')
        
        # Create manufacturer and asset type for lines
        self.manufacturer = Manufacturer.objects.create(name='Test Manufacturer', slug='test-manufacturer')
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model='Test Model',
            slug='test-model',
            requestable=True
        )
        
        # Create software and license
        self.software = Software.objects.create(
            name='Test Software Product',
            manufacturer=self.manufacturer
        )
        self.license = License.objects.create(
            name='Test License Product',
            software=self.software,
            seats=10
        )
        
        # Create purchase order (starts in draft status)
        self.po = PurchaseOrder.objects.create(
            order_number='PO-1001',
            supplier=self.supplier,
            destination_location=self.location,
            created_by=self.user
        )

    def test_approve_purchase_order_successful(self):
        # Must have at least one line item to approve
        PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            asset_type=self.asset_type,
            qty_ordered=5,
            unit_price=10.00
        )
        from procurement.services import approve_purchase_order
        approve_purchase_order(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.STATUS_APPROVED)

    def test_approve_purchase_order_no_lines_fails(self):
        from procurement.services import approve_purchase_order
        with self.assertRaises(ValidationError):
            approve_purchase_order(self.po)

    def test_order_purchase_order_successful(self):
        # Add line, approve first
        PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            asset_type=self.asset_type,
            qty_ordered=5,
            unit_price=10.00
        )
        from procurement.services import approve_purchase_order, order_purchase_order
        approve_purchase_order(self.po)
        order_purchase_order(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.STATUS_ORDERED)
        self.assertIsNotNone(self.po.order_date)

    def test_order_purchase_order_without_approval_fails(self):
        from procurement.services import order_purchase_order
        with self.assertRaises(ValidationError):
            order_purchase_order(self.po)

    def test_cancel_purchase_order_reverts_linked_requests(self):
        # Create a PO line
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            asset_type=self.asset_type,
            qty_ordered=2,
            unit_price=100.00
        )
        # Create an asset request and fulfillment link
        request = AssetRequest.objects.create(
            requester=self.user,
            asset_type=self.asset_type,
            qty=2,
            status=AssetRequest.STATUS_PROCUREMENT
        )
        FulfillmentLink.objects.create(
            asset_request=request,
            purchase_order_line=line,
            qty_allocated=2
        )
        
        # Cancel the PO
        from procurement.services import cancel_purchase_order
        cancel_purchase_order(self.po)
        
        self.po.refresh_from_db()
        request.refresh_from_db()
        
        self.assertEqual(self.po.status, PurchaseOrder.STATUS_CANCELLED)
        self.assertEqual(request.status, AssetRequest.STATUS_APPROVED)
        self.assertFalse(FulfillmentLink.objects.filter(purchase_order_line=line).exists())

    def test_reopen_cancelled_purchase_order(self):
        from procurement.services import cancel_purchase_order, reopen_purchase_order
        cancel_purchase_order(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.STATUS_CANCELLED)
        
        reopen_purchase_order(self.po)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.STATUS_DRAFT)

    def test_license_purchase_order_line_creation_and_validation(self):
        # Successful creation of license line item
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            license=self.license,
            qty_ordered=3,
            unit_price=50.00
        )
        self.assertEqual(line.license, self.license)
        self.assertEqual(str(line), f"3x {self.license} for PO PO-1001")
        
        # Validation fails when both license and asset_type are specified
        line.asset_type = self.asset_type
        with self.assertRaises(ValidationError):
            line.clean()
            
        # Validation fails when nothing is specified
        line.asset_type = None
        line.license = None
        with self.assertRaises(ValidationError):
            line.clean()

    def test_receive_license_line_increases_qty_received(self):
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            license=self.license,
            qty_ordered=5,
            unit_price=50.00
        )
        from procurement.services import approve_purchase_order, order_purchase_order, receive_purchase_order
        approve_purchase_order(self.po)
        order_purchase_order(self.po)
        
        # Receive 3 licenses
        receive_purchase_order(self.po, {line.pk: 3})
        
        line.refresh_from_db()
        self.assertEqual(line.qty_received, 3)
        self.assertEqual(line.qty_outstanding, 2)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.STATUS_PARTIAL)
        
        # Receive the remaining 2 licenses
        receive_purchase_order(self.po, {line.pk: 2})
        line.refresh_from_db()
        self.assertEqual(line.qty_received, 5)
        self.assertEqual(line.qty_outstanding, 0)
        self.po.refresh_from_db()
        self.assertEqual(self.po.status, PurchaseOrder.STATUS_RECEIVED)

    def test_receiving_status_guards(self):
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            asset_type=self.asset_type,
            qty_ordered=5,
            unit_price=10.00
        )
        from procurement.services import receive_purchase_order
        # Try to receive while in Draft
        with self.assertRaises(ValidationError):
            receive_purchase_order(self.po, {line.pk: 2})

    def test_receive_form_view_post_does_not_crash(self):
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            asset_type=self.asset_type,
            qty_ordered=5,
            unit_price=10.00
        )
        from procurement.services import approve_purchase_order, order_purchase_order
        approve_purchase_order(self.po)
        order_purchase_order(self.po)
        
        self.client.force_login(self.user)
        response = self.client.post(
            f'/procurement/orders/{self.po.pk}/receive/',
            {
                'form-TOTAL_FORMS': '1',
                'form-INITIAL_FORMS': '1',
                'form-MIN_NUM_FORMS': '0',
                'form-MAX_NUM_FORMS': '1000',
                'form-0-line_id': line.pk,
                'form-0-qty_to_receive': 2,
                'step': '1'
            }
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'procurement/purchaseorder_receive_step2.html')

    def test_receive_form_view_step2_submit_empty_details_does_not_crash(self):
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            asset_type=self.asset_type,
            qty_ordered=5,
            unit_price=10.00
        )
        from procurement.services import approve_purchase_order, order_purchase_order
        approve_purchase_order(self.po)
        order_purchase_order(self.po)
        
        # Setup session for step 2
        session = self.client.session
        session['receive_po_quantities'] = {line.pk: 2}
        session.save()
        
        # Deployable status label is required by the receiving service
        from assets.models import StatusLabel
        StatusLabel.objects.get_or_create(name='Deployable', type='deployable', slug='deployable')
        
        self.client.force_login(self.user)
        response = self.client.post(
            f'/procurement/orders/{self.po.pk}/receive/',
            {
                'form-TOTAL_FORMS': '2',
                'form-INITIAL_FORMS': '2',
                'form-MIN_NUM_FORMS': '0',
                'form-MAX_NUM_FORMS': '1000',
                # Form 0: filled out
                'form-0-line_id': line.pk,
                'form-0-serial_number': 'SN123',
                'form-0-asset_tag': 'TAG123',
                'form-0-name': 'Test Asset 1',
                # Form 1: only line_id is submitted (simulating blank user input)
                'form-1-line_id': line.pk,
                'step': '2'
            }
        )
        # It should redirect to absolute URL on success
        self.assertEqual(response.status_code, 302)

    def test_receive_po_with_blank_serial_stores_empty_string(self):
        """Regression: blank serial_number must store '' not NULL (IntegrityError guard)."""
        from assets.models import StatusLabel
        StatusLabel.objects.get_or_create(name='Deployable', type='deployable', slug='deployable')

        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            asset_type=self.asset_type,
            qty_ordered=1,
            unit_price=99.00
        )
        from procurement.services import approve_purchase_order, order_purchase_order, receive_purchase_order
        approve_purchase_order(self.po)
        order_purchase_order(self.po)

        # Blank serial — the form allows "Optional" and must not IntegrityError.
        receive_purchase_order(self.po, {line.pk: 1}, asset_details=[{'line_id': line.pk, 'serial_number': '', 'asset_tag': '', 'name': 'Test Asset'}])

        from assets.models import Asset
        asset = Asset.objects.get(purchase_order_line=line)
        self.assertEqual(asset.serial_number, '', "Blank serial should be stored as '' not NULL")

    def test_line_edit_view_get_returns_editing_line_id(self):
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            asset_type=self.asset_type,
            qty_ordered=5,
            unit_price=10.00
        )
        self.client.force_login(self.user)
        response = self.client.get(f'/procurement/lines/{line.pk}/edit/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="qty_ordered"')
        self.assertContains(response, 'name="unit_price"')

    def test_line_edit_view_post_updates_price_and_qty(self):
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po,
            asset_type=self.asset_type,
            qty_ordered=5,
            unit_price=10.00
        )
        self.client.force_login(self.user)
        response = self.client.post(
            f'/procurement/lines/{line.pk}/edit/',
            {
                'qty_ordered': 12,
                'unit_price': '15.50'
            }
        )
        self.assertEqual(response.status_code, 200)
        line.refresh_from_db()
        self.assertEqual(line.qty_ordered, 12)
        self.assertEqual(float(line.unit_price), 15.50)


from procurement.forms import PurchaseOrderForm, PurchaseOrderLineForm
from organization.models import Tenant
from core.currency import CURRENCY_CHOICES

class PurchaseOrderFormTests(TestCase):
    def setUp(self):
        self.site = Site.objects.create(name='Test Site', slug='test-site')
        self.location = Location.objects.create(name='Test Location', slug='test-location', site=self.site)
        self.supplier = Supplier.objects.create(name='Test Supplier', slug='test-supplier')
        self.tenant = Tenant.objects.create(name='Test Tenant', slug='test-tenant')

    def test_po_form_has_tenant_field(self):
        form = PurchaseOrderForm()
        self.assertIn('tenant', form.fields)

    def test_po_form_saves_tenant(self):
        form_data = {
            'order_number': 'PO-TEST-123',
            'supplier': self.supplier.pk,
            'order_date': '2026-06-07',
            'expected_delivery_date': '2026-06-14',
            'destination_location': self.location.pk,
            'tenant': self.tenant.pk,
            'notes': 'Test notes'
        }
        form = PurchaseOrderForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        po = form.save()
        self.assertEqual(po.tenant, self.tenant)


class PurchaseOrderLineFormTests(TestCase):
    def setUp(self):
        self.manufacturer = Manufacturer.objects.create(name='Test Manufacturer', slug='test-manufacturer')
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model='Test Model',
            slug='test-model',
            requestable=True
        )
        self.software = Software.objects.create(
            name='Test Software Product',
            manufacturer=self.manufacturer
        )
        self.license = License.objects.create(
            name='Test License Product',
            software=self.software,
            seats=10
        )

    def test_form_validation_valid_asset_type(self):
        form_data = {
            'item_category': 'asset_type',
            'asset_type': self.asset_type.pk,
            'qty_ordered': 3,
            'unit_price': 15.00
        }
        form = PurchaseOrderLineForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        cleaned_data = form.cleaned_data
        self.assertEqual(cleaned_data['asset_type'], self.asset_type)
        # Ensure other fields are cleared
        self.assertIsNone(cleaned_data['license'])
        self.assertIsNone(cleaned_data['component'])

    def test_form_validation_valid_license(self):
        form_data = {
            'item_category': 'license',
            'license': self.license.pk,
            'qty_ordered': 2,
            'unit_price': 100.00
        }
        form = PurchaseOrderLineForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        cleaned_data = form.cleaned_data
        self.assertEqual(cleaned_data['license'], self.license)
        # Ensure other fields are cleared
        self.assertIsNone(cleaned_data['asset_type'])

    def test_form_validation_missing_category_fails(self):
        form_data = {
            'asset_type': self.asset_type.pk,
            'qty_ordered': 3,
            'unit_price': 15.00
        }
        form = PurchaseOrderLineForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)
        self.assertIn("Please select an Item Category.", form.errors['__all__'])

    def test_form_validation_missing_item_field_fails(self):
        form_data = {
            'item_category': 'asset_type',
            'qty_ordered': 3,
            'unit_price': 15.00
        }
        form = PurchaseOrderLineForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('asset_type', form.errors)
        self.assertIn("Please select a Asset type.", form.errors['asset_type'])


class PurchaseOrderCurrencyTests(TestCase):
    """Tests for the per-PO currency field and the PurchaseOrderLine.currency property."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='currencyuser', email='currency@example.com', password='password'
        )
        self.site = Site.objects.create(name='Currency Site', slug='currency-site')
        self.location = Location.objects.create(
            name='Currency Location', slug='currency-location', site=self.site
        )
        self.supplier = Supplier.objects.create(name='Currency Supplier', slug='currency-supplier')
        self.manufacturer = Manufacturer.objects.create(
            name='Currency Manufacturer', slug='currency-manufacturer'
        )
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model='Currency Model',
            slug='currency-model',
            requestable=False,
        )

    def _make_po(self, currency=''):
        return PurchaseOrder.objects.create(
            order_number=f'PO-CUR-{currency or "blank"}',
            supplier=self.supplier,
            destination_location=self.location,
            created_by=self.user,
            currency=currency,
        )

    # --- PurchaseOrder.currency field ---

    def test_currency_defaults_to_blank(self):
        """A new PO should default to blank (inherit tenant currency at display time)."""
        po = self._make_po()
        self.assertEqual(po.currency, '')

    def test_currency_explicit_value_stored(self):
        """An explicit ISO currency code must round-trip through the DB."""
        po = self._make_po(currency='USD')
        po.refresh_from_db()
        self.assertEqual(po.currency, 'USD')

    def test_currency_choices_are_valid(self):
        """All CURRENCY_CHOICES codes must be accepted by the field."""
        valid_codes = [code for code, _ in CURRENCY_CHOICES]
        for code in valid_codes:
            po = PurchaseOrder(
                order_number=f'PO-CHK-{code}',
                supplier=self.supplier,
                destination_location=self.location,
                created_by=self.user,
                currency=code,
            )
            # full_clean validates choices; this must not raise
            po.full_clean()

    def test_currency_field_in_form(self):
        """PurchaseOrderForm must expose the currency field."""
        form = PurchaseOrderForm()
        self.assertIn('currency', form.fields)

    def test_form_saves_currency(self):
        """PurchaseOrderForm must persist an explicit currency on save."""
        tenant = Tenant.objects.create(name='Curr Tenant', slug='curr-tenant')
        form_data = {
            'order_number': 'PO-FORM-GBP',
            'supplier': self.supplier.pk,
            'currency': 'GBP',
            'order_date': '2026-06-14',
            'expected_delivery_date': '2026-07-01',
            'destination_location': self.location.pk,
            'tenant': tenant.pk,
            'notes': '',
        }
        form = PurchaseOrderForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        po = form.save()
        self.assertEqual(po.currency, 'GBP')

    def test_form_saves_blank_currency(self):
        """An empty currency selection must save as blank (tenant-fallback semantics)."""
        tenant = Tenant.objects.create(name='Curr Tenant 2', slug='curr-tenant-2')
        form_data = {
            'order_number': 'PO-FORM-BLANK',
            'supplier': self.supplier.pk,
            'currency': '',
            'order_date': '2026-06-14',
            'expected_delivery_date': '2026-07-01',
            'destination_location': self.location.pk,
            'tenant': tenant.pk,
            'notes': '',
        }
        form = PurchaseOrderForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        po = form.save()
        self.assertEqual(po.currency, '')

    # --- PurchaseOrderLine.currency property ---

    def test_line_currency_delegates_to_po(self):
        """PurchaseOrderLine.currency must return the parent PO's currency."""
        po = self._make_po(currency='EUR')
        line = PurchaseOrderLine.objects.create(
            purchase_order=po,
            asset_type=self.asset_type,
            qty_ordered=2,
            unit_price='99.99',
        )
        self.assertEqual(line.currency, 'EUR')

    def test_line_currency_blank_when_po_currency_blank(self):
        """When the PO has no explicit currency, the line property also returns blank."""
        po = self._make_po(currency='')
        line = PurchaseOrderLine.objects.create(
            purchase_order=po,
            asset_type=self.asset_type,
            qty_ordered=1,
            unit_price='10.00',
        )
        self.assertEqual(line.currency, '')

