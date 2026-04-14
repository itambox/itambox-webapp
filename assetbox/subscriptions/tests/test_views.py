from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from assets.models import Asset
from organization.models import TenantGroup, Tenant, Site, Location
from subscriptions.models import (
    Provider, Subscription, SubscriptionAssignment,
    SubscriptionTypeChoices, SubscriptionStatusChoices, BillingCycleChoices,
)

User = get_user_model()

class SubscriptionViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="testuser", password="testpass", is_staff=True, is_superuser=True
        )
        self.client.login(username="testuser", password="testpass")
        self.provider = Provider.objects.create(name="Test Provider")
        self.sub = Subscription.objects.create(
            name="Test Subscription",
            provider=self.provider,
            type=SubscriptionTypeChoices.SAAS,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_cost=999.99,
            currency="EUR",
            billing_cycle=BillingCycleChoices.ANNUAL,
            licensed_quantity=50,
        )

    def test_list_view_status_200(self):
        url = reverse("subscriptions:subscription_list")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Test Subscription")

    def test_detail_view_status_200(self):
        url = reverse("subscriptions:subscription_detail", kwargs={"pk": self.sub.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Test Subscription")
        self.assertContains(resp, "999.99")
        self.assertContains(resp, "EUR")

    def test_create_view_get(self):
        url = reverse("subscriptions:subscription_create")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_create_view_post(self):
        url = reverse("subscriptions:subscription_create")
        resp = self.client.post(url, {
            "name": "New Subscription",
            "provider": self.provider.pk,
            "type": SubscriptionTypeChoices.SAAS,
            "status": SubscriptionStatusChoices.ACTIVE,
            "renewal_cost": "499.00",
            "currency": "USD",
            "billing_cycle": BillingCycleChoices.ANNUAL,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Subscription.objects.filter(name="New Subscription").exists())

    def test_edit_view_get(self):
        url = reverse("subscriptions:subscription_update", kwargs={"pk": self.sub.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_edit_view_post(self):
        url = reverse("subscriptions:subscription_update", kwargs={"pk": self.sub.pk})
        resp = self.client.post(url, {
            "name": "Renamed Subscription",
            "provider": self.provider.pk,
            "type": SubscriptionTypeChoices.SAAS,
            "status": SubscriptionStatusChoices.ACTIVE,
            "renewal_cost": "999.99",
            "currency": "EUR",
            "billing_cycle": BillingCycleChoices.ANNUAL,
        })
        self.assertEqual(resp.status_code, 302)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.name, "Renamed Subscription")

    def test_delete_view_get(self):
        url = reverse("subscriptions:subscription_delete", kwargs={"pk": self.sub.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_delete_view_post(self):
        url = reverse("subscriptions:subscription_delete", kwargs={"pk": self.sub.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Subscription.objects.filter(pk=self.sub.pk).exists())

class ProviderViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="testuser", password="testpass", is_staff=True, is_superuser=True
        )
        self.client.login(username="testuser", password="testpass")
        self.provider = Provider.objects.create(
            name="AWS", account_id="aws-001", is_active=True
        )

    def test_list_view_status_200(self):
        url = reverse("subscriptions:provider_list")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "AWS")

    def test_detail_view_status_200(self):
        url = reverse("subscriptions:provider_detail", kwargs={"pk": self.provider.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "AWS")

    def test_create_view_post(self):
        url = reverse("subscriptions:provider_create")
        resp = self.client.post(url, {
            "name": "Google Cloud",
            "account_id": "gcp-001",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Provider.objects.filter(name="Google Cloud").exists())

    def test_edit_view_post(self):
        url = reverse("subscriptions:provider_update", kwargs={"pk": self.provider.pk})
        resp = self.client.post(url, {
            "name": "Amazon Web Services",
            "account_id": "aws-001",
        })
        self.assertEqual(resp.status_code, 302)
        self.provider.refresh_from_db()
        self.assertEqual(self.provider.name, "Amazon Web Services")

    def test_delete_view_post(self):
        url = reverse("subscriptions:provider_delete", kwargs={"pk": self.provider.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Provider.objects.filter(pk=self.provider.pk).exists())

class SubscriptionAssignmentViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="testuser", password="testpass", is_staff=True, is_superuser=True
        )
        self.client.login(username="testuser", password="testpass")
        self.provider = Provider.objects.create(name="AWS", is_active=True)
        self.subscription = Subscription.objects.create(
            name="AWS Business Support",
            provider=self.provider,
            status=SubscriptionStatusChoices.ACTIVE,
        )
        self.tg = TenantGroup.objects.create(name="TG1", slug="tg1")
        self.tenant = Tenant.objects.create(name="Tenant1", slug="tenant1", group=self.tg)
        self.site = Site.objects.create(name="Dublin", slug="dublin", tenant=self.tenant)
        self.location = Location.objects.create(
            name="Rack A", slug="rack-a", site=self.site, tenant=self.tenant
        )
        self.asset = Asset.objects.create(
            name="Server Ireland",
            asset_tag="SRV-IRE-01",
            location=self.location,
            tenant=self.tenant,
        )
        self.asset_ct = ContentType.objects.get_for_model(Asset)

    def test_assignment_create_view_get(self):
        url = reverse("subscriptions:subscriptionassignment_create")
        resp = self.client.get(f"{url}?content_type={self.asset_ct.pk}&object_id={self.asset.pk}")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Assign Subscription")

    def test_assignment_create_view_post(self):
        url = reverse("subscriptions:subscriptionassignment_create")
        post_url = f"{url}?content_type={self.asset_ct.pk}&object_id={self.asset.pk}"
        resp = self.client.post(post_url, {
            "subscription": self.subscription.pk,
            "notes": "Test support assignment",
        })
        self.assertEqual(resp.status_code, 302)
        
        assignment = SubscriptionAssignment.objects.filter(
            subscription=self.subscription,
            content_type=self.asset_ct,
            object_id=self.asset.pk
        ).first()
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.notes, "Test support assignment")
        self.assertEqual(assignment.assigned_by, self.user)

    def test_assignment_delete_view_post(self):
        assignment = SubscriptionAssignment.objects.create(
            subscription=self.subscription,
            content_type=self.asset_ct,
            object_id=self.asset.pk,
            assigned_by=self.user
        )
        url = reverse("subscriptions:subscriptionassignment_delete", kwargs={"pk": assignment.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(SubscriptionAssignment.objects.filter(pk=assignment.pk).exists())


from datetime import date
from organization.models import AssetHolder

class SubscriptionLifecycleViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="testuser", password="testpass", is_staff=True, is_superuser=True
        )
        self.client.login(username="testuser", password="testpass")
        self.provider = Provider.objects.create(name="Test Provider")
        self.sub = Subscription.objects.create(
            name="Test Subscription",
            provider=self.provider,
            type=SubscriptionTypeChoices.SAAS,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=date(2026, 6, 1),
            renewal_cost=999.99,
            currency="EUR",
            billing_cycle=BillingCycleChoices.ANNUAL,
            licensed_quantity=50,
        )
        self.tg = TenantGroup.objects.create(name="TG1", slug="tg1")
        self.tenant = Tenant.objects.create(name="Tenant1", slug="tenant1", group=self.tg)
        self.site = Site.objects.create(name="Dublin", slug="dublin", tenant=self.tenant)
        self.location = Location.objects.create(
            name="Rack A", slug="rack-a", site=self.site, tenant=self.tenant
        )
        self.asset = Asset.objects.create(
            name="Server Ireland",
            asset_tag="SRV-IRE-01",
            location=self.location,
            tenant=self.tenant,
        )
        self.holder = AssetHolder.objects.create(
            first_name="John",
            last_name="Doe",
            email="john@example.com",
            tenant=self.tenant
        )

    def test_subscription_renew_workflow(self):
        # 1. Test GET request renders the form
        url = reverse("subscriptions:subscription_renew", kwargs={"pk": self.sub.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Renew Subscription:")

        # 2. Test POST request updates the subscription and returns HTMX headers
        new_renewal_date = date(2027, 6, 1)
        resp = self.client.post(url, {
            "renewal_date": "2027-06-01",
            "renewal_cost": "1099.99"
        }, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 204)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.renewal_date, new_renewal_date)
        self.assertEqual(float(self.sub.renewal_cost), 1099.99)
        self.assertEqual(self.sub.status, SubscriptionStatusChoices.ACTIVE)
        self.assertIn("tableRefreshRequired", resp["HX-Trigger"])

    def test_subscription_cancel_workflow(self):
        # 1. Test GET request renders the form
        url = reverse("subscriptions:subscription_cancel", kwargs={"pk": self.sub.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Cancel Subscription:")

        # 2. Test POST request updates status and logs cancellation notes
        cancel_date = date(2026, 6, 2)
        resp = self.client.post(url, {
            "cancellation_date": "2026-06-02",
            "reason": "Moving to AWS"
        }, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 204)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.cancellation_date, cancel_date)
        self.assertEqual(self.sub.status, SubscriptionStatusChoices.CANCELLED)
        self.assertIn("Moving to AWS", self.sub.notes)
        self.assertIn("tableRefreshRequired", resp["HX-Trigger"])

    def test_subscription_suspend_workflow(self):
        url = reverse("subscriptions:subscription_suspend", kwargs={"pk": self.sub.pk})
        resp = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 204)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatusChoices.SUSPENDED)
        self.assertIn("tableRefreshRequired", resp["HX-Trigger"])

    def test_subscription_assignment_lifecycle(self):
        # 1. Test GET request to checkout/assign renders form
        url = reverse("subscriptions:subscription_checkout", kwargs={"pk": self.sub.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Assign Subscription:")

        # 2. Test POST checkout to Employee (AssetHolder)
        resp = self.client.post(url, {
            "target_type": "holder",
            "assigned_holder": self.holder.pk,
            "notes": "Assigning to John"
        }, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(self.sub.assignments.count(), 1)
        assignment = self.sub.assignments.first()
        self.assertEqual(assignment.assigned_object, self.holder)
        self.assertEqual(assignment.notes, "Assigning to John")

        # 3. Test POST checkout to Hardware Asset
        resp = self.client.post(url, {
            "target_type": "asset",
            "asset": self.asset.pk,
            "notes": "Assigning to server"
        }, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(self.sub.assignments.count(), 2)

        # 4. Checkin / Delete assignment restores capacity
        delete_url = reverse("subscriptions:subscriptionassignment_delete", kwargs={"pk": assignment.pk})
        resp = self.client.post(delete_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.sub.assignments.count(), 1)
