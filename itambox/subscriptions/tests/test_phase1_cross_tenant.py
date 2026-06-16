from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.contenttypes.models import ContentType

from core.managers import set_current_tenant
from organization.models import Tenant, TenantRole, TenantMembership
from assets.models import Asset, StatusLabel
from subscriptions.models import Provider, Subscription, SubscriptionAssignment

User = get_user_model()

# Permissions the actor (tenant B) holds so that any block is proven to come
# from tenant scoping rather than a missing-permission denial.
ASSIGNMENT_PERMS = [
    'subscriptions.view_subscriptionassignment',
    'subscriptions.add_subscriptionassignment',
    'subscriptions.change_subscriptionassignment',
    'subscriptions.delete_subscriptionassignment',
]


class SubscriptionAssignmentCrossTenantTests(TestCase):
    """A tenant-A SubscriptionAssignment must be invisible/immutable to tenant B."""

    def setUp(self):
        # Tenants
        self.tenant_a = Tenant.objects.create(name='Tenant A', slug='tenant-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='tenant-b')

        # Users
        self.user_a = User.objects.create_user(username='user_a', password='password123')
        self.user_b = User.objects.create_user(username='user_b', password='password123')

        # Tenant A membership/role
        self.role_a = TenantRole.objects.create(
            tenant=self.tenant_a, name='Admin', permissions=list(ASSIGNMENT_PERMS)
        )
        TenantMembership.objects.create(user=self.user_a, tenant=self.tenant_a, role=self.role_a)

        # Tenant B membership/role (same perms — block must be from scoping, not perms)
        self.role_b = TenantRole.objects.create(
            tenant=self.tenant_b, name='Admin', permissions=list(ASSIGNMENT_PERMS)
        )
        TenantMembership.objects.create(user=self.user_b, tenant=self.tenant_b, role=self.role_b)

        # Shared metadata
        self.status = StatusLabel.objects.create(name='Active', slug='active', type='deployable')

        # Build tenant-A objects with tenant-A context active so the scoping
        # manager does not interfere during creation.
        set_current_tenant(self.tenant_a)
        try:
            self.provider_a = Provider.objects.create(name='Provider A', tenant=self.tenant_a)
            self.subscription_a = Subscription.objects.create(
                name='Subscription A', provider=self.provider_a, tenant=self.tenant_a
            )
            self.asset_a = Asset.objects.create(
                name='Asset A', asset_tag='TAG-A-001', status=self.status, tenant=self.tenant_a
            )
            self.assignment_a = SubscriptionAssignment.objects.create(
                subscription=self.subscription_a,
                content_type=ContentType.objects.get_for_model(Asset),
                object_id=self.asset_a.pk,
                assigned_by=self.user_a,
            )
        finally:
            set_current_tenant(None)

    def _login_as_tenant_b(self):
        self.client.force_login(self.user_b)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_b.pk
        session.save()

    def test_list_excludes_other_tenant_assignment(self):
        self._login_as_tenant_b()
        list_url = reverse('api:subscriptions_api:subscriptionassignment-list')
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)
        returned_ids = {row['id'] for row in response.data['results']}
        self.assertNotIn(self.assignment_a.pk, returned_ids)

    def test_detail_get_blocked(self):
        self._login_as_tenant_b()
        detail_url = reverse(
            'api:subscriptions_api:subscriptionassignment-detail',
            kwargs={'pk': self.assignment_a.pk},
        )
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 404)

    def test_patch_blocked(self):
        self._login_as_tenant_b()
        detail_url = reverse(
            'api:subscriptions_api:subscriptionassignment-detail',
            kwargs={'pk': self.assignment_a.pk},
        )
        response = self.client.patch(
            detail_url, data={'notes': 'hacked'}, content_type='application/json'
        )
        self.assertEqual(response.status_code, 404)
        self.assignment_a.refresh_from_db()
        self.assertNotEqual(self.assignment_a.notes, 'hacked')

    def test_delete_blocked_row_survives(self):
        self._login_as_tenant_b()
        detail_url = reverse(
            'api:subscriptions_api:subscriptionassignment-detail',
            kwargs={'pk': self.assignment_a.pk},
        )
        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, 404)
        # Use the unscoped base manager: the tenant-scoped default manager would
        # hide a tenant-A row from the active tenant-B context regardless of
        # whether it was deleted, so it cannot prove survival.
        self.assertTrue(
            SubscriptionAssignment._base_manager.filter(pk=self.assignment_a.pk).exists()
        )
