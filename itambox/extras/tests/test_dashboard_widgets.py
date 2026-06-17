from datetime import date, timedelta
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from organization.models import Tenant, AssetHolder, Location, Site
from assets.models import Asset, StatusLabel, AssetType, Manufacturer
from assets.models import AssetMaintenance
from licenses.models import License
from subscriptions.models import Subscription, Provider
from core.models import ObjectChange
from extras.dashboard.widgets import (
    get_widget, NoteWidget, ObjectCountsWidget, FinancialWidget,
    StatusLabelsWidget, LicenseWidget, MaintenanceWidget, EOLAlertsWidget,
    ChangelogWidget, RenewalsWidget, LowStockWidget, AssetAgeWidget, TenantSpendWidget
)

User = get_user_model()


class DashboardWidgetsMultiTenancyTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

        # Create Tenants
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")

        # Create Users
        self.user_a = User.objects.create_user(username="user_a", password="password")
        self.user_b = User.objects.create_user(username="user_b", password="password")
        self.admin = User.objects.create_superuser(username="admin", password="password")

        # Associate users with Tenants via AssetHolder profiles
        self.holder_a = AssetHolder.objects.create(
            user=self.user_a, first_name="User", last_name="A", upn="user.a", tenant=self.tenant_a
        )
        self.holder_b = AssetHolder.objects.create(
            user=self.user_b, first_name="User", last_name="B", upn="user.b", tenant=self.tenant_b
        )

        # Basic catalog setups
        self.mfr = Manufacturer.objects.create(name="Acme Mfr", slug="acme-mfr")
        from assets.models import Category
        self.category_asset = Category.objects.create(name="Computers", slug="computers", applies_to={'asset': True})
        self.category_comp = Category.objects.create(name="RAM", slug="ram", applies_to={'component': True})
        
        self.asset_type = AssetType.objects.create(
            manufacturer=self.mfr, model="Model X", slug="model-x", category=self.category_asset, eol_months=12
        )
        self.status = StatusLabel.objects.create(name="Deployable", slug="deployable", type="deployable")

        # Setup locations
        self.site_a = Site.objects.create(name="Site A", slug="site-a", tenant=self.tenant_a)
        self.site_b = Site.objects.create(name="Site B", slug="site-b", tenant=self.tenant_b)
        self.loc_a = Location.objects.create(name="Loc A", slug="loc-a", site=self.site_a, tenant=self.tenant_a)
        self.loc_b = Location.objects.create(name="Loc B", slug="loc-b", site=self.site_b, tenant=self.tenant_b)

        # Tenant A Assets & Maintenances
        self.asset_a = Asset.objects.create(
            name="Asset A", asset_tag="TAG-A", serial_number="SN-A",
            asset_type=self.asset_type, status=self.status, tenant=self.tenant_a,
            purchase_cost=1000.00, purchase_date=date.today() - timedelta(days=300)
        )
        self.maint_a = AssetMaintenance.objects.create(
            asset=self.asset_a, title="Repair A", start_date=date.today(), cost=150.00
        )

        # Tenant B Assets & Maintenances
        self.asset_b = Asset.objects.create(
            name="Asset B", asset_tag="TAG-B", serial_number="SN-B",
            asset_type=self.asset_type, status=self.status, tenant=self.tenant_b,
            purchase_cost=2000.00, purchase_date=date.today() - timedelta(days=300)
        )
        self.maint_b = AssetMaintenance.objects.create(
            asset=self.asset_b, title="Repair B", start_date=date.today(), cost=300.00
        )

        # Setup subscriptions
        self.provider = Provider.objects.create(name="Provider X", slug="provider-x")
        self.sub_a = Subscription.objects.create(
            name="SaaS A", slug="saas-a", provider=self.provider, status="active",
            start_date=date.today(), renewal_date=date.today() + timedelta(days=30),
            renewal_cost=500.00, tenant=self.tenant_a
        )
        self.sub_b = Subscription.objects.create(
            name="SaaS B", slug="saas-b", provider=self.provider, status="active",
            start_date=date.today(), renewal_date=date.today() + timedelta(days=30),
            renewal_cost=800.00, tenant=self.tenant_b
        )

        # Setup licenses
        from software.models import Software
        self.sw = Software.objects.create(
            name="Office", manufacturer=self.mfr
        )
        self.lic_a = License.objects.create(
            name="Office 365 A", seats=10, software=self.sw, tenant=self.tenant_a
        )
        self.lic_b = License.objects.create(
            name="Office 365 B", seats=20, software=self.sw, tenant=self.tenant_b
        )

    def make_request(self, user):
        request = self.factory.get("/")
        request.user = user
        return request

    def test_financial_widget_scoping(self):
        widget = FinancialWidget()

        # Test Tenant A User
        ctx_a = widget.get_context(self.make_request(self.user_a))
        self.assertEqual(ctx_a['total_purchase_cost'], 1000.00)
        self.assertEqual(ctx_a['total_maintenance_cost'], 150.00)
        self.assertEqual(ctx_a['total_tco'], 1150.00)

        # Test Tenant B User
        ctx_b = widget.get_context(self.make_request(self.user_b))
        self.assertEqual(ctx_b['total_purchase_cost'], 2000.00)
        self.assertEqual(ctx_b['total_maintenance_cost'], 300.00)
        self.assertEqual(ctx_b['total_tco'], 2300.00)

        # Test Global Admin
        ctx_admin = widget.get_context(self.make_request(self.admin))
        self.assertEqual(ctx_admin['total_purchase_cost'], 3000.00)
        self.assertEqual(ctx_admin['total_maintenance_cost'], 450.00)
        self.assertEqual(ctx_admin['total_tco'], 3450.00)

    def test_object_counts_widget_scoping(self):
        # Configure counts for Assets and Subscriptions
        config = {'config': {'models': ['assets.asset', 'subscriptions.subscription']}}
        widget = ObjectCountsWidget(config=config)

        # User A count
        ctx_a = widget.get_context(self.make_request(self.user_a))
        counts_a = {item['label']: item['count'] for item in ctx_a['counts']}
        self.assertEqual(counts_a['Assets'], 1)
        self.assertEqual(counts_a['Subscriptions'], 1)

        # User B count
        ctx_b = widget.get_context(self.make_request(self.user_b))
        counts_b = {item['label']: item['count'] for item in ctx_b['counts']}
        self.assertEqual(counts_b['Assets'], 1)
        self.assertEqual(counts_b['Subscriptions'], 1)

        # Admin count
        ctx_admin = widget.get_context(self.make_request(self.admin))
        counts_admin = {item['label']: item['count'] for item in ctx_admin['counts']}
        self.assertEqual(counts_admin['Assets'], 2)
        self.assertEqual(counts_admin['Subscriptions'], 2)

    def test_status_labels_widget_scoping(self):
        widget = StatusLabelsWidget()

        # Tenant A User Status
        ctx_a = widget.get_context(self.make_request(self.user_a))
        self.assertEqual(ctx_a['total_assets'], 1)
        self.assertEqual(ctx_a['status_stats'][0].asset_count, 1)

        # Tenant B User Status
        ctx_b = widget.get_context(self.make_request(self.user_b))
        self.assertEqual(ctx_b['total_assets'], 1)
        self.assertEqual(ctx_b['status_stats'][0].asset_count, 1)

        # Admin Status
        ctx_admin = widget.get_context(self.make_request(self.admin))
        self.assertEqual(ctx_admin['total_assets'], 2)
        self.assertEqual(ctx_admin['status_stats'][0].asset_count, 2)

    def test_license_widget_scoping(self):
        widget = LicenseWidget()

        # Tenant A licenses
        ctx_a = widget.get_context(self.make_request(self.user_a))
        self.assertEqual(len(ctx_a['license_stats']), 1)
        self.assertEqual(ctx_a['license_stats'][0]['license'].name, "Office 365 A")

        # Tenant B licenses
        ctx_b = widget.get_context(self.make_request(self.user_b))
        self.assertEqual(len(ctx_b['license_stats']), 1)
        self.assertEqual(ctx_b['license_stats'][0]['license'].name, "Office 365 B")

    def test_maintenance_widget_scoping(self):
        widget = MaintenanceWidget()

        # Tenant A active maintenances
        ctx_a = widget.get_context(self.make_request(self.user_a))
        self.assertEqual(ctx_a['active_maintenance_count'], 1)
        self.assertEqual(ctx_a['active_maintenances'][0].title, "Repair A")

        # Tenant B active maintenances
        ctx_b = widget.get_context(self.make_request(self.user_b))
        self.assertEqual(ctx_b['active_maintenance_count'], 1)
        self.assertEqual(ctx_b['active_maintenances'][0].title, "Repair B")

    def test_eol_alerts_widget_scoping(self):
        widget = EOLAlertsWidget()

        # EOL Alerts User A
        ctx_a = widget.get_context(self.make_request(self.user_a))
        self.assertEqual(len(ctx_a['eol_alerts']), 1)
        self.assertEqual(ctx_a['eol_alerts'][0]['asset'].name, "Asset A")

        # EOL Alerts User B
        ctx_b = widget.get_context(self.make_request(self.user_b))
        self.assertEqual(len(ctx_b['eol_alerts']), 1)
        self.assertEqual(ctx_b['eol_alerts'][0]['asset'].name, "Asset B")

    def test_renewals_widget_scoping(self):
        widget = RenewalsWidget()

        # User A renewals — spend is grouped per currency (no combined total).
        ctx_a = widget.get_context(self.make_request(self.user_a))
        self.assertEqual(len(ctx_a['upcoming_renewals']), 1)
        self.assertEqual(ctx_a['upcoming_renewals'][0]['name'], "SaaS A")
        self.assertEqual(len(ctx_a['currency_spend']), 1)
        self.assertEqual(float(ctx_a['currency_spend'][0]['total']), 500.00)

        # User B renewals
        ctx_b = widget.get_context(self.make_request(self.user_b))
        self.assertEqual(len(ctx_b['upcoming_renewals']), 1)
        self.assertEqual(ctx_b['upcoming_renewals'][0]['name'], "SaaS B")
        self.assertEqual(len(ctx_b['currency_spend']), 1)
        self.assertEqual(float(ctx_b['currency_spend'][0]['total']), 800.00)

    def test_asset_age_widget_scoping(self):
        widget = AssetAgeWidget()

        # User A age distribution
        ctx_a = widget.get_context(self.make_request(self.user_a))
        self.assertEqual(ctx_a['age_buckets']['lt1y'], 1)

        # User B age distribution
        ctx_b = widget.get_context(self.make_request(self.user_b))
        self.assertEqual(ctx_b['age_buckets']['lt1y'], 1)

    def test_tenant_spend_widget_permissions(self):
        widget = TenantSpendWidget()

        # Global Admin: has access
        self.assertTrue(widget.has_permission(self.make_request(self.admin)))
        ctx_admin = widget.get_context(self.make_request(self.admin))
        self.assertEqual(len(ctx_admin['tenant_spend']), 2)

        # Standard Tenant User: blocked
        self.assertFalse(widget.has_permission(self.make_request(self.user_a)))
        
        # Test widget render returns restricted block
        render_out = widget.render(self.make_request(self.user_a))
        self.assertIn("Restricted to Global Administrators", render_out)

    def test_low_stock_widget_scoping(self):
        from inventory.models import (
            Accessory, Consumable, AccessoryStock, ConsumableStock,
            AccessoryAssignment, ConsumableAssignment
        )
        
        # 1. Create Accessory with min_qty=5 for Tenant A
        acc_a = Accessory.objects.create(
            name="Keyboard A", manufacturer=self.mfr, min_qty=5, tenant=self.tenant_a
        )
        # Create AccessoryStock: 10 units at Loc A (belonging to Tenant A)
        AccessoryStock.objects.create(accessory=acc_a, location=self.loc_a, qty=10)
        
        # Checked out assignment: 8 units to holder_a (leaving 2 available, which is < min_qty of 5)
        AccessoryAssignment.objects.create(
            accessory=acc_a, assigned_holder=self.holder_a, qty=8
        )
        
        # 2. Create Accessory with min_qty=5 for Tenant B
        acc_b = Accessory.objects.create(
            name="Keyboard B", manufacturer=self.mfr, min_qty=5, tenant=self.tenant_b
        )
        # Create AccessoryStock: 10 units at Loc B (belonging to Tenant B)
        AccessoryStock.objects.create(accessory=acc_b, location=self.loc_b, qty=10)
        
        # 3. Create Consumable with min_qty=3 for Tenant A
        con_a = Consumable.objects.create(
            name="Toner A", manufacturer=self.mfr, min_qty=3, tenant=self.tenant_a
        )
        # Create ConsumableStock: 5 units at Loc A
        ConsumableStock.objects.create(consumable=con_a, location=self.loc_a, qty=5)
        
        # Debited assignment: 4 units consumed (leaving 1 available, which is < min_qty of 3)
        ConsumableAssignment.objects.create(
            consumable=con_a, assigned_holder=self.holder_a, qty=4
        )
        
        widget = LowStockWidget()
        
        # Tenant A User context: should see acc_a (available=2 < 5) and con_a (available=1 < 3)
        ctx_a = widget.get_context(self.make_request(self.user_a))
        self.assertEqual(ctx_a['low_stock_accessory_count'], 1)
        self.assertEqual(ctx_a['low_stock_accessories'][0].name, "Keyboard A")
        self.assertEqual(ctx_a['low_stock_accessories'][0].available, 2)
        
        self.assertEqual(ctx_a['low_stock_consumable_count'], 1)
        self.assertEqual(ctx_a['low_stock_consumables'][0].name, "Toner A")
        self.assertEqual(ctx_a['low_stock_consumables'][0].available, 1)
        
        # Tenant B User context: should see no low stock items (since acc_b has 10 available >= 5, and no low consumable for B)
        ctx_b = widget.get_context(self.make_request(self.user_b))
        self.assertEqual(ctx_b['low_stock_accessory_count'], 0)
        self.assertEqual(ctx_b['low_stock_consumable_count'], 0)
        
        # Admin User context: should see acc_a and con_a
        ctx_admin = widget.get_context(self.make_request(self.admin))
        self.assertEqual(ctx_admin['low_stock_accessory_count'], 1)
        self.assertEqual(ctx_admin['low_stock_consumable_count'], 1)

    def test_admin_custom_tenant_filtering(self):
        # Configure LowStockWidget to target Tenant B
        config_b = {'config': {'tenant_id': str(self.tenant_b.id)}}
        widget_low_stock = LowStockWidget(config=config_b)
        
        ctx_admin_low_stock = widget_low_stock.get_context(self.make_request(self.admin))
        # Since Tenant B has no low stock accessories or consumables, count should be 0
        self.assertEqual(ctx_admin_low_stock['low_stock_accessory_count'], 0)
        self.assertEqual(ctx_admin_low_stock['low_stock_consumable_count'], 0)
        
        # Configure StatusLabelsWidget to target Tenant A
        config_a = {'config': {'tenant_id': str(self.tenant_a.id)}}
        widget_status = StatusLabelsWidget(config=config_a)
        
        ctx_admin_status = widget_status.get_context(self.make_request(self.admin))
        # Admin should see total_assets = 1 (Tenant A's asset) and not 2
        self.assertEqual(ctx_admin_status['total_assets'], 1)
