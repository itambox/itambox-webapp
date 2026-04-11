from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from subscriptions.models import (
    Provider, Subscription, SubscriptionAssignment,
    SubscriptionTypeChoices, SubscriptionStatusChoices, BillingCycleChoices,
)

User = get_user_model()

class SubscriptionAPITests(APITestCase):
    def setUp(self):
        # Create users
        self.superuser = User.objects.create_user(
            username='api_superuser', email='api_super@example.com', password='password123', is_staff=True, is_superuser=True
        )
        self.staff = User.objects.create_user(
            username='api_staff', email='api_staff@example.com', password='password123', is_staff=True, is_superuser=False
        )

        # Create Tenant & AssetHolder profile for staff user
        from organization.models import TenantGroup, Tenant, AssetHolder
        self.tg = TenantGroup.objects.create(name="API TG", slug="api-tg")
        self.tenant = Tenant.objects.create(name="API Tenant", slug="api-tenant", group=self.tg)
        self.holder = AssetHolder.objects.create(
            user=self.staff,
            first_name="API",
            last_name="Staff",
            upn="api.staff",
            email="api_staff@example.com",
            tenant=self.tenant
        )

        # Base metadata
        self.provider = Provider.objects.create(name="AWS API Provider", slug="aws-api-provider")
        self.subscription = Subscription.objects.create(
            name="Developer Support API",
            provider=self.provider,
            type=SubscriptionTypeChoices.SAAS,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_cost=29.00,
            currency="USD",
            billing_cycle=BillingCycleChoices.MONTHLY,
            licensed_quantity=10,
            tenant=self.tenant
        )

        # Grant specific subscriptions permission codenames
        from django.contrib.auth.models import Permission
        from django.contrib.contenttypes.models import ContentType
        
        for model in [Provider, Subscription, SubscriptionAssignment]:
            content_type = ContentType.objects.get_for_model(model)
            for action in ['view', 'add', 'change', 'delete']:
                codename = f'{action}_{model._meta.model_name}'
                try:
                    permission = Permission.objects.get(
                        codename=codename,
                        content_type=content_type,
                    )
                    self.staff.user_permissions.add(permission)
                except Permission.DoesNotExist:
                    pass

    def test_provider_api_crud(self):
        self.client.force_authenticate(user=self.staff)

        # List
        list_url = reverse('api:subscriptions_api:provider-list')
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['count'], 1)

        # Create
        post_data = {
            'name': 'GCP API Provider',
            'account_id': 'gcp-456'
        }
        response = self.client.post(list_url, data=post_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_pk = response.data['id']
        etag = response['ETag']

        # Update
        detail_url = reverse('api:subscriptions_api:provider-detail', kwargs={'pk': new_pk})
        put_data = {
            'name': 'Google Cloud Platform API Provider',
            'account_id': 'gcp-999'
        }
        response = self.client.put(detail_url, data=put_data, format='json', HTTP_IF_MATCH=etag)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['name'], 'Google Cloud Platform API Provider')

        # Delete
        response = self.client.delete(detail_url, HTTP_IF_MATCH=response['ETag'])
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_subscription_api_crud(self):
        self.client.force_authenticate(user=self.staff)

        # List
        list_url = reverse('api:subscriptions_api:subscription-list')
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data['count'], 1)

        # Create
        post_data = {
            'name': 'Business Support API Sub',
            'provider_id': self.provider.id,
            'type': SubscriptionTypeChoices.SAAS,
            'status': SubscriptionStatusChoices.ACTIVE,
            'renewal_cost': '100.00',
            'currency': 'USD',
            'billing_cycle': BillingCycleChoices.MONTHLY,
            'tenant_id': self.tenant.id
        }
        response = self.client.post(list_url, data=post_data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_pk = response.data['id']
        etag = response['ETag']

        # Update status action
        status_url = reverse('api:subscriptions_api:subscription-update-status', kwargs={'pk': new_pk})
        response = self.client.patch(status_url, data={'status': 'suspended'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'suspended')

        # Delete
        detail_url = reverse('api:subscriptions_api:subscription-detail', kwargs={'pk': new_pk})
        response = self.client.delete(detail_url, HTTP_IF_MATCH=etag)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
