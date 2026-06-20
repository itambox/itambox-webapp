from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.utils import timezone
from procurement.models import PurchaseOrder, PurchaseOrderLine, FulfillmentLink
from assets.models import Supplier, AssetRequest, AssetType, Manufacturer
from assets.choices import RequestStatusChoices
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
            status=RequestStatusChoices.PROCUREMENT
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
        self.assertEqual(request.status, RequestStatusChoices.APPROVED)
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

    def test_receive_component_locks_stock_row(self):
        """WS2-2: the component-stock read-modify-write must hold a row lock
        (SELECT ... FOR UPDATE) so concurrent receipts cannot lose an increment.
        Fails before the fix — plain get_or_create emits no FOR UPDATE on the stock row."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from assets.models import StatusLabel, Category
        from inventory.models import Component, ComponentStock
        from procurement.services import (
            approve_purchase_order, order_purchase_order, receive_purchase_order,
        )

        StatusLabel.objects.get_or_create(
            name='Deployable', defaults={'type': 'deployable', 'slug': 'deployable'}
        )
        category = Category.objects.create(
            name='Comp Cat', slug='comp-cat', applies_to={'component': True}
        )
        component = Component.objects.create(
            name='RAM', manufacturer=self.manufacturer, category=category
        )
        line = PurchaseOrderLine.objects.create(
            purchase_order=self.po, component=component, qty_ordered=10, unit_price=5.00,
        )
        approve_purchase_order(self.po)
        order_purchase_order(self.po)

        with CaptureQueriesContext(connection) as ctx:
            receive_purchase_order(self.po, {line.pk: 5})

        stock = ComponentStock.objects.get(component=component, location=self.location)
        self.assertEqual(stock.qty, 5)

        locked = any(
            'componentstock' in q['sql'].lower() and 'for update' in q['sql'].lower()
            for q in ctx.captured_queries
        )
        self.assertTrue(
            locked, "receive_purchase_order must lock the component stock row (FOR UPDATE)"
        )

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


from procurement.forms import PurchaseOrderForm, PurchaseOrderLineForm, ContractForm
from procurement.models import Contract
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
        self.assertIn("Please select a Asset Type.", form.errors['asset_type'])


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


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------

import datetime
from django.utils import timezone


class ContractModelTests(TestCase):
    """Unit tests for the Contract model."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name='Contract Tenant', slug='contract-tenant')
        self.supplier = Supplier.objects.create(name='Contract Supplier', slug='contract-supplier')

    def _make_contract(self, **kwargs):
        defaults = dict(
            name='Hardware Support Agreement',
            contract_number='CTR-001',
            contract_type='support',
            status='active',
            supplier=self.supplier,
            tenant=self.tenant,
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2027, 1, 1),
        )
        defaults.update(kwargs)
        return Contract.objects.create(**defaults)

    # --- Basic creation ---

    def test_contract_creation_defaults(self):
        contract = self._make_contract()
        self.assertEqual(contract.status, 'active')
        self.assertEqual(contract.contract_type, 'support')
        self.assertFalse(contract.auto_renew)
        self.assertEqual(contract.currency, '')  # CurrencyField blanks = tenant default
        self.assertIsNone(contract.cost)

    def test_contract_str(self):
        contract = self._make_contract()
        self.assertEqual(str(contract), 'CTR-001 – Hardware Support Agreement')

    def test_get_absolute_url(self):
        contract = self._make_contract()
        self.assertIn(str(contract.pk), contract.get_absolute_url())

    # --- Uniqueness: contract_number per-active row ---

    def test_unique_contract_number_active_enforced(self):
        """Two active contracts with the same number must raise IntegrityError."""
        self._make_contract(contract_number='CTR-DUP')
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            # Bypass full_clean — hit the DB constraint directly
            Contract.objects.create(
                name='Duplicate',
                contract_number='CTR-DUP',
                contract_type='maintenance',
                status='draft',
                start_date=datetime.date(2026, 1, 1),
                end_date=datetime.date(2027, 1, 1),
            )

    def test_unique_contract_number_allows_soft_deleted_resurrection(self):
        """After soft-deleting a contract, a new one with the same number is allowed."""
        contract = self._make_contract(contract_number='CTR-REBORN')
        contract.delete()  # soft-delete sets deleted_at
        # This must not raise — the old row is soft-deleted so the constraint lifts
        new_contract = self._make_contract(contract_number='CTR-REBORN')
        self.assertIsNotNone(new_contract.pk)

    # --- Assets M2M ---

    def test_assets_m2m(self):
        from assets.models import Asset, AssetType, Manufacturer, StatusLabel
        manufacturer = Manufacturer.objects.create(name='M2M Mfr', slug='m2m-mfr')
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer, model='M2M Model', slug='m2m-model'
        )
        status_label, _ = StatusLabel.objects.get_or_create(
            name='Deployable', defaults={'type': 'deployable', 'slug': 'deployable'}
        )
        asset = Asset.objects.create(
            asset_type=asset_type,
            name='Test Asset',
            asset_tag='TAG-M2M',
            status=status_label,
        )
        contract = self._make_contract()
        contract.assets.add(asset)
        self.assertIn(asset, contract.assets.all())
        self.assertIn(contract, asset.contracts.all())

    # --- Currency field ---

    def test_currency_blank_default(self):
        contract = self._make_contract()
        self.assertEqual(contract.currency, '')

    def test_currency_explicit(self):
        contract = self._make_contract(currency='USD')
        contract.refresh_from_db()
        self.assertEqual(contract.currency, 'USD')

    # --- Date properties ---

    def test_days_until_expiry_future(self):
        future_end = timezone.now().date() + datetime.timedelta(days=15)
        contract = self._make_contract(end_date=future_end)
        self.assertEqual(contract.days_until_expiry, 15)

    def test_is_expiring_soon_true(self):
        future_end = timezone.now().date() + datetime.timedelta(days=10)
        contract = self._make_contract(end_date=future_end)
        self.assertTrue(contract.is_expiring_soon)

    def test_is_expiring_soon_false_when_far(self):
        future_end = timezone.now().date() + datetime.timedelta(days=90)
        contract = self._make_contract(end_date=future_end)
        self.assertFalse(contract.is_expiring_soon)

    def test_days_until_expiry_negative_when_past(self):
        past_end = timezone.now().date() - datetime.timedelta(days=5)
        contract = self._make_contract(
            start_date=datetime.date(2020, 1, 1),
            end_date=past_end,
        )
        self.assertLess(contract.days_until_expiry, 0)

    # --- Validation ---

    def test_clean_raises_when_end_before_start(self):
        from django.core.exceptions import ValidationError
        contract = Contract(
            name='Bad Dates',
            contract_number='CTR-BAD',
            contract_type='lease',
            status='draft',
            start_date=datetime.date(2026, 6, 1),
            end_date=datetime.date(2026, 1, 1),  # before start
        )
        with self.assertRaises(ValidationError):
            contract.clean()

    # --- PO link ---

    def test_purchase_order_link(self):
        self.site = Site.objects.create(name='Contract Site', slug='contract-site')
        self.location = Location.objects.create(name='Contract Loc', slug='contract-loc', site=self.site)
        user = User.objects.create_superuser(username='ctruser', email='ctr@example.com', password='pw')
        po = PurchaseOrder.objects.create(
            order_number='PO-CTR-001',
            supplier=self.supplier,
            destination_location=self.location,
            created_by=user,
        )
        contract = self._make_contract(purchase_order=po)
        self.assertEqual(contract.purchase_order, po)
        self.assertIn(contract, po.contracts.all())


