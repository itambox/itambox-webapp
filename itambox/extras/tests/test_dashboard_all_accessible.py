"""Issue #133 regression suite.

Two defects on the dashboard under the canonical "All accessible tenants" scope
for a non-superuser member:

1. Every data-backed widget rendered empty states / ``0`` / ``0.00`` because
   ``extras/dashboard/widgets.py`` kept a second, divergent scoping layer
   (``get_scoped_queryset`` plus copies inside ``StatusLabelsWidget`` and
   ``LowStockWidget``). That layer honoured only ``request.active_tenant`` and, on
   the all-accessible scope (``active_tenant is None``), fell back to the member's
   first ``AssetHolder`` profile — collapsing the aggregate to one arbitrary
   tenant, or to ``qs.none()`` when the member had no profile.

2. ``TenantSpendWidget`` was ``admin_only`` and read ``Asset._base_manager``
   (globally, bypassing the scope), so a standard user saw
   ``Restricted to Global Administrators.`` instead of spend across exactly the
   tenants they may access.

These tests reproduce the zero-value / restriction behaviour and pin the
corrected behaviour: widgets aggregate EXACTLY the canonical accessible tenant
set (direct memberships, managed reach, UserGroup-derived) and never leak an
inaccessible or soft-deleted tenant.
"""
import uuid
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.managers import (
    set_current_all_accessible,
    set_current_membership,
    set_current_tenant,
    set_current_tenant_group,
)
from core.models import ObjectChange
from core.tests.mixins import grant
from itambox.middleware import set_current_user
from organization.models import (
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
    TenantGroup,
)
from users.models import GroupMembership, UserGroup

from assets.models import (
    Asset,
    AssetMaintenance,
    AssetType,
    Category,
    Manufacturer,
    StatusLabel,
)
from licenses.models import License
from software.models import Software
from subscriptions.models import Provider, Subscription

from extras.models import Dashboard
from extras.dashboard.widgets import (
    AssetAgeWidget,
    ChangelogWidget,
    EOLAlertsWidget,
    FinancialWidget,
    LicenseWidget,
    LowStockWidget,
    MaintenanceWidget,
    ObjectCountsWidget,
    RenewalsWidget,
    StatusLabelsWidget,
    TenantSpendWidget,
    get_scoped_queryset,
)

User = get_user_model()


class DashboardContextMixin:
    """Establish the canonical tenant contextvars + a matching request the way
    ``CurrentUserMiddleware`` / ``TenantMiddleware`` would, so widget-level tests
    exercise the real manager scope instead of the removed AssetHolder fallback.

    The autouse ``conftest`` fixture clears the tenant/user contextvars after each
    test, so these setters never leak between tests.
    """

    def _request(self, user, *, tenant=None, group=None, membership=None,
                 all_accessible=False):
        set_current_user(user)
        set_current_tenant(tenant)
        set_current_tenant_group(group)
        set_current_membership(membership)
        set_current_all_accessible(all_accessible)
        request = RequestFactory().get('/')
        request.user = user
        request.active_tenant = tenant
        request.active_tenant_group = group
        request.active_membership = membership
        request.active_all_accessible = all_accessible
        return request

    def all_accessible_request(self, user):
        return self._request(user, all_accessible=True)

    def single_tenant_request(self, user, tenant, membership=None):
        return self._request(user, tenant=tenant, membership=membership)

    def global_request(self, user):
        """Superuser global scope: no tenant/group/all-accessible active."""
        return self._request(user)


def _make_catalog():
    mfr = Manufacturer.objects.create(name='I133 Acme', slug='i133-acme')
    category = Category.objects.create(
        name='I133 Computers', slug='i133-computers', applies_to={'asset': True},
    )
    asset_type = AssetType.objects.create(
        manufacturer=mfr, model='I133 Model', slug='i133-model',
        category=category, eol_months=12,
    )
    status = StatusLabel.objects.create(
        name='I133 Deployable', slug='i133-deployable', type='deployable',
    )
    software = Software.objects.create(name='I133 Office', manufacturer=mfr)
    provider = Provider.objects.create(name='I133 Provider', slug='i133-provider')
    return mfr, asset_type, status, software, provider


