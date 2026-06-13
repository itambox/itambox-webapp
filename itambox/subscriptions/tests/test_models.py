import datetime
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError
from django.utils import timezone
from assets.models import Asset
from organization.models import AssetHolder, Location, Site, Tenant, TenantGroup
from subscriptions.models import (
    Provider, Subscription, SubscriptionAssignment,
    SubscriptionTypeChoices, SubscriptionStatusChoices, BillingCycleChoices,
)
from model_bakery import baker
from software.models import Software
from licenses.models import License, LicenseSeatAssignment

User = get_user_model()


class SubscriptionSeatRollupTests(TestCase):
    """Seats are tracked on Licenses; a Subscription rolls them up across the
    licenses it funds (Subscription -> License -> Software)."""

    def test_seats_roll_up_from_funded_licenses(self):
        sub = baker.make(Subscription, tenant=None)
        software = baker.make(Software, manufacturer__name="Acme", manufacturer__slug="acme", tenant=None)
        baker.make(License, software=software, subscription=sub, seats=10, tenant=None)
        l2 = baker.make(License, software=software, subscription=sub, seats=5, tenant=None)
        # A license NOT funded by this subscription must not be counted.
        baker.make(License, software=software, subscription=None, seats=99, tenant=None)

        self.assertEqual(sub.total_seats, 15)
        self.assertEqual(sub.assigned_seats, 0)
        self.assertEqual(sub.available_seats, 15)

        holder = baker.make(AssetHolder, tenant=None)
        baker.make(LicenseSeatAssignment, license=l2, assigned_holder=holder, asset=None)
        self.assertEqual(sub.assigned_seats, 1)
        self.assertEqual(sub.available_seats, 14)

    def test_license_rejects_cross_tenant_subscription(self):
        t_a = baker.make(Tenant, name="A", slug="a")
        t_b = baker.make(Tenant, name="B", slug="b")
        sub_b = baker.make(Subscription, tenant=t_b)
        software = baker.make(Software, manufacturer__name="Acme2", manufacturer__slug="acme2", tenant=None)
        lic = baker.prepare(License, software=software, subscription=sub_b, seats=1, tenant=t_a)
        with self.assertRaises(ValidationError):
            lic.clean()

class ProviderModelTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(
            name="AWS",
            account_id="aws-12345",
            portal_url="https://aws.amazon.com/console",
            is_active=True,
        )

    def test_provider_creation(self):
        self.assertEqual(str(self.provider), "AWS")
        self.assertTrue(self.provider.is_active)

    def test_provider_contact_resolution(self):
        from organization.models import Contact, ContactRole, ContactAssignment
        role, _ = ContactRole.objects.get_or_create(
            slug="primary-contact",
            defaults={"name": "Primary Contact", "description": "Primary Contact"}
        )
        contact = Contact.objects.create(
            name="AWS Account Manager",
            email="manager@aws.example.com",
            phone="+1-800-555-0199"
        )
        ContactAssignment.objects.create(
            contact=contact,
            role=role,
            content_type=ContentType.objects.get_for_model(Provider),
            object_id=self.provider.pk,
            priority="primary"
        )
        self.assertEqual(self.provider.primary_contact, contact)

    def test_provider_absolute_url(self):
        url = self.provider.get_absolute_url()
        self.assertIn(str(self.provider.pk), url)

    def test_provider_slug_auto_generation(self):
        provider = Provider.objects.create(name="Google Cloud Platform")
        self.assertEqual(provider.slug, "google-cloud-platform")

    def test_provider_inactive_does_not_filter_out(self):
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
