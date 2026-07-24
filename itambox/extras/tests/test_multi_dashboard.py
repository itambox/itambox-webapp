from datetime import date, timedelta
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import PermissionDenied

from organization.models import Tenant, AssetHolder, Membership, Role
from core.tests.mixins import grant
from core.managers import (
    set_current_all_accessible,
    set_current_membership,
    set_current_tenant,
    set_current_tenant_group,
)
from itambox.middleware import set_current_user
from assets.models import Asset, StatusLabel, AssetType, Manufacturer
from extras.models import Dashboard
from extras.dashboard.widgets import (
    FinancialWidget, LowStockWidget, StatusLabelsWidget
)
from extras.dashboard.utils import get_dashboard, get_default_dashboard
from extras.templatetags.dashboard import render_widget, get_widget_footer_links

User = get_user_model()


class MultiDashboardModelAndUtilsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="test_user", password="password")
        self.tenant = Tenant.objects.create(name="Acme Corp", slug="acme-corp")

    def test_get_dashboard_creates_default(self):
        # When user has no dashboard, get_dashboard should create a default one
        self.assertEqual(Dashboard.objects.filter(user=self.user).count(), 0)
        
        dashboard = get_dashboard(self.user)
        self.assertIsNotNone(dashboard)
        self.assertEqual(dashboard.user, self.user)
        self.assertEqual(dashboard.name, "Main Dashboard")
        self.assertTrue(dashboard.is_default)
        self.assertIsNone(dashboard.tenant)
        self.assertTrue(len(dashboard.layout) > 0)
        
        # Verify it persisted in DB
        self.assertEqual(Dashboard.objects.filter(user=self.user).count(), 1)

    def test_get_dashboard_with_specific_id(self):
        # Create multiple dashboards
        db1 = Dashboard.objects.create(user=self.user, name="Board 1", is_default=False)
        db2 = Dashboard.objects.create(user=self.user, name="Board 2", is_default=True)
        
        # Querying with specific ID
        fetched = get_dashboard(self.user, dashboard_id=db1.id)
        self.assertEqual(fetched.id, db1.id)
        
        # Querying with default fallback
        fetched_default = get_dashboard(self.user)
        self.assertEqual(fetched_default.id, db2.id)

    def test_get_dashboard_default_fallback(self):
        # When no dashboard is explicitly default, fall back to the first available
        db1 = Dashboard.objects.create(user=self.user, name="Board B", is_default=False)
        db2 = Dashboard.objects.create(user=self.user, name="Board A", is_default=False)
        
        fetched = get_dashboard(self.user)
        # Ordering is ordering = ['-is_default', 'name'], so Board A should be first among non-defaults
        self.assertEqual(fetched.name, "Board A")


class MultiDashboardViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="test_user", password="password")
        self.client.login(username="test_user", password="password")
        self.tenant = Tenant.objects.create(name="Acme Corp", slug="acme-corp")

        # The user must be a member of the tenant to bind a dashboard to it
        # (DashboardCreateView rejects non-member tenants).
        self.role = Role.objects.create(tenant=self.tenant, name="Member", permissions=[])
        grant(self.user, self.tenant, self.role)

        # Create two dashboards
        self.db_default = Dashboard.objects.create(
            user=self.user, name="Default Board", is_default=True, layout=get_default_dashboard()
        )
        self.db_secondary = Dashboard.objects.create(
            user=self.user, name="Secondary Board", is_default=False, layout=get_default_dashboard()
        )

    def test_dashboard_view_selection(self):
        # 1. Access dashboard without parameters -> Loads default
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_dashboard'].id, self.db_default.id)
        self.assertEqual(self.client.session['active_dashboard_id'], self.db_default.id)

        # 2. Access dashboard with explicit ?dashboard=ID
        response = self.client.get(reverse('dashboard') + f"?dashboard={self.db_secondary.id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_dashboard'].id, self.db_secondary.id)
        self.assertEqual(self.client.session['active_dashboard_id'], self.db_secondary.id)

    def test_dashboard_view_session_persistence(self):
        # Set active dashboard in session first
        session = self.client.session
        session['active_dashboard_id'] = self.db_secondary.id
        session.save()

        # Access dashboard -> should load from session cache
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_dashboard'].id, self.db_secondary.id)

    def test_dashboard_manage_modal(self):
        url = reverse('extras:dashboard_manage_modal')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Default Board", response.content.decode())
        self.assertIn("Secondary Board", response.content.decode())
        self.assertIn("Acme Corp", response.content.decode())

    def test_dashboard_create_view_creation(self):
        # Create a new dashboard with tenant scoping
        url = reverse('extras:dashboard_create')
        data = {
            'name': 'Tenant Scoped Board',
            'tenant': self.tenant.id
        }
        
        response = self.client.post(url, data)
        self.assertRedirects(response, reverse('dashboard'))
        
        # Verify created in DB
        new_db = Dashboard.objects.filter(name='Tenant Scoped Board').first()
        self.assertIsNotNone(new_db)
        self.assertEqual(new_db.tenant, self.tenant)
        self.assertFalse(new_db.is_default)
        self.assertEqual(self.client.session['active_dashboard_id'], new_db.id)

    def test_dashboard_delete_view(self):
        # 1. Deleting secondary board
        url = reverse('extras:dashboard_delete_dashboard', kwargs={'pk': self.db_secondary.id})
        response = self.client.post(url)
        self.assertRedirects(response, reverse('dashboard'))
        self.assertFalse(Dashboard.objects.filter(id=self.db_secondary.id).exists())

        # 2. Deleting last remaining board should be blocked
        url_last = reverse('extras:dashboard_delete_dashboard', kwargs={'pk': self.db_default.id})
        response_last = self.client.post(url_last)
        self.assertEqual(response_last.status_code, 302) # Redirect without deleting
        self.assertTrue(Dashboard.objects.filter(id=self.db_default.id).exists())

    def test_dashboard_delete_default_reassigns_default(self):
        # Setup session pointing to default board
        session = self.client.session
        session['active_dashboard_id'] = self.db_default.id
        session.save()

        # Delete default board
        url = reverse('extras:dashboard_delete_dashboard', kwargs={'pk': self.db_default.id})
        self.client.post(url)

        # The secondary board should be promoted to default
        self.db_secondary.refresh_from_db()
        self.assertTrue(self.db_secondary.is_default)
        self.assertEqual(self.client.session['active_dashboard_id'], self.db_secondary.id)

    def test_dashboard_rename_view(self):
        url = reverse('extras:dashboard_rename_dashboard', kwargs={'pk': self.db_secondary.id})
        response = self.client.post(url, {'name': 'Super Custom Name'})
        self.assertRedirects(response, reverse('dashboard'))
        
        self.db_secondary.refresh_from_db()
        self.assertEqual(self.db_secondary.name, 'Super Custom Name')

    def test_dashboard_set_default_view(self):
        url = reverse('extras:dashboard_set_default_dashboard', kwargs={'pk': self.db_secondary.id})
        response = self.client.post(url)
        self.assertRedirects(response, reverse('dashboard'))
        
        self.db_default.refresh_from_db()
        self.db_secondary.refresh_from_db()
        self.assertFalse(self.db_default.is_default)
        self.assertTrue(self.db_secondary.is_default)


class MultiDashboardTenantScopingTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

        # Create Tenants
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")

        # Create Users
        self.admin = User.objects.create_superuser(username="admin", password="password")
        self.user_a = User.objects.create_user(username="user_a", password="password")

        # Associate user_a with Tenant A via AssetHolder
        self.holder_a = AssetHolder.objects.create(
            user=self.user_a, first_name="User", last_name="A", upn="user.a", tenant=self.tenant_a
        )
        # Canonical (Membership-backed) access to Tenant A. The dashboard scope is
        # driven by the tenant-scoping managers, not the AssetHolder profile.
        self.role_a = Role.objects.create(tenant=self.tenant_a, name="Member A", permissions=[])
        grant(self.user_a, self.tenant_a, self.role_a)

        # Basic Asset catalogs
        self.mfr = Manufacturer.objects.create(name="Manufacturer X", slug="mfr-x")
        from assets.models import Category
        self.cat = Category.objects.create(name="Laptops", slug="laptops", applies_to={'asset': True})
        self.asset_type = AssetType.objects.create(
            manufacturer=self.mfr, model="Standard Pro", slug="standard-pro", category=self.cat
        )
        self.status = StatusLabel.objects.create(name="Active", slug="active", type="deployable")

        # Create Asset A for Tenant A
        self.asset_a = Asset.objects.create(
            name="Asset A", asset_tag="TAG-A", serial_number="SN-A",
            asset_type=self.asset_type, status=self.status, tenant=self.tenant_a,
            purchase_cost=1500.00
        )

        # Create Asset B for Tenant B
        self.asset_b = Asset.objects.create(
            name="Asset B", asset_tag="TAG-B", serial_number="SN-B",
            asset_type=self.asset_type, status=self.status, tenant=self.tenant_b,
            purchase_cost=2500.00
        )

        # Create default layouts
        self.layout_config = [
            {'widget': 'status-labels', 'title': 'Status Labels', 'visible': True, 'w': 4, 'h': 3, 'config': {'chart_type': 'list'}},
            {'widget': 'financial-overview', 'title': 'Financial Overview', 'visible': True, 'w': 4, 'h': 3, 'config': {}},
        ]

    def make_request(self, user):
        request = self.factory.get("/")
        request.user = user
        # Add basic session structure
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()
        # Bind the canonical tenant context the middleware would set: a member is
        # scoped to their (single) accessible tenant, a superuser stays global.
        # Widgets read this via the tenant-scoping managers/contextvars. The
        # autouse conftest fixture clears these after each test.
        set_current_user(user)
        set_current_tenant_group(None)
        set_current_all_accessible(False)
        if user.is_superuser:
            membership = None
            tenant = None
        else:
            membership = (
                Membership.objects.filter(user=user, is_active=True)
                .select_related("tenant").first()
            )
            tenant = membership.tenant if membership else None
        set_current_tenant(tenant)
        set_current_membership(membership)
        request.active_tenant = tenant
        request.active_tenant_group = None
        request.active_membership = membership
        request.active_all_accessible = False
        return request

    def test_global_admin_all_tenants_by_default(self):
        # Admin dashboard has no tenant scoping (tenant=None)
        db = Dashboard.objects.create(
            user=self.admin, name="Admin Global Board", tenant=None, layout=self.layout_config
        )

        # Create request and context
        request = self.make_request(self.admin)
        ctx = {'request': request, 'active_dashboard': db}

        # Render status widget and verify counts
        rendered = render_widget(ctx, db.layout[0], index=0)
        self.assertIn("Active", rendered)
        self.assertIn("2", rendered) # Admin should see both assets since no tenant constraint is on the dashboard

    def test_global_admin_scoped_to_tenant_a(self):
        # Admin dashboard scoped to Tenant A
        db = Dashboard.objects.create(
            user=self.admin, name="Admin Tenant A Board", tenant=self.tenant_a, layout=self.layout_config
        )

        # Create request and context
        request = self.make_request(self.admin)
        ctx = {'request': request, 'active_dashboard': db}

        # Render status widget and verify scoped count
        rendered = render_widget(ctx, db.layout[0], index=0)
        self.assertIn("Active", rendered)
        self.assertIn("1", rendered) # Admin should only see 1 asset because dashboard is scoped to Tenant A

        # Render financial widget and verify scoped TCO cost.
        # Money filter formats with locale thousand-separators (1,500.00) and currency symbol.
        financial_rendered = render_widget(ctx, db.layout[1], index=1)
        self.assertIn("1,500", financial_rendered)  # Scoped purchase cost from Asset A
        self.assertNotIn("2,500", financial_rendered)  # Scoped purchase cost from Asset B should not appear

    def test_global_admin_scoped_to_tenant_b(self):
        # Admin dashboard scoped to Tenant B
        db = Dashboard.objects.create(
            user=self.admin, name="Admin Tenant B Board", tenant=self.tenant_b, layout=self.layout_config
        )

        # Create request and context
        request = self.make_request(self.admin)
        ctx = {'request': request, 'active_dashboard': db}

        # Render status widget and verify scoped count
        rendered = render_widget(ctx, db.layout[0], index=0)
        self.assertIn("Active", rendered)
        self.assertIn("1", rendered) # Admin should only see 1 asset because dashboard is scoped to Tenant B

        # Render financial widget and verify scoped TCO cost.
        financial_rendered = render_widget(ctx, db.layout[1], index=1)
        self.assertIn("2,500", financial_rendered)  # Scoped purchase cost from Asset B
        self.assertNotIn("1,500", financial_rendered)  # Scoped purchase cost from Asset A should not appear

    def test_tenant_user_always_sandboxed(self):
        # Tenant A user creates a dashboard and tries to scope it to Tenant B (or leave it unscoped)
        db = Dashboard.objects.create(
            user=self.user_a, name="User A Board", tenant=self.tenant_b, layout=self.layout_config
        )

        # Create request and context
        request = self.make_request(self.user_a)
        ctx = {'request': request, 'active_dashboard': db}

        # Render status widget
        rendered = render_widget(ctx, db.layout[0], index=0)

        # Even though dashboard is scoped to Tenant B, user_a belongs to Tenant A!
        # Sandboxing must strictly enforce Tenant A limits and NEVER leak Tenant B.
        self.assertIn("Active", rendered)
        self.assertIn("1", rendered) # Must still show 1 (Asset A) because of active user sandboxing

        # Render financial widget
        financial_rendered = render_widget(ctx, db.layout[1], index=1)
        self.assertIn("1,500", financial_rendered)  # Must show Tenant A's asset cost
        self.assertNotIn("2,500", financial_rendered)  # Tenant B's asset cost must remain hidden