class AllAccessibleWidgetTests(DashboardContextMixin, TestCase):
    """Every data-backed widget must aggregate exactly the two accessible tenants
    and never the inaccessible third one, under the all-accessible member scope."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name='Alpha Workspace', slug='i133w-a')
        self.tenant_b = Tenant.objects.create(name='Bravo Workspace', slug='i133w-b')
        self.tenant_c = Tenant.objects.create(name='Zulu Hidden Workspace', slug='i133w-c')

        self.member = User.objects.create_user(username='i133w-member', password='pw')
        self.role_a = Role.objects.create(tenant=self.tenant_a, name='A', permissions=[])
        self.role_b = Role.objects.create(tenant=self.tenant_b, name='B', permissions=[])
        grant(self.member, self.tenant_a, self.role_a)
        grant(self.member, self.tenant_b, self.role_b)
        self.superuser = User.objects.create_superuser(
            username='i133w-su', email='i133w-su@x.com', password='pw',
        )

        self.mfr, self.asset_type, self.status, self.software, self.provider = _make_catalog()

        self.assets = {}
        self._seed(self.tenant_a, key='a', display='AlphaAsset', cost=1000,
                   maint=150, sub_cost=500, seats=10)
        self._seed(self.tenant_b, key='b', display='BravoAsset', cost=2000,
                   maint=300, sub_cost=800, seats=20)
        self._seed(self.tenant_c, key='c', display='ZuluSecretAsset', cost=9999,
                   maint=999, sub_cost=999, seats=99)

    def _seed(self, tenant, *, key, display, cost, maint, sub_cost, seats):
        asset = Asset.objects.create(
            name=display, asset_tag=f'I133-TAG-{key}', serial_number=f'I133-SN-{key}',
            asset_type=self.asset_type, status=self.status, tenant=tenant,
            purchase_cost=cost, purchase_date=date.today() - timedelta(days=300),
        )
        self.assets[key] = asset
        AssetMaintenance.objects.create(asset=asset, start_date=date.today(), cost=maint)
        Subscription.objects.create(
            name=f'SaaS {key}', slug=f'i133-saas-{key}', provider=self.provider,
            status='active', start_date=date.today(),
            renewal_date=date.today() + timedelta(days=30),
            renewal_cost=sub_cost, tenant=tenant,
        )
        License.objects.create(
            name=f'License {key}', seats=seats, software=self.software, tenant=tenant,
        )

    # --- get_scoped_queryset ------------------------------------------------

    def test_scoped_asset_queryset_aggregates_accessible_only(self):
        qs = get_scoped_queryset(Asset, self.all_accessible_request(self.member))
        self.assertEqual(
            set(qs.values_list('pk', flat=True)),
            {self.assets['a'].pk, self.assets['b'].pk},
        )
        self.assertNotIn(self.assets['c'].pk, set(qs.values_list('pk', flat=True)))

    def test_scoped_maintenance_queryset_aggregates_accessible_only(self):
        qs = get_scoped_queryset(AssetMaintenance, self.all_accessible_request(self.member))
        tenants = {m.asset.tenant_id for m in qs}
        self.assertEqual(tenants, {self.tenant_a.pk, self.tenant_b.pk})

    # --- individual widgets under the all-accessible scope ------------------

    def test_financial_widget_aggregates_two_tenants(self):
        ctx = FinancialWidget().get_context(self.all_accessible_request(self.member))
        self.assertEqual(len(ctx['currency_breakdown']), 1)
        bucket = ctx['currency_breakdown'][0]
        self.assertEqual(bucket['total_purchase_cost'], 3000.00)
        self.assertEqual(bucket['total_maintenance_cost'], 450.00)
        self.assertEqual(bucket['total_tco'], 3450.00)
        self.assertEqual(ctx['costed_asset_count'], 2)

    def test_object_counts_widget_aggregates_two_tenants(self):
        widget = ObjectCountsWidget(config={'config': {'models': [
            'assets.asset', 'subscriptions.subscription', 'licenses.license',
        ]}})
        ctx = widget.get_context(self.all_accessible_request(self.member))
        counts = {item['label']: item['count'] for item in ctx['counts']}
        self.assertEqual(counts['Assets'], 2)
        self.assertEqual(counts['Subscriptions'], 2)
        self.assertEqual(counts['Licenses'], 2)

    def test_status_labels_widget_aggregates_two_tenants(self):
        ctx = StatusLabelsWidget().get_context(self.all_accessible_request(self.member))
        self.assertEqual(ctx['total_assets'], 2)
        self.assertEqual(ctx['status_stats'][0].asset_count, 2)

    def test_license_widget_aggregates_two_tenants(self):
        ctx = LicenseWidget().get_context(self.all_accessible_request(self.member))
        names = {row['license'].name for row in ctx['license_stats']}
        self.assertEqual(names, {'License a', 'License b'})

    def test_maintenance_widget_aggregates_two_tenants(self):
        ctx = MaintenanceWidget().get_context(self.all_accessible_request(self.member))
        self.assertEqual(ctx['active_maintenance_count'], 2)

    def test_eol_widget_aggregates_two_tenants(self):
        ctx = EOLAlertsWidget().get_context(self.all_accessible_request(self.member))
        names = {alert['asset'].name for alert in ctx['eol_alerts']}
        self.assertEqual(names, {'AlphaAsset', 'BravoAsset'})

    def test_renewals_widget_aggregates_two_tenants(self):
        ctx = RenewalsWidget().get_context(self.all_accessible_request(self.member))
        self.assertEqual(len(ctx['upcoming_renewals']), 2)
        total = sum(float(row['total']) for row in ctx['currency_spend'])
        self.assertEqual(total, 1300.00)  # 500 + 800, never 999 (tenant C)

    def test_asset_age_widget_aggregates_two_tenants(self):
        ctx = AssetAgeWidget().get_context(self.all_accessible_request(self.member))
        self.assertEqual(ctx['age_buckets']['lt1y'], 2)

    # --- inaccessible-tenant exclusion for aggregate/annotation widgets -----

    def test_status_labels_widget_excludes_inaccessible_tenant(self):
        ctx = StatusLabelsWidget().get_context(self.all_accessible_request(self.member))
        # If tenant C leaked, the (single) status label would count 3 assets.
        self.assertNotEqual(ctx['total_assets'], 3)
        self.assertEqual(ctx['status_stats'][0].asset_count, 2)

    def test_eol_widget_excludes_inaccessible_tenant(self):
        ctx = EOLAlertsWidget().get_context(self.all_accessible_request(self.member))
        names = {alert['asset'].name for alert in ctx['eol_alerts']}
        self.assertNotIn('ZuluSecretAsset', names)

    # --- superuser global behaviour is preserved ---------------------------

    def test_superuser_global_financial_spans_all_tenants(self):
        ctx = FinancialWidget().get_context(self.global_request(self.superuser))
        bucket = ctx['currency_breakdown'][0]
        self.assertEqual(bucket['total_purchase_cost'], 12999.00)  # 1000+2000+9999
        self.assertEqual(ctx['costed_asset_count'], 3)


class LowStockAllAccessibleTests(DashboardContextMixin, TestCase):
    """Low-stock correlated stock/assignment subqueries must aggregate exactly
    the accessible tenants and never an inaccessible one."""

    def setUp(self):
        from organization.models import AssetHolder, Location, Site

        self.tenant_a = Tenant.objects.create(name='Alpha LS', slug='i133ls-a')
        self.tenant_b = Tenant.objects.create(name='Bravo LS', slug='i133ls-b')
        self.tenant_c = Tenant.objects.create(name='Zulu LS', slug='i133ls-c')

        self.member = User.objects.create_user(username='i133ls-member', password='pw')
        role_a = Role.objects.create(tenant=self.tenant_a, name='A', permissions=[])
        role_b = Role.objects.create(tenant=self.tenant_b, name='B', permissions=[])
        grant(self.member, self.tenant_a, role_a)
        grant(self.member, self.tenant_b, role_b)
        self.superuser = User.objects.create_superuser(
            username='i133ls-su', email='i133ls-su@x.com', password='pw',
        )

        self.mfr = Manufacturer.objects.create(name='I133LS Mfr', slug='i133ls-mfr')

        self.site_a = Site.objects.create(name='SA', slug='i133ls-sa', tenant=self.tenant_a)
        self.loc_a = Location.objects.create(
            name='LA', slug='i133ls-la', site=self.site_a, tenant=self.tenant_a,
        )
        self.holder_a = AssetHolder.objects.create(
            user=None, first_name='H', last_name='A', upn='i133ls.a', tenant=self.tenant_a,
        )
        self.site_c = Site.objects.create(name='SC', slug='i133ls-sc', tenant=self.tenant_c)
        self.loc_c = Location.objects.create(
            name='LC', slug='i133ls-lc', site=self.site_c, tenant=self.tenant_c,
        )
        self.holder_c = AssetHolder.objects.create(
            user=None, first_name='H', last_name='C', upn='i133ls.c', tenant=self.tenant_c,
        )

    def _low_accessory(self, tenant, location, holder, tag):
        from inventory.models import Accessory, AccessoryStock, AccessoryAssignment

        acc = Accessory.objects.create(
            name=f'Keyboard {tag}', manufacturer=self.mfr, min_qty=5, tenant=tenant,
        )
        AccessoryStock.objects.create(accessory=acc, location=location, qty=10)
        # 8 checked out -> 2 available, which is below the min of 5.
        AccessoryAssignment.objects.create(accessory=acc, assigned_holder=holder, qty=8)
        return acc

    def test_low_stock_aggregates_accessible_and_excludes_inaccessible(self):
        low_a = self._low_accessory(self.tenant_a, self.loc_a, self.holder_a, 'A')
        # tenant C accessory is ALSO low, but the member cannot access it.
        self._low_accessory(self.tenant_c, self.loc_c, self.holder_c, 'C')

        ctx = LowStockWidget().get_context(self.all_accessible_request(self.member))
        names = {w.name for w in ctx['low_stock_accessories']}
        self.assertEqual(names, {low_a.name})
        self.assertEqual(ctx['low_stock_accessory_count'], 1)
        self.assertEqual(ctx['low_stock_accessories'][0].available, 2)

    def test_low_stock_superuser_sees_all_tenants(self):
        self._low_accessory(self.tenant_a, self.loc_a, self.holder_a, 'A')
        self._low_accessory(self.tenant_c, self.loc_c, self.holder_c, 'C')

        ctx = LowStockWidget().get_context(self.global_request(self.superuser))
        self.assertEqual(ctx['low_stock_accessory_count'], 2)


class ChangelogAllAccessibleTests(DashboardContextMixin, TestCase):
    """The Change Log widget scopes ObjectChange by the canonical tenant set."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name='Alpha CL', slug='i133cl-a')
        self.tenant_b = Tenant.objects.create(name='Bravo CL', slug='i133cl-b')
        self.tenant_c = Tenant.objects.create(name='Zulu CL', slug='i133cl-c')

        self.member = User.objects.create_user(username='i133cl-member', password='pw')
        role_a = Role.objects.create(tenant=self.tenant_a, name='A', permissions=[])
        role_b = Role.objects.create(tenant=self.tenant_b, name='B', permissions=[])
        grant(self.member, self.tenant_a, role_a)
        grant(self.member, self.tenant_b, role_b)

        self.ct = ContentType.objects.get_for_model(Tenant)
        self._change(self.tenant_a, 'alpha-change')
        self._change(self.tenant_b, 'bravo-change')
        self._change(self.tenant_c, 'zulu-change')

    def _change(self, tenant, repr_):
        ObjectChange._base_manager.create(
            tenant=tenant, user=self.member, user_name='i133cl-member',
            request_id=uuid.uuid4(), action='update',
            changed_object_type=self.ct, changed_object_id=tenant.pk,
            object_repr=repr_,
        )

    def test_changelog_excludes_inaccessible_tenant(self):
        ctx = ChangelogWidget().get_context(self.all_accessible_request(self.member))
        reprs = {c.object_repr for c in ctx['recent_changes']}
        self.assertIn('alpha-change', reprs)
        self.assertIn('bravo-change', reprs)
        self.assertNotIn('zulu-change', reprs)