class ContractFormTests(TestCase):
    """Smoke tests for ContractForm."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name='Form Tenant', slug='form-tenant')
        self.supplier = Supplier.objects.create(name='Form Supplier', slug='form-supplier')

    def test_contract_form_valid(self):
        form_data = {
            'name': 'Annual Support',
            'contract_number': 'CTR-FORM-001',
            'contract_type': 'support',
            'status': 'draft',
            'supplier': self.supplier.pk,
            'cost': '12000.00',
            'currency': 'EUR',
            'billing_cycle': 'annual',
            'start_date': '2026-01-01',
            'end_date': '2027-01-01',
            'renewal_date': '',
            'auto_renew': False,
            'sla_response_time': '4 business hours',
            'sla_resolution_time': '8 business hours',
            'coverage_hours': '24x7',
            'sla_terms': '',
            'assets': [],
            'purchase_order': '',
            'tenant': self.tenant.pk,
            'notes': '',
        }
        form = ContractForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        contract = form.save()
        self.assertEqual(contract.contract_number, 'CTR-FORM-001')
        self.assertEqual(contract.currency, 'EUR')

    def test_contract_form_invalid_missing_dates(self):
        form_data = {
            'name': 'No Dates',
            'contract_number': 'CTR-NODATE',
            'contract_type': 'maintenance',
            'status': 'draft',
            # start_date and end_date deliberately omitted
        }
        form = ContractForm(data=form_data)
        self.assertFalse(form.is_valid())
        self.assertIn('start_date', form.errors)
        self.assertIn('end_date', form.errors)


class ContractViewSmokeTests(TestCase):
    """List and detail view smoke tests."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='contractview', email='cv@example.com', password='password'
        )
        self.supplier = Supplier.objects.create(name='View Supplier', slug='view-supplier')
        self.contract = Contract.objects.create(
            name='View Test Contract',
            contract_number='CTR-VIEW-001',
            contract_type='support',
            status='active',
            supplier=self.supplier,
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2027, 1, 1),
        )

    def test_contract_list_view(self):
        self.client.force_login(self.user)
        response = self.client.get('/procurement/contracts/')
        self.assertEqual(response.status_code, 200)

    def test_contract_detail_view(self):
        self.client.force_login(self.user)
        response = self.client.get(f'/procurement/contracts/{self.contract.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.contract.contract_number)


# ---------------------------------------------------------------------------
# Contract.cost_center FK tests
# ---------------------------------------------------------------------------

class ContractCostCenterTests(TestCase):
    """Tests for the cost_center FK on Contract."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name='CC Tenant', slug='cc-tenant')
        self.supplier = Supplier.objects.create(name='CC Supplier', slug='cc-supplier')

    def _make_contract(self, **kwargs):
        defaults = dict(
            name='CC Contract',
            contract_number='CTR-CC-001',
            contract_type='support',
            status='draft',
            tenant=self.tenant,
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2027, 1, 1),
        )
        defaults.update(kwargs)
        return Contract.objects.create(**defaults)

    def test_cost_center_defaults_to_null(self):
        """A new Contract must have cost_center=None when not specified."""
        contract = self._make_contract()
        self.assertIsNone(contract.cost_center)

    def test_cost_center_blank_is_valid_in_form(self):
        """ContractForm must be valid when cost_center is omitted."""
        form_data = {
            'name': 'No CC Contract',
            'contract_number': 'CTR-NOCC-001',
            'contract_type': 'support',
            'status': 'draft',
            'supplier': self.supplier.pk,
            'cost': '',
            'currency': '',
            'billing_cycle': 'annual',
            'start_date': '2026-01-01',
            'end_date': '2027-01-01',
            'renewal_date': '',
            'auto_renew': False,
            'sla_response_time': '',
            'sla_resolution_time': '',
            'coverage_hours': '',
            'sla_terms': '',
            'assets': [],
            'purchase_order': '',
            'cost_center': '',
            'tenant': self.tenant.pk,
            'notes': '',
        }
        from procurement.forms import ContractForm
        form = ContractForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        contract = form.save()
        self.assertIsNone(contract.cost_center)

    def test_cost_center_field_present_in_form(self):
        """ContractForm must expose the cost_center field."""
        from procurement.forms import ContractForm
        form = ContractForm()
        self.assertIn('cost_center', form.fields)


# ---------------------------------------------------------------------------
# ContractForm asset queryset tenant-scoping test
# ---------------------------------------------------------------------------

class ContractFormAssetScopingTests(TestCase):
    """Verify that ContractForm scopes the assets queryset to the active tenant."""

    def setUp(self):
        from assets.models import AssetType, Manufacturer, StatusLabel
        self.tenant_a = Tenant.objects.create(name='Scope Tenant A', slug='scope-tenant-a')
        self.tenant_b = Tenant.objects.create(name='Scope Tenant B', slug='scope-tenant-b')

        manufacturer = Manufacturer.objects.create(name='Scope Mfr', slug='scope-mfr')
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer, model='Scope Model', slug='scope-model'
        )
        status_label, _ = StatusLabel.objects.get_or_create(
            name='Deployable', defaults={'type': 'deployable', 'slug': 'deployable'}
        )
        from assets.models import Asset
        self.asset_a = Asset.objects.create(
            asset_type=asset_type,
            name='Asset A',
            asset_tag='TAG-SCOPE-A',
            status=status_label,
            tenant=self.tenant_a,
        )
        self.asset_b = Asset.objects.create(
            asset_type=asset_type,
            name='Asset B',
            asset_tag='TAG-SCOPE-B',
            status=status_label,
            tenant=self.tenant_b,
        )

    def test_assets_queryset_scoped_to_active_tenant(self):
        """When Tenant A is active, ContractForm.assets must only list Tenant A's assets."""
        from core.managers import set_current_tenant
        from procurement.forms import ContractForm

        set_current_tenant(self.tenant_a)
        try:
            form = ContractForm()
            qs = form.fields['assets'].queryset
            self.assertIn(self.asset_a, qs)
            self.assertNotIn(self.asset_b, qs)
        finally:
            set_current_tenant(None)

    def test_assets_queryset_unfiltered_when_no_tenant(self):
        """When no tenant is active, ContractForm.assets falls back to the default manager queryset."""
        from core.managers import set_current_tenant
        from procurement.forms import ContractForm

        set_current_tenant(None)
        form = ContractForm()
        qs = form.fields['assets'].queryset
        # Both assets should be visible (no tenant restriction)
        self.assertIn(self.asset_a, qs)
        self.assertIn(self.asset_b, qs)


# ---------------------------------------------------------------------------
# Contract REST API smoke tests
# ---------------------------------------------------------------------------

class ContractAPITests(TestCase):
    """Smoke tests for the Contract REST API (list + create)."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='apiuser', email='api@example.com', password='password'
        )
        self.tenant = Tenant.objects.create(name='API Tenant', slug='api-tenant')
        self.supplier = Supplier.objects.create(name='API Supplier', slug='api-supplier')
        self.contract = Contract.objects.create(
            name='API Contract',
            contract_number='CTR-API-001',
            contract_type='support',
            status='active',
            supplier=self.supplier,
            tenant=self.tenant,
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2027, 1, 1),
        )

    def test_contract_api_list(self):
        """GET /api/procurement/contracts/ returns 200 for a superuser."""
        self.client.force_login(self.user)
        response = self.client.get('/api/procurement/contracts/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('results', data)

    def test_contract_api_detail(self):
        """GET /api/procurement/contracts/<pk>/ returns the contract."""
        self.client.force_login(self.user)
        response = self.client.get(f'/api/procurement/contracts/{self.contract.pk}/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['contract_number'], 'CTR-API-001')

    def test_contract_api_create(self):
        """POST /api/procurement/contracts/ creates a new contract."""
        self.client.force_login(self.user)
        payload = {
            'name': 'Created via API',
            'contract_number': 'CTR-API-NEW',
            'contract_type': 'maintenance',
            'status': 'draft',
            'start_date': '2026-06-01',
            'end_date': '2027-06-01',
            'billing_cycle': 'annual',
            'tenant_id': self.tenant.pk,
        }
        response = self.client.post(
            '/api/procurement/contracts/',
            data=payload,
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['contract_number'], 'CTR-API-NEW')

