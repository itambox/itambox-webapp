from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assets.models import Supplier
from organization.models import Tenant
from subscriptions.models import (
    BillingCycleChoices,
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
        self.supplier = Supplier.objects.create(name="AWS API Supplier", slug="aws-api-supplier")
        self.subscription = Subscription.objects.create(
            name="Developer Support API",
            supplier=self.supplier,
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
            "supplier_id": self.supplier.id,
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

    def test_subscription_create_without_tenant_id_accepts_the_active_tenants_supplier(self):
        """The optional tenant_id is injected after validation; relation checks must still see it."""
        self._login_as_staff()
        list_url = reverse("api:subscriptions_api:subscription-list")
        tenant_supplier = Supplier.objects.create(
            name="Implicit tenant supplier", slug="implicit-tenant-supplier", tenant=self.tenant
        )
        post_data = {
            "name": "Implicit Tenant Sub",
            "supplier_id": tenant_supplier.id,
            "type": SubscriptionTypeChoices.SAAS,
            "status": SubscriptionStatusChoices.ACTIVE,
            "renewal_cost": "10.00",
            "currency": "USD",
            "billing_cycle": BillingCycleChoices.MONTHLY,
        }
        response = self.client.post(list_url, data=post_data, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        created = Subscription.objects.get(pk=response.data["id"])
        self.assertEqual(created.tenant_id, self.tenant.id)
        self.assertEqual(created.supplier_id, tenant_supplier.id)

        # A supplier from a different tenant is still rejected.
        other_tenant = Tenant.objects.create(name="Implicit Tenant Other", slug="implicit-tenant-other")
        foreign_supplier = Supplier.objects.create(
            name="Foreign supplier", slug="foreign-supplier", tenant=other_tenant
        )
        response = self.client.post(
            list_url,
            data={**post_data, "name": "Foreign Implicit Sub", "supplier_id": foreign_supplier.pk},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("supplier_id", response.data)
