"""
Tests for the Subscriptions module: models, filters, serializers, and views.
"""
import datetime

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from assets.models import Asset
from organization.models import AssetHolder, Location, Site, Tenant, TenantGroup
from .models import (
    Provider, Subscription, SubscriptionAssignment,
    SubscriptionTypeChoices, SubscriptionStatusChoices, BillingCycleChoices,
)


class ProviderModelTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(
            name="AWS",
            account_id="aws-12345",
            portal_url="https://aws.amazon.com/console",
            website="https://aws.amazon.com",
            contact_email="support@aws.example.com",
            contact_phone="+1-800-555-0199",
            is_active=True,
        )

    def test_provider_creation(self):
        self.assertEqual(str(self.provider), "AWS")
        self.assertTrue(self.provider.is_active)

    def test_provider_absolute_url(self):
        url = self.provider.get_absolute_url()
        self.assertIn(str(self.provider.pk), url)

    def test_provider_slug_auto_generation(self):
        provider = Provider.objects.create(name="Google Cloud Platform")
        self.assertEqual(provider.slug, "google-cloud-platform")

    def test_provider_inactive_does_not_filter_out(self):
        """Inactive providers still exist but won't show in active-only queries."""
        provider = Provider.objects.create(name="Old Vendor", is_active=False)
        self.assertFalse(Provider.objects.filter(is_active=True).filter(pk=provider.pk).exists())


class SubscriptionModelTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(name="Adobe Inc.", account_id="adobe-001")
        self.today = timezone.now().date()

    def test_subscription_creation(self):
        sub = Subscription.objects.create(
            name="Adobe Creative Cloud",
            provider=self.provider,
            type=SubscriptionTypeChoices.SAAS,
            status=SubscriptionStatusChoices.ACTIVE,
            start_date=self.today - datetime.timedelta(days=90),
            renewal_date=self.today + datetime.timedelta(days=275),
            renewal_cost=599.99,
            currency="USD",
            billing_cycle=BillingCycleChoices.ANNUAL,
            term_months=12,
            auto_renewal=True,
            licensed_quantity=25,
            contract_reference="PO-2026-0042",
            cost_center="CC-ENG-001",
        )
        self.assertEqual(str(sub), "Adobe Inc. - Adobe Creative Cloud")
        self.assertEqual(sub.days_until_renewal, 275)
        self.assertFalse(sub.is_expired)
        self.assertEqual(sub.annual_cost, 599.99)

    def test_subscription_expired(self):
        sub = Subscription.objects.create(
            name="Expired SaaS",
            provider=self.provider,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=self.today - datetime.timedelta(days=1),
            renewal_cost=100,
        )
        self.assertTrue(sub.is_expired)
        self.assertEqual(sub.days_until_renewal, -1)

    def test_subscription_renewing_today(self):
        sub = Subscription.objects.create(
            name="Renewing Today",
            provider=self.provider,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=self.today,
        )
        self.assertEqual(sub.days_until_renewal, 0)

    def test_subscription_annual_cost_monthly(self):
        sub = Subscription.objects.create(
            name="Monthly Plan",
            provider=self.provider,
            renewal_cost=49.99,
            billing_cycle=BillingCycleChoices.MONTHLY,
        )
        self.assertEqual(sub.annual_cost, 49.99 * 12)

    def test_subscription_annual_cost_quarterly(self):
        sub = Subscription.objects.create(
            name="Quarterly Plan",
            provider=self.provider,
            renewal_cost=299.99,
            billing_cycle=BillingCycleChoices.QUARTERLY,
        )
        self.assertEqual(sub.annual_cost, 299.99 * 4)

    def test_subscription_annual_cost_biannual(self):
        sub = Subscription.objects.create(
            name="Biannual Plan",
            provider=self.provider,
            renewal_cost=1199.99,
            billing_cycle=BillingCycleChoices.BIANNUAL,
        )
        self.assertEqual(sub.annual_cost, 1199.99 * 2)

    def test_subscription_annual_cost_none_when_no_cost(self):
        sub = Subscription.objects.create(
            name="Free Plan",
            provider=self.provider,
        )
        self.assertIsNone(sub.annual_cost)

    def test_subscription_days_until_renewal_none(self):
        sub = Subscription.objects.create(
            name="No Renewal",
            provider=self.provider,
        )
        self.assertIsNone(sub.days_until_renewal)

    def test_subscription_slug_auto_generation(self):
        sub = Subscription.objects.create(
            name="Adobe Creative Cloud - All Apps",
            provider=self.provider,
        )
        self.assertEqual(sub.slug, "adobe-creative-cloud-all-apps")

    def test_subscription_status_choices(self):
        self.assertEqual(SubscriptionStatusChoices.ACTIVE, "active")
        self.assertEqual(SubscriptionStatusChoices.EXPIRED, "expired")
        self.assertEqual(SubscriptionStatusChoices.CANCELLED, "cancelled")
        self.assertEqual(SubscriptionStatusChoices.PENDING, "pending")
        self.assertEqual(SubscriptionStatusChoices.SUSPENDED, "suspended")
        self.assertEqual(SubscriptionStatusChoices.RENEWING, "renewing")

    def test_subscription_billing_cycle_choices(self):
        self.assertEqual(BillingCycleChoices.MONTHLY, "monthly")
        self.assertEqual(BillingCycleChoices.ANNUAL, "annual")
        self.assertEqual(BillingCycleChoices.BIANNUAL, "biannual")

    def test_subscription_tenant(self):
        tg = TenantGroup.objects.create(name="Group", slug="group")
        tenant = Tenant.objects.create(name="Tenant Inc.", slug="tenant-inc", group=tg)
        sub = Subscription.objects.create(
            name="Tenant Sub",
            provider=self.provider,
            tenant=tenant,
        )
        self.assertEqual(sub.tenant, tenant)


class SubscriptionAssignmentModelTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(name="Microsoft", account_id="ms-001")
        self.subscription = Subscription.objects.create(
            name="M365 E5",
            provider=self.provider,
            licensed_quantity=100,
        )
        self.tg = TenantGroup.objects.create(name="G", slug="g")
        self.tenant = Tenant.objects.create(name="Tenant", slug="tenant", group=self.tg)
        self.site = Site.objects.create(name="Office", slug="office", tenant=self.tenant)
        self.location = Location.objects.create(
            name="Room 101", slug="room-101", site=self.site, tenant=self.tenant
        )
        self.asset = Asset.objects.create(
            name="Test Asset",
            asset_tag="TAG-001",
            serial_number="SN-001",
            purchase_cost=100,
            location=self.location,
            tenant=self.tenant,
        )
        self.user = get_user_model().objects.create_user(
            username="assigner", password="testpass123"
        )

    def test_assignment_to_asset(self):
        ct = ContentType.objects.get_for_model(Asset)
        assignment = SubscriptionAssignment.objects.create(
            subscription=self.subscription,
            content_type=ct,
            object_id=self.asset.pk,
            assigned_by=self.user,
        )
        self.assertEqual(str(assignment), f"Subscription {self.subscription} -> {self.asset}")

    def test_assignment_unique_constraint(self):
        ct = ContentType.objects.get_for_model(Asset)
        SubscriptionAssignment.objects.create(
            subscription=self.subscription,
            content_type=ct,
            object_id=self.asset.pk,
        )
        with self.assertRaises(IntegrityError):
            SubscriptionAssignment.objects.create(
                subscription=self.subscription,
                content_type=ct,
                object_id=self.asset.pk,
            )

    def test_assignment_to_asset_holder(self):
        holder = AssetHolder.objects.create(
            first_name="John", last_name="Doe", upn="john.doe", email="john@test.com"
        )
        ct = ContentType.objects.get_for_model(AssetHolder)
        assignment = SubscriptionAssignment.objects.create(
            subscription=self.subscription,
            content_type=ct,
            object_id=holder.pk,
        )
        self.assertIn("John Doe", str(assignment))

    def test_assignment_absolute_url_falls_back_to_subscription(self):
        ct = ContentType.objects.get_for_model(Asset)
        assignment = SubscriptionAssignment.objects.create(
            subscription=self.subscription,
            content_type=ct,
            object_id=self.asset.pk,
        )
        url = assignment.get_absolute_url()
        self.assertIn(str(self.subscription.pk), url)


class SubscriptionAutoExpirySignalTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(name="Test Provider")
        self.yesterday = timezone.now().date() - datetime.timedelta(days=1)

    def test_signal_marks_expired(self):
        sub = Subscription.objects.create(
            name="Should Expire",
            provider=self.provider,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=self.yesterday,
        )
        # The signal fires on save, but since the status was already set to ACTIVE
        # during creation and renewal_date is yesterday, saving again should trigger expiry.
        # Re-save to trigger signal if needed
        sub.save()
        self.assertEqual(sub.status, SubscriptionStatusChoices.EXPIRED)

    def test_signal_does_not_mark_future_renewal(self):
        future = timezone.now().date() + datetime.timedelta(days=30)
        sub = Subscription.objects.create(
            name="Future Renewal",
            provider=self.provider,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=future,
        )
        sub.save()
        self.assertEqual(sub.status, SubscriptionStatusChoices.ACTIVE)


class SubscriptionFilterSetTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(name="AWS", is_active=True)
        self.provider2 = Provider.objects.create(name="GCP", is_active=True)
        self.today = timezone.now().date()
        self.sub_active = Subscription.objects.create(
            name="Active Sub", provider=self.provider, status=SubscriptionStatusChoices.ACTIVE,
            renewal_date=self.today + datetime.timedelta(days=30),
        )
        self.sub_expired = Subscription.objects.create(
            name="Expired Sub", provider=self.provider2, status=SubscriptionStatusChoices.EXPIRED,
            renewal_date=self.today - datetime.timedelta(days=10),
        )

    def test_filter_by_status(self):
        from .filters import SubscriptionFilterSet
        f = SubscriptionFilterSet({'status': 'active'}, queryset=Subscription.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.sub_active, f.qs)
        self.assertNotIn(self.sub_expired, f.qs)

    def test_filter_by_provider(self):
        from .filters import SubscriptionFilterSet
        f = SubscriptionFilterSet(
            {'provider': self.provider.pk},
            queryset=Subscription.objects.all(),
        )
        self.assertTrue(f.is_valid())
        self.assertIn(self.sub_active, f.qs)
        self.assertNotIn(self.sub_expired, f.qs)

    def test_filter_search(self):
        from .filters import SubscriptionFilterSet
        f = SubscriptionFilterSet({'q': 'Expired'}, queryset=Subscription.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.sub_expired, f.qs)
        self.assertNotIn(self.sub_active, f.qs)

    def test_filter_renewal_within(self):
        from .filters import SubscriptionFilterSet
        f = SubscriptionFilterSet(
            {'renewal_within': '60'},
            queryset=Subscription.objects.all(),
        )
        self.assertTrue(f.is_valid())
        self.assertIn(self.sub_active, f.qs)


class SubscriptionViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_user(
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
        self.user = get_user_model().objects.create_user(
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
        self.user = get_user_model().objects.create_user(
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
        
        # Verify assignment exists
        assignment = SubscriptionAssignment.objects.filter(
            subscription=self.subscription,
            content_type=self.asset_ct,
            object_id=self.asset.pk
        ).first()
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.notes, "Test support assignment")
        self.assertEqual(assignment.assigned_by, self.user)

    def test_assignment_delete_view_post(self):
        # Create assignment first
        assignment = SubscriptionAssignment.objects.create(
            subscription=self.subscription,
            content_type=self.asset_ct,
            object_id=self.asset.pk,
            assigned_by=self.user
        )
        url = reverse("subscriptions:subscriptionassignment_delete", kwargs={"pk": assignment.pk})
        resp = self.client.post(url)
        self.assertEqual(resp.status_code, 302)
        
        # Verify assignment was deleted
        self.assertFalse(SubscriptionAssignment.objects.filter(pk=assignment.pk).exists())

