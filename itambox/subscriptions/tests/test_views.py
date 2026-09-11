import re
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from assets.models import Asset
from extras.models import JournalEntry
from organization.models import Location, Site, Tenant, TenantGroup
from subscriptions.models import (
    BillingCycleChoices,
    Provider,
    Subscription,
    SubscriptionAssignment,
    SubscriptionStatusChoices,
    SubscriptionTypeChoices,
)

User = get_user_model()


class SubscriptionViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="testuser", password="testpass", is_staff=True, is_superuser=True)
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

    def test_detail_renders_seat_usage_with_one_assignment_count_query(self):
        url = reverse("subscriptions:subscription_detail", kwargs={"pk": self.sub.pk})
        with CaptureQueriesContext(connection) as queries:
            resp = self.client.get(url)

        self.assertEqual(resp.status_code, 200)
        seat_queries = [query for query in queries.captured_queries if "licenseseatassignment" in query["sql"].lower()]
        self.assertEqual(len(seat_queries), 1, queries.captured_queries)

    def _detail_content(self, sub):
        url = reverse("subscriptions:subscription_detail", kwargs={"pk": sub.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return resp.content.decode()

    def _row_value(self, content, label):
        """Return the <dd> body rendered after the given labeled <dt>."""
        match = re.search(re.escape(label) + r"\s*</dt>\s*<dd[^>]*>(.*?)</dd>", content, re.S)
        self.assertIsNotNone(match, f"no rendered row for label {label!r}")
        return match.group(1)

    def test_detail_labels_one_time_cost_and_omits_annual_row(self):
        """Issue #501: a one-time payment must not read as a yearly cost."""
        sub = Subscription.objects.create(
            name="Perpetual Software",
            provider=self.provider,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_cost=Decimal("500.00"),
            currency="EUR",
            billing_cycle=BillingCycleChoices.ONETIME,
        )
        content = self._detail_content(sub)

        self.assertIn("One-Time Cost:", content)
        self.assertIn("500.00 EUR", self._row_value(content, "One-Time Cost:"))
        self.assertNotIn("Renewal Cost:", content)
        self.assertNotIn("Est. Annual Cost:", content)
        self.assertNotIn("Annualized Cost:", content)

    def test_detail_labels_multi_year_annual_cost_as_annualized(self):
        """Issue #501: the multi-year figure in the annual slot is the annualized value."""
        sub = Subscription.objects.create(
            name="Three-Year Contract",
            provider=self.provider,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_cost=Decimal("3600.00"),
            currency="EUR",
            billing_cycle=BillingCycleChoices.MULTI_YEAR,
            term_months=36,
        )
        content = self._detail_content(sub)

        self.assertIn("3600.00 EUR", self._row_value(content, "Renewal Cost:"))
        self.assertIn("1200.00 EUR", self._row_value(content, "Annualized Cost:"))
        self.assertNotIn("Est. Annual Cost:", content)

    def test_detail_omits_annual_row_for_multi_year_without_term(self):
        """Issue #501 fallback: no yearly figure when the term is unknown."""
        sub = Subscription.objects.create(
            name="Termless Multi-Year",
            provider=self.provider,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_cost=Decimal("3600.00"),
            currency="EUR",
            billing_cycle=BillingCycleChoices.MULTI_YEAR,
        )
        content = self._detail_content(sub)

        self.assertIn("3600.00 EUR", self._row_value(content, "Renewal Cost:"))
        self.assertNotIn("Annualized Cost:", content)
        self.assertNotIn("Est. Annual Cost:", content)

    def test_detail_renders_zero_cost_as_zero(self):
        """Issue #501: a zero-cost row shows 0.00 instead of being dropped or showing Not set."""
        sub = Subscription.objects.create(
            name="Free Annual Plan",
            provider=self.provider,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_cost=Decimal("0.00"),
            currency="EUR",
            billing_cycle=BillingCycleChoices.ANNUAL,
        )
        content = self._detail_content(sub)

        self.assertIn("0.00 EUR", self._row_value(content, "Renewal Cost:"))
        self.assertIn("0.00 EUR", self._row_value(content, "Est. Annual Cost:"))

    def test_create_view_get(self):
        url = reverse("subscriptions:subscription_create")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_create_view_post(self):
        url = reverse("subscriptions:subscription_create")
        resp = self.client.post(
            url,
            {
                "name": "New Subscription",
                "provider": self.provider.pk,
                "type": SubscriptionTypeChoices.SAAS,
                "status": SubscriptionStatusChoices.ACTIVE,
                "renewal_cost": "499.00",
                "currency": "USD",
                "billing_cycle": BillingCycleChoices.ANNUAL,
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Subscription.objects.filter(name="New Subscription").exists())

    def test_edit_view_get(self):
        url = reverse("subscriptions:subscription_update", kwargs={"pk": self.sub.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)

    def test_edit_view_post(self):
        url = reverse("subscriptions:subscription_update", kwargs={"pk": self.sub.pk})
        resp = self.client.post(
            url,
            {
                "name": "Renamed Subscription",
                "provider": self.provider.pk,
                "type": SubscriptionTypeChoices.SAAS,
                "status": SubscriptionStatusChoices.ACTIVE,
                "renewal_cost": "999.99",
                "currency": "EUR",
                "billing_cycle": BillingCycleChoices.ANNUAL,
            },
        )
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
        self.user = User.objects.create_user(username="testuser", password="testpass", is_staff=True, is_superuser=True)
        self.client.login(username="testuser", password="testpass")
        self.provider = Provider.objects.create(name="AWS", account_id="aws-001", is_active=True)

    def test_list_view_status_200(self):
        url = reverse("subscriptions:provider_list")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "AWS")

    def test_list_view_renders_subscription_count_link(self):
        # A provider WITH subscriptions exercises render_subscription_count,
        # which builds a reverse() link to the filtered subscription list. The
        # plain list test above uses a provider with zero subscriptions and so
        # never hit this branch (regression: missing `reverse` import -> 500).
        Subscription.objects.create(
            name="AWS Sub",
            provider=self.provider,
            type=SubscriptionTypeChoices.SAAS,
            status=SubscriptionStatusChoices.ACTIVE,
            renewal_cost=10,
            currency="EUR",
            billing_cycle=BillingCycleChoices.ANNUAL,
        )
        url = reverse("subscriptions:provider_list")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        expected = f"{reverse('subscriptions:subscription_list')}?provider={self.provider.pk}"
        self.assertContains(resp, expected)

    def test_detail_view_status_200(self):
        url = reverse("subscriptions:provider_detail", kwargs={"pk": self.provider.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "AWS")

    def test_create_view_post(self):
        url = reverse("subscriptions:provider_create")
        resp = self.client.post(
            url,
            {
                "name": "Google Cloud",
                "account_id": "gcp-001",
            },
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Provider.objects.filter(name="Google Cloud").exists())

    def test_edit_view_post(self):
        url = reverse("subscriptions:provider_update", kwargs={"pk": self.provider.pk})
        resp = self.client.post(
            url,
            {
                "name": "Amazon Web Services",
                "account_id": "aws-001",
            },
        )
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
        self.user = User.objects.create_user(username="testuser", password="testpass", is_staff=True, is_superuser=True)
        self.client.login(username="testuser", password="testpass")
        self.provider = Provider.objects.create(name="AWS", is_active=True)
        self.subscription = Subscription.objects.create(
            name="AWS Business Support",
            provider=self.provider,
            status=SubscriptionStatusChoices.ACTIVE,
        )
        self.tg = TenantGroup.objects.create(name="TG1", slug="tg1")
        self.tenant = Tenant.objects.create(name="Tenant1", slug="tenant1", group=self.tg)
        self.subscription.tenant = self.tenant
        self.subscription.save(update_fields=["tenant"])
        self.site = Site.objects.create(name="Dublin", slug="dublin", tenant=self.tenant)
        self.location = Location.objects.create(name="Rack A", slug="rack-a", site=self.site, tenant=self.tenant)
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
        resp = self.client.post(
            post_url,
            {
                "subscription": self.subscription.pk,
                "notes": "Test support assignment",
            },
        )
        self.assertEqual(resp.status_code, 302)

        assignment = SubscriptionAssignment.objects.filter(
            subscription=self.subscription, content_type=self.asset_ct, object_id=self.asset.pk
        ).first()
        self.assertIsNotNone(assignment)
        self.assertEqual(assignment.notes, "Test support assignment")
        self.assertEqual(assignment.assigned_by, self.user)

    def test_assignment_delete_view_post(self):
        assignment = SubscriptionAssignment.objects.create(
            subscription=self.subscription, content_type=self.asset_ct, object_id=self.asset.pk, assigned_by=self.user
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
        self.user = User.objects.create_user(username="testuser", password="testpass", is_staff=True, is_superuser=True)
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
        self.sub.tenant = self.tenant
        self.sub.save(update_fields=["tenant"])
        self.site = Site.objects.create(name="Dublin", slug="dublin", tenant=self.tenant)
        self.location = Location.objects.create(name="Rack A", slug="rack-a", site=self.site, tenant=self.tenant)
        self.asset = Asset.objects.create(
            name="Server Ireland",
            asset_tag="SRV-IRE-01",
            location=self.location,
            tenant=self.tenant,
        )
        self.holder = AssetHolder.objects.create(
            first_name="John", last_name="Doe", email="john@example.com", tenant=self.tenant
        )

    def test_subscription_renew_workflow(self):
        # 1. Test GET request renders the form
        url = reverse("subscriptions:subscription_renew", kwargs={"pk": self.sub.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Renew Subscription:")

        # 2. Test POST request updates the subscription and returns HTMX headers
        new_renewal_date = date(2027, 6, 1)
        resp = self.client.post(url, {"renewal_date": "2027-06-01", "renewal_cost": "1099.99"}, HTTP_HX_REQUEST="true")
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
        resp = self.client.post(
            url, {"cancellation_date": "2026-06-02", "reason": "Moving to AWS"}, HTTP_HX_REQUEST="true"
        )
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

        resume_url = reverse("subscriptions:subscription_resume", kwargs={"pk": self.sub.pk})
        resp = self.client.post(resume_url, HTTP_HX_REQUEST="true")
        self.assertEqual(resp.status_code, 204)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.status, SubscriptionStatusChoices.ACTIVE)
        self.assertIn("tableRefreshRequired", resp["HX-Trigger"])

    def test_illegal_lifecycle_transitions_return_controlled_responses(self):
        self.sub.cancel(cancellation_date=date(2026, 6, 2), reason="terminal")

        renew_url = reverse("subscriptions:subscription_renew", kwargs={"pk": self.sub.pk})
        response = self.client.post(renew_url, {"renewal_date": "2027-06-01", "renewal_cost": "1099.99"})
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            None,
            "Invalid subscription status transition from cancelled to active.",
        )

        suspend_url = reverse("subscriptions:subscription_suspend", kwargs={"pk": self.sub.pk})
        response = self.client.post(suspend_url)
        self.assertEqual(response.status_code, 400)

        resume_url = reverse("subscriptions:subscription_resume", kwargs={"pk": self.sub.pk})
        response = self.client.post(resume_url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 204)
        self.assertIn('"level": "danger"', response["HX-Trigger"])

    def test_lifecycle_view_retries_do_not_duplicate_journal_entries(self):
        object_type = ContentType.objects.get_for_model(Subscription)
        entries = JournalEntry.objects.filter(model=object_type, object_id=self.sub.pk)

        renew_url = reverse("subscriptions:subscription_renew", kwargs={"pk": self.sub.pk})
        renewal = {"renewal_date": "2027-06-01", "renewal_cost": "1099.99"}
        self.assertEqual(self.client.post(renew_url, renewal).status_code, 302)
        count_after_renewal = entries.count()
        self.assertEqual(self.client.post(renew_url, renewal).status_code, 302)
        self.assertEqual(entries.count(), count_after_renewal)

        suspend_url = reverse("subscriptions:subscription_suspend", kwargs={"pk": self.sub.pk})
        self.assertEqual(self.client.post(suspend_url).status_code, 302)
        count_after_suspend = entries.count()
        self.assertEqual(self.client.post(suspend_url).status_code, 302)
        self.assertEqual(entries.count(), count_after_suspend)

        resume_url = reverse("subscriptions:subscription_resume", kwargs={"pk": self.sub.pk})
        self.assertEqual(self.client.post(resume_url).status_code, 302)
        count_after_resume = entries.count()
        self.assertEqual(self.client.post(resume_url).status_code, 302)
        self.assertEqual(entries.count(), count_after_resume)

        cancel_url = reverse("subscriptions:subscription_cancel", kwargs={"pk": self.sub.pk})
        cancellation = {"cancellation_date": "2026-06-02", "reason": "retired"}
        self.assertEqual(self.client.post(cancel_url, cancellation).status_code, 302)
        count_after_cancel = entries.count()
        self.assertEqual(self.client.post(cancel_url, cancellation).status_code, 302)
        self.assertEqual(entries.count(), count_after_cancel)

    def test_subscription_assignment_lifecycle(self):
        # 1. Test GET request to checkout/assign renders form
        url = reverse("subscriptions:subscription_checkout", kwargs={"pk": self.sub.pk})
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Assign Subscription:")

        # 2. Test POST checkout to Employee (AssetHolder)
        resp = self.client.post(
            url,
            {"target_type": "holder", "assigned_holder": self.holder.pk, "notes": "Assigning to John"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(self.sub.assignments.count(), 1)
        assignment = self.sub.assignments.first()
        self.assertEqual(assignment.assigned_object, self.holder)
        self.assertEqual(assignment.notes, "Assigning to John")

        # 3. Test POST checkout to Hardware Asset
        resp = self.client.post(
            url,
            {"target_type": "asset", "asset": self.asset.pk, "notes": "Assigning to server"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(self.sub.assignments.count(), 2)

        # 4. Checkin / Delete assignment restores capacity
        delete_url = reverse("subscriptions:subscriptionassignment_delete", kwargs={"pk": assignment.pk})
        resp = self.client.post(delete_url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.sub.assignments.count(), 1)
