from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from subscriptions.models import (
    BillingCycleChoices,
    Provider,
    Subscription,
    SubscriptionStatusChoices,
    SubscriptionTypeChoices,
)

User = get_user_model()


class SubscriptionAPITests(APITestCase):
    def setUp(self):
        # Create users
        self.superuser = User.objects.create_user(
            username="api_superuser",
            email="api_super@example.com",
            password="password123",
            is_staff=True,
            is_superuser=True,
        )
        self.staff = User.objects.create_user(
            username="api_staff",
            email="api_staff@example.com",
            password="password123",
            is_staff=True,
            is_superuser=False,
        )

        # Create Tenant & AssetHolder profile for staff user
        from core.tests.mixins import grant
        from organization.models import AssetHolder, Role, Tenant, TenantGroup

        self.tg = TenantGroup.objects.create(name="API TG", slug="api-tg")
        self.tenant = Tenant.objects.create(name="API Tenant", slug="api-tenant", group=self.tg)
        self.holder = AssetHolder.objects.create(
            user=self.staff,
            first_name="API",
            last_name="Staff",
            upn="api.staff",
            email="api_staff@example.com",
            tenant=self.tenant,
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
            tenant=self.tenant,
        )

        # Grant permissions via Role + Membership (RBAC backend requires this)
        role = Role.objects.create(
            tenant=self.tenant,
            name="Staff Role",
            permissions=[
                "subscriptions.view_provider",
                "subscriptions.add_provider",
                "subscriptions.change_provider",
                "subscriptions.delete_provider",
                "subscriptions.view_subscription",
                "subscriptions.add_subscription",
                "subscriptions.change_subscription",
                "subscriptions.delete_subscription",
                "subscriptions.view_subscriptionassignment",
                "subscriptions.add_subscriptionassignment",
                "subscriptions.change_subscriptionassignment",
                "subscriptions.delete_subscriptionassignment",
            ],
        )
        grant(self.staff, self.tenant, role)

    def _login_as_staff(self):
        # TokenPermissions fails closed when a non-superuser request has no
        # active tenant. force_authenticate() bypasses TenantMiddleware's
        # session-based tenant binding entirely, so authenticate through a
        # real session and bind the active tenant the same way a browser
        # login would.
        self.client.force_login(self.staff)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

    def test_provider_api_crud(self):
        self._login_as_staff()

        # List
        list_url = reverse("api:subscriptions_api:provider-list")
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data["count"], 1)

        # Create
        post_data = {"name": "GCP API Provider", "account_id": "gcp-456"}
        response = self.client.post(list_url, data=post_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_pk = response.data["id"]
        etag = response["ETag"]

        # Update
        detail_url = reverse("api:subscriptions_api:provider-detail", kwargs={"pk": new_pk})
        put_data = {"name": "Google Cloud Platform API Provider", "account_id": "gcp-999"}
        response = self.client.put(detail_url, data=put_data, format="json", HTTP_IF_MATCH=etag)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Google Cloud Platform API Provider")

        # Delete
        response = self.client.delete(detail_url, HTTP_IF_MATCH=response["ETag"])
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_subscription_api_crud(self):
        self._login_as_staff()

        # List
        list_url = reverse("api:subscriptions_api:subscription-list")
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(response.data["count"], 1)

        # Create
        post_data = {
            "name": "Business Support API Sub",
            "provider_id": self.provider.id,
            "type": SubscriptionTypeChoices.SAAS,
            "status": SubscriptionStatusChoices.ACTIVE,
            "renewal_cost": "100.00",
            "currency": "USD",
            "billing_cycle": BillingCycleChoices.MONTHLY,
            "auto_renewal": False,
            "tenant_id": self.tenant.id,
        }
        response = self.client.post(list_url, data=post_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIs(response.data["vendor_contract_auto_renews"], False)
        self.assertIs(response.data["auto_renewal"], False)
        new_pk = response.data["id"]
        etag = response["ETag"]

        detail_url = reverse("api:subscriptions_api:subscription-detail", kwargs={"pk": new_pk})
        response = self.client.patch(
            detail_url,
            data={"status": "cancelled", "cancellation_date": "2029-01-01"},
            format="json",
            HTTP_IF_MATCH=etag,
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("lifecycle", str(response.data["status"]).lower())

        # Explicit lifecycle action with optimistic concurrency.
        status_url = reverse("api:subscriptions_api:subscription-suspend", kwargs={"pk": new_pk})
        response = self.client.post(status_url, format="json", HTTP_IF_MATCH=etag)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "suspended")
        etag = response["ETag"]

        resume_url = reverse("api:subscriptions_api:subscription-resume", kwargs={"pk": new_pk})
        response = self.client.post(resume_url, format="json", HTTP_IF_MATCH=etag)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "active")
        etag = response["ETag"]

        renew_url = reverse("api:subscriptions_api:subscription-renew", kwargs={"pk": new_pk})
        response = self.client.post(
            renew_url,
            data={"renewal_date": "2030-01-15", "renewal_cost": "125.00"},
            format="json",
            HTTP_IF_MATCH=etag,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["renewal_date"], "2030-01-15")
        etag = response["ETag"]

        cancel_url = reverse("api:subscriptions_api:subscription-cancel", kwargs={"pk": new_pk})
        response = self.client.post(
            cancel_url,
            data={"cancellation_date": "2030-01-16", "reason": "No longer needed"},
            format="json",
            HTTP_IF_MATCH=etag,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "cancelled")
        etag = response["ETag"]

        # Delete
        detail_url = reverse("api:subscriptions_api:subscription-detail", kwargs={"pk": new_pk})
        response = self.client.delete(detail_url, HTTP_IF_MATCH=etag)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
