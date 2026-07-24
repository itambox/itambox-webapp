"""Issue #134 regression suite — REST API permissions & viewsets.

The DRF default permission classes and the tenant-scoped create path historically
derived identity from ``request.user.asset_holder_profiles.first()`` (or only
recognised a single active tenant), which:

* rejected session-authenticated requests under the canonical *All accessible
  tenants* scope (``TokenPermissions.has_permission`` did not know the scope);
* compared each detail object against an arbitrary AssetHolder tenant, returning
  incorrect 404s (``StrictTenantPermission.has_object_permission``);
* let a non-superuser mint a global (``tenant=None``) row by omitting the tenant
  when no single tenant was active (``perform_create``).

These tests pin the canonical model: reads follow the authorized tenant set,
object/mutation authorization uses the object's own tenant, an omitted tenant on
a multi-tenant-scope create fails closed, token requests stay single-tenant, and
superuser behaviour is unchanged. The exercised users hold NO AssetHolder profile
so the obsolete fallback cannot mask a regression.
"""
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from core.managers import (
    set_current_all_accessible,
    set_current_membership,
    set_current_tenant,
    set_current_tenant_group,
)
from core.tests.mixins import grant
from extras.models import AlertRule, NotificationChannel
from itambox.middleware import _current_user
from organization.models import (
    AssetHolder,
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
    TenantGroup,
)
from users.models import GroupMembership, Token, UserGroup

User = get_user_model()

CHANNEL_PERMS = [
    'extras.view_notificationchannel', 'extras.add_notificationchannel',
    'extras.change_notificationchannel', 'extras.delete_notificationchannel',
]
ALERT_PERMS = [
    'extras.view_alertrule', 'extras.add_alertrule',
    'extras.change_alertrule', 'extras.delete_alertrule',
]


def _reset_scope():
    set_current_tenant(None)
    set_current_tenant_group(None)
    set_current_membership(None)
    set_current_all_accessible(False)
    _current_user.set(None)


def _channel(name, tenant):
    return NotificationChannel.objects.create(
        name=name, channel_type=NotificationChannel.TYPE_IN_APP, tenant=tenant,
    )