class TenantSpendWidgetTests(DashboardContextMixin, TestCase):
    """Tenant Spend must be available to standard users and grouped over exactly
    the active authorized scope, never the unscoped member query."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name='Alpha Spend', slug='i133ts-a')
        self.tenant_b = Tenant.objects.create(name='Bravo Spend', slug='i133ts-b')
        self.tenant_c = Tenant.objects.create(name='Zulu Spend', slug='i133ts-c')

        self.member = User.objects.create_user(username='i133ts-member', password='pw')
        self.role_a = Role.objects.create(tenant=self.tenant_a, name='A', permissions=[])
        self.role_b = Role.objects.create(tenant=self.tenant_b, name='B', permissions=[])
        self.member_a_grant = grant(self.member, self.tenant_a, self.role_a)
        grant(self.member, self.tenant_b, self.role_b)
        self.superuser = User.objects.create_superuser(
            username='i133ts-su', email='i133ts-su@x.com', password='pw',
        )

        self.mfr, self.asset_type, self.status, self.software, self.provider = _make_catalog()
        self._asset(self.tenant_a, 'a', 1000)
        self._asset(self.tenant_b, 'b', 2000)
        self._asset(self.tenant_c, 'c', 9999)

    def _asset(self, tenant, key, cost):
        return Asset.objects.create(
            name=f'Spend {key}', asset_tag=f'I133TS-{key}', serial_number=f'I133TS-SN-{key}',
            asset_type=self.asset_type, status=self.status, tenant=tenant,
            purchase_cost=cost,
        )

    def test_available_to_non_superuser(self):
        widget = TenantSpendWidget()
        request = self.all_accessible_request(self.member)
        self.assertTrue(widget.has_permission(request))
        rendered = widget.render(request)
        self.assertNotIn('Restricted to Global Administrators', rendered)

    def test_groups_across_accessible_scope_only(self):
        ctx = TenantSpendWidget().get_context(self.all_accessible_request(self.member))
        names = {row['name'] for row in ctx['tenant_spend']}
        self.assertEqual(names, {'Alpha Spend', 'Bravo Spend'})
        self.assertNotIn('Zulu Spend', names)

    def test_single_tenant_scope_shows_one_tenant(self):
        membership = Membership.objects.get(user=self.member, tenant=self.tenant_a)
        request = self.single_tenant_request(self.member, self.tenant_a, membership)
        ctx = TenantSpendWidget().get_context(request)
        names = {row['name'] for row in ctx['tenant_spend']}
        self.assertEqual(names, {'Alpha Spend'})

    def test_superuser_global_comparison_spans_all_tenants(self):
        ctx = TenantSpendWidget().get_context(self.global_request(self.superuser))
        names = {row['name'] for row in ctx['tenant_spend']}
        self.assertEqual(names, {'Alpha Spend', 'Bravo Spend', 'Zulu Spend'})

    def test_excludes_soft_deleted_tenant_for_superuser(self):
        self.tenant_c.deleted_at = timezone.now()
        self.tenant_c.save(update_fields=['deleted_at'])
        ctx = TenantSpendWidget().get_context(self.global_request(self.superuser))
        names = {row['name'] for row in ctx['tenant_spend']}
        self.assertNotIn('Zulu Spend', names)


class GetScopedQuerysetScopeTests(DashboardContextMixin, TestCase):
    """The scoped queryset used by every widget must honour all three canonical
    access paths (direct / managed / UserGroup-derived) and exclude inaccessible
    and soft-deleted tenants — including a member who has no AssetHolder profile
    and one with multiple profiles."""

    def setUp(self):
        self.region = TenantGroup.objects.create(name='I133 Region', slug='i133g-region')
        self.provider = Tenant.objects.create(
            name='I133 Provider', slug='i133g-p', is_provider=True,
        )
        self.direct = Tenant.objects.create(
            name='I133 Direct', slug='i133g-direct', managed_by=self.provider, group=self.region,
        )
        self.managed = Tenant.objects.create(
            name='I133 Managed', slug='i133g-managed', managed_by=self.provider, group=self.region,
        )
        self.grouped = Tenant.objects.create(
            name='I133 Grouped', slug='i133g-grouped', managed_by=self.provider, group=self.region,
        )
        self.inaccessible = Tenant.objects.create(
            name='I133 Inaccessible', slug='i133g-inacc',
        )
        self.soft_deleted = Tenant.objects.create(
            name='I133 SoftDeleted', slug='i133g-soft', managed_by=self.provider,
        )

        self.tech_role = Role.objects.create(tenant=self.provider, name='Tech', permissions=[])
        self.direct_role = Role.objects.create(tenant=self.direct, name='Direct', permissions=[])

        self.member = User.objects.create_user(username='i133g-member', password='pw')
        # 1) direct membership
        grant(self.member, self.direct, self.direct_role)
        # 2) managed reach to `managed` and `soft_deleted` (rides provider membership)
        self.managed_grant = grant(
            self.member, self.provider, self.tech_role,
            reach=RoleGrant.REACH_MANAGED,
            managed_scope=RoleGrantScope.SCOPE_TENANT,
            assigned_tenants=[self.managed, self.soft_deleted],
        )
        # 3) UserGroup-derived access to `grouped`
        user_group = UserGroup.objects.create(
            name='I133 Team', slug='i133g-team', tenant=self.provider,
        )
        provider_membership = Membership.objects.get(user=self.member, tenant=self.provider)
        GroupMembership.objects.create(user_group=user_group, membership=provider_membership)
        group_grant = RoleGrant.objects.create(user_group=user_group, role=self.tech_role)
        RoleGrantScope.objects.create(
            role_grant=group_grant, scope_type=RoleGrantScope.SCOPE_TENANT, tenant=self.grouped,
        )

        self.mfr, self.asset_type, self.status, self.software, self.provider_prov = _make_catalog()
        self.assets = {
            slug: self._asset(tenant, slug)
            for slug, tenant in (
                ('direct', self.direct), ('managed', self.managed),
                ('grouped', self.grouped), ('inaccessible', self.inaccessible),
                ('soft', self.soft_deleted),
            )
        }

    def _asset(self, tenant, key):
        return Asset.objects.create(
            name=f'Asset {key}', asset_tag=f'I133G-{key}', serial_number=f'I133G-SN-{key}',
            asset_type=self.asset_type, status=self.status, tenant=tenant, purchase_cost=100,
        )

    def _scoped_asset_ids(self):
        qs = get_scoped_queryset(Asset, self.all_accessible_request(self.member))
        return set(qs.values_list('pk', flat=True))

    def test_direct_membership_contributes(self):
        self.assertIn(self.assets['direct'].pk, self._scoped_asset_ids())

    def test_managed_reach_contributes(self):
        self.assertIn(self.assets['managed'].pk, self._scoped_asset_ids())

    def test_group_derived_access_contributes(self):
        self.assertIn(self.assets['grouped'].pk, self._scoped_asset_ids())

    def test_inaccessible_tenant_excluded(self):
        self.assertNotIn(self.assets['inaccessible'].pk, self._scoped_asset_ids())

    def test_soft_deleted_tenant_excluded(self):
        self.soft_deleted.deleted_at = timezone.now()
        self.soft_deleted.save(update_fields=['deleted_at'])
        self.assertNotIn(self.assets['soft'].pk, self._scoped_asset_ids())

    def test_tenant_group_scope_aggregates_accessible_group_tenants(self):
        # A selected tenant-group scope aggregates exactly the accessible tenants
        # inside that group's subtree; an accessible tenant outside the group
        # (soft_deleted, no group) and an inaccessible one both drop out.
        request = self._request(self.member, group=self.region)
        ids = set(get_scoped_queryset(Asset, request).values_list('pk', flat=True))
        self.assertEqual(
            ids,
            {
                self.assets['direct'].pk, self.assets['managed'].pk,
                self.assets['grouped'].pk,
            },
        )

    def test_member_without_asset_holder_profile_gets_data(self):
        # The member has NO AssetHolder profile; the old fallback returned none().
        self.assertFalse(self.member.asset_holder_profiles.exists())
        ids = self._scoped_asset_ids()
        # Every accessible tenant contributes (soft_deleted is still live here and
        # reached via managed grant); only the truly inaccessible tenant is out.
        self.assertEqual(
            ids,
            {
                self.assets['direct'].pk, self.assets['managed'].pk,
                self.assets['grouped'].pk, self.assets['soft'].pk,
            },
        )
        self.assertNotIn(self.assets['inaccessible'].pk, ids)

    def test_multiple_asset_holder_profiles_not_collapsed_to_first(self):
        from organization.models import AssetHolder

        # Two profiles in two different accessible tenants; the old code silently
        # collapsed the aggregate to `asset_holder_profiles.first()`.
        AssetHolder.objects.create(
            user=self.member, first_name='P', last_name='1', upn='i133g.p1', tenant=self.direct,
        )
        AssetHolder.objects.create(
            user=self.member, first_name='P', last_name='2', upn='i133g.p2', tenant=self.managed,
        )
        ids = self._scoped_asset_ids()
        # Both profile tenants AND the group-derived tenant must all contribute.
        self.assertIn(self.assets['direct'].pk, ids)
        self.assertIn(self.assets['managed'].pk, ids)
        self.assertIn(self.assets['grouped'].pk, ids)


class DashboardViewAllAccessibleGetTests(TestCase):
    """A real ``DashboardView`` GET through ``?switch_all_accessible=1`` must
    render non-zero aggregated values for the two accessible tenants and exclude
    the inaccessible one. Manager-only / widget-only tests are insufficient."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name='Alpha Board', slug='i133v-a')
        self.tenant_b = Tenant.objects.create(name='Bravo Board', slug='i133v-b')
        self.tenant_c = Tenant.objects.create(name='Zulu Hidden Board', slug='i133v-c')

        # Member has NO AssetHolder profile: the old fallback fail-closed to
        # qs.none() (zero values) precisely in this configuration.
        self.member = User.objects.create_user(username='i133v-member', password='pw')
        role_a = Role.objects.create(tenant=self.tenant_a, name='A', permissions=[])
        role_b = Role.objects.create(tenant=self.tenant_b, name='B', permissions=[])
        grant(self.member, self.tenant_a, role_a)
        grant(self.member, self.tenant_b, role_b)
        self.superuser = User.objects.create_superuser(
            username='i133v-su', email='i133v-su@x.com', password='pw',
        )

        self.mfr, self.asset_type, self.status, self.software, self.provider = _make_catalog()
        for tenant, key, cost in (
            (self.tenant_a, 'AlphaBoardAsset', 1000),
            (self.tenant_b, 'BravoBoardAsset', 2000),
            (self.tenant_c, 'ZuluSecretBoardAsset', 9999),
        ):
            Asset.objects.create(
                name=key, asset_tag=f'I133V-{key}', serial_number=f'I133V-SN-{key}',
                asset_type=self.asset_type, status=self.status, tenant=tenant,
                purchase_cost=cost, purchase_date=date.today() - timedelta(days=100),
            )

        self.layout = [
            {'widget': 'object-counts', 'title': 'Object Counts', 'visible': True,
             'config': {'models': ['assets.asset']}},
            {'widget': 'financial-overview', 'title': 'Financial', 'visible': True, 'config': {}},
            {'widget': 'tenant-spend', 'title': 'Tenant Spend', 'visible': True, 'config': {}},
            {'widget': 'status-labels', 'title': 'Status', 'visible': True,
             'config': {'chart_type': 'list'}},
        ]

    def _dashboard_for(self, user):
        return Dashboard.objects.create(
            user=user, name='Board', tenant=None, is_default=True, layout=self.layout,
        )

    def test_all_accessible_get_aggregates_and_excludes_inaccessible(self):
        self._dashboard_for(self.member)
        self.client.force_login(self.member)
        response = self.client.get(reverse('dashboard') + '?switch_all_accessible=1')
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        # Object Counts aggregates BOTH accessible tenants (2), not 0 (fail-closed
        # bug) and not 3 (would include the inaccessible tenant).
        self.assertRegex(
            content,
            r'widget-count-label">Assets</span>\s*<span class="widget-count-value">2<',
        )

        # Tenant Spend is available to the member (no admin restriction) and shows
        # only the two accessible tenants.
        self.assertNotIn('Restricted to Global Administrators', content)
        self.assertIn('Purchase cost by tenant', content)
        self.assertIn('Alpha Board', content)
        self.assertIn('Bravo Board', content)

        # The inaccessible tenant and its asset never leak into any widget.
        self.assertNotIn('Zulu Hidden Board', content)
        self.assertNotIn('ZuluSecretBoardAsset', content)

    def test_superuser_get_sees_all_tenants(self):
        self._dashboard_for(self.superuser)
        self.client.force_login(self.superuser)
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        # Superuser retains the global comparison across all three tenants.
        self.assertRegex(
            content,
            r'widget-count-label">Assets</span>\s*<span class="widget-count-value">3<',
        )
        self.assertIn('Zulu Hidden Board', content)