class AllAccessibleRestReadTests(APITestCase):
    """Session-authenticated *All accessible tenants* list/detail must expose
    exactly the canonical authorized tenant set — reached via direct membership,
    managed reach, and UserGroup-derived access — and exclude inaccessible and
    soft-deleted tenants. The member holds NO AssetHolder profile."""

    def setUp(self):
        _reset_scope()
        self.region = TenantGroup.objects.create(name='Region', slug='i134r')
        self.region_west = TenantGroup.objects.create(
            name='Region West', slug='i134rw', parent=self.region,
        )
        self.provider = Tenant.objects.create(
            name='Provider', slug='i134-prov', is_provider=True,
        )
        self.cust_a = Tenant.objects.create(  # direct membership
            name='Cust A', slug='i134-a', managed_by=self.provider, group=self.region,
        )
        self.cust_b = Tenant.objects.create(  # managed reach
            name='Cust B', slug='i134-b', managed_by=self.provider, group=self.region,
        )
        self.cust_c = Tenant.objects.create(  # UserGroup-derived
            name='Cust C', slug='i134-c', managed_by=self.provider, group=self.region_west,
        )
        self.cust_d = Tenant.objects.create(  # NOT accessible
            name='Cust D', slug='i134-d', managed_by=self.provider, group=self.region,
        )
        self.cust_e = Tenant.objects.create(  # accessible, then soft-deleted
            name='Cust E', slug='i134-e', managed_by=self.provider, group=self.region,
        )

        self.tech_role = Role.objects.create(
            tenant=self.provider, name='Tech', permissions=CHANNEL_PERMS,
        )
        self.role_a = Role.objects.create(
            tenant=self.cust_a, name='A Direct', permissions=CHANNEL_PERMS,
        )
        self.role_e = Role.objects.create(
            tenant=self.cust_e, name='E Direct', permissions=CHANNEL_PERMS,
        )

        self.member = User.objects.create_user(username='i134-reader', password='pw')
        # 1) direct membership in cust_a
        grant(self.member, self.cust_a, self.role_a)
        # 2) managed reach to cust_b (rides on a provider membership)
        grant(
            self.member, self.provider, self.tech_role,
            reach=RoleGrant.REACH_MANAGED,
            managed_scope=RoleGrantScope.SCOPE_TENANT,
            assigned_tenants=[self.cust_b],
        )
        # 3) UserGroup-derived access to cust_c
        user_group = UserGroup.objects.create(
            name='Team', slug='i134-team', tenant=self.provider,
        )
        provider_membership = Membership.objects.get(user=self.member, tenant=self.provider)
        GroupMembership.objects.create(user_group=user_group, membership=provider_membership)
        group_grant = RoleGrant.objects.create(user_group=user_group, role=self.tech_role)
        RoleGrantScope.objects.create(
            role_grant=group_grant, scope_type=RoleGrantScope.SCOPE_TENANT, tenant=self.cust_c,
        )
        # 4) accessible-then-soft-deleted membership in cust_e
        grant(self.member, self.cust_e, self.role_e)

        self.ch_a = _channel('CH A', self.cust_a)
        self.ch_b = _channel('CH B', self.cust_b)
        self.ch_c = _channel('CH C', self.cust_c)
        self.ch_d = _channel('CH D', self.cust_d)
        self.ch_e = _channel('CH E', self.cust_e)
        self.ch_global = _channel('CH Global', None)

        # Soft-delete cust_e AFTER its channel exists: the tenant leaves the
        # accessible set, so its (still-present) channel must drop out of scope.
        self.cust_e.deleted_at = timezone.now()
        self.cust_e.save(update_fields=['deleted_at'])

        self.superuser = User.objects.create_superuser(
            username='i134-su', email='i134-su@x.com', password='pw',
        )

        self.assertFalse(AssetHolder._base_manager.filter(user=self.member).exists())

    def tearDown(self):
        _reset_scope()

    def _login_all_accessible(self, user):
        self.client.force_login(user)
        session = self.client.session
        session['active_all_accessible'] = True
        session.save()

    def _list_ids(self, resp):
        data = resp.data
        rows = data['results'] if isinstance(data, dict) and 'results' in data else data
        return {row['id'] for row in rows}

    def _detail(self, pk):
        return reverse('api:extras_api:notificationchannel-detail', kwargs={'pk': pk})

    def test_list_returns_exactly_authorized_set(self):
        self._login_all_accessible(self.member)
        resp = self.client.get(reverse('api:extras_api:notificationchannel-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(
            self._list_ids(resp),
            {self.ch_a.pk, self.ch_b.pk, self.ch_c.pk},
        )

    def test_detail_direct_membership_returns_200(self):
        self._login_all_accessible(self.member)
        resp = self.client.get(self._detail(self.ch_a.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

    def test_detail_managed_reach_returns_200(self):
        self._login_all_accessible(self.member)
        resp = self.client.get(self._detail(self.ch_b.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

    def test_detail_group_derived_returns_200(self):
        self._login_all_accessible(self.member)
        resp = self.client.get(self._detail(self.ch_c.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

    def test_detail_inaccessible_tenant_returns_404(self):
        self._login_all_accessible(self.member)
        resp = self.client.get(self._detail(self.ch_d.pk))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_detail_soft_deleted_tenant_returns_404(self):
        self._login_all_accessible(self.member)
        resp = self.client.get(self._detail(self.ch_e.pk))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_superuser_global_scope_unchanged(self):
        self.client.force_authenticate(self.superuser)
        resp = self.client.get(reverse('api:extras_api:notificationchannel-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = self._list_ids(resp)
        for pk in (self.ch_a.pk, self.ch_b.pk, self.ch_c.pk, self.ch_d.pk, self.ch_global.pk):
            self.assertIn(pk, ids)


class GroupScopeRestWriteTests(APITestCase):
    """Under a tenant-group scope (where objectless mutation permissions are not
    read-only), REST writes must be authorized against the object/payload tenant,
    and a create that omits the tenant must fail closed rather than mint a global
    row. AlertRule.allow_global_tenant=True, so an unpinned create would otherwise
    persist a globally visible row. The member holds NO AssetHolder profile."""

    def setUp(self):
        _reset_scope()
        self.region = TenantGroup.objects.create(name='WRegion', slug='i134w')
        self.cust_a = Tenant.objects.create(name='WA', slug='i134w-a', group=self.region)
        self.cust_b = Tenant.objects.create(name='WB', slug='i134w-b', group=self.region)
        self.cust_d = Tenant.objects.create(name='WD', slug='i134w-d', group=self.region)
        self.role_a = Role.objects.create(tenant=self.cust_a, name='WA role', permissions=ALERT_PERMS)
        self.role_b = Role.objects.create(tenant=self.cust_b, name='WB role', permissions=ALERT_PERMS)
        self.member = User.objects.create_user(username='i134-writer', password='pw')
        grant(self.member, self.cust_a, self.role_a)
        grant(self.member, self.cust_b, self.role_b)
        self.rule_a = AlertRule.objects.create(
            name='Rule A', alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=5, severity=AlertRule.SEVERITY_WARNING, tenant=self.cust_a,
        )
        self.rule_d = AlertRule.objects.create(
            name='Rule D', alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=5, severity=AlertRule.SEVERITY_WARNING, tenant=self.cust_d,
        )
        self.assertFalse(AssetHolder._base_manager.filter(user=self.member).exists())

    def tearDown(self):
        _reset_scope()

    def _login_group_scope(self):
        self.client.force_login(self.member)
        session = self.client.session
        session['active_tenant_group_id'] = self.region.pk
        session.save()

    def _detail(self, pk):
        return reverse('api:extras_api:alertrule-detail', kwargs={'pk': pk})

    @staticmethod
    def _etag(rule):
        rule.refresh_from_db()
        return 'W/"{0}"'.format(rule.updated_at.isoformat())

    def test_create_without_tenant_fails_closed_no_global_row(self):
        self._login_group_scope()
        before = AlertRule._base_manager.count()
        resp = self.client.post(
            reverse('api:extras_api:alertrule-list'),
            data={
                'name': 'Sneaky Global', 'alert_type': AlertRule.ALERT_TYPE_LOW_STOCK,
                'threshold_value': 3, 'severity': AlertRule.SEVERITY_INFO,
            },
            format='json',
        )
        # No single active tenant + omitted tenant => fail closed, never a 201.
        self.assertIn(
            resp.status_code,
            (status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
            resp.data,
        )
        self.assertEqual(AlertRule._base_manager.count(), before)
        self.assertFalse(
            AlertRule._base_manager.filter(name='Sneaky Global', tenant__isnull=True).exists()
        )

    def test_bulk_create_without_tenant_fails_closed_no_global_rows(self):
        self._login_group_scope()
        before = AlertRule._base_manager.count()
        names = ['Sneaky Bulk Global A', 'Sneaky Bulk Global B']
        resp = self.client.post(
            reverse('api:extras_api:alertrule-list'),
            data=[
                {
                    'name': name,
                    'alert_type': AlertRule.ALERT_TYPE_LOW_STOCK,
                    'threshold_value': 3,
                    'severity': AlertRule.SEVERITY_INFO,
                }
                for name in names
            ],
            format='json',
        )
        self.assertIn(
            resp.status_code,
            (status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND),
            resp.data,
        )
        self.assertEqual(AlertRule._base_manager.count(), before)
        self.assertFalse(
            AlertRule._base_manager.filter(name__in=names, tenant__isnull=True).exists()
        )

    def test_update_accessible_object_succeeds_via_object_tenant(self):
        self._login_group_scope()
        resp = self.client.patch(
            self._detail(self.rule_a.pk), {'severity': AlertRule.SEVERITY_CRITICAL},
            format='json', HTTP_IF_MATCH=self._etag(self.rule_a),
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.rule_a.refresh_from_db()
        self.assertEqual(self.rule_a.severity, AlertRule.SEVERITY_CRITICAL)
        self.assertEqual(self.rule_a.tenant, self.cust_a)  # tenant not reassigned

    def test_update_inaccessible_object_returns_404(self):
        self._login_group_scope()
        resp = self.client.patch(
            self._detail(self.rule_d.pk), {'severity': AlertRule.SEVERITY_CRITICAL},
            format='json', HTTP_IF_MATCH=self._etag(self.rule_d),
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
        self.rule_d.refresh_from_db()
        self.assertEqual(self.rule_d.severity, AlertRule.SEVERITY_WARNING)


class TokenSingleTenantTests(APITestCase):
    """Token-authenticated requests stay pinned to the token's tenant regardless
    of the canonical multi-tenant scopes."""

    def setUp(self):
        _reset_scope()
        self.tenant_a = Tenant.objects.create(name='TA', slug='i134t-a')
        self.tenant_b = Tenant.objects.create(name='TB', slug='i134t-b')
        self.role_a = Role.objects.create(
            tenant=self.tenant_a, name='TA role', permissions=CHANNEL_PERMS,
        )
        self.role_b = Role.objects.create(
            tenant=self.tenant_b, name='TB role', permissions=CHANNEL_PERMS,
        )
        self.member = User.objects.create_user(username='i134-token', password='pw')
        grant(self.member, self.tenant_a, self.role_a)
        grant(self.member, self.tenant_b, self.role_b)
        self.ch_a = _channel('T CH A', self.tenant_a)
        self.ch_b = _channel('T CH B', self.tenant_b)
        self.token = Token.objects.create(user=self.member, tenant=self.tenant_a)

    def tearDown(self):
        _reset_scope()

    def _detail(self, pk):
        return reverse('api:extras_api:notificationchannel-detail', kwargs={'pk': pk})

    def test_token_list_pinned_to_token_tenant(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        resp = self.client.get(reverse('api:extras_api:notificationchannel-list'))
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        data = resp.data
        rows = data['results'] if isinstance(data, dict) and 'results' in data else data
        ids = {row['id'] for row in rows}
        self.assertIn(self.ch_a.pk, ids)
        self.assertNotIn(self.ch_b.pk, ids)

    def test_token_cannot_reach_other_tenant_detail(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        resp = self.client.get(self._detail(self.ch_b.pk))
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_token_bulk_create_without_tenant_pins_every_row(self):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        names = ['Token Bulk A', 'Token Bulk B']
        resp = self.client.post(
            reverse('api:extras_api:notificationchannel-list'),
            data=[
                {
                    'name': name,
                    'channel_type': NotificationChannel.TYPE_IN_APP,
                    'config': {},
                }
                for name in names
            ],
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED, resp.data)
        created = NotificationChannel._base_manager.filter(name__in=names)
        self.assertEqual(created.count(), 2)
        self.assertEqual(set(created.values_list('tenant_id', flat=True)), {self.tenant_a.pk})
