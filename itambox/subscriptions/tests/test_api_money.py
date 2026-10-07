"""REST regression tests for the subscription money field (#612, XM-06).

The GraphQL mutations typed ``renewalCost`` as a binary float. The REST surface is the only write path
now, so create, update and the renew action must store exact decimals and reject sub-cent precision.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assets.models import Supplier
from core.tests.mixins import grant
from organization.models import Role, Tenant
from subscriptions.models import Subscription

WRITE_PERMISSIONS = [
    "subscriptions.view_subscription",
    "subscriptions.add_subscription",
    "subscriptions.change_subscription",
]


class SubscriptionRenewalCostRestTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="sub-money", email="sub-money@example.com", password="pw")
        self.reader = user_model.objects.create_user(
            username="sub-reader", email="sub-reader@example.com", password="pw"
        )
        self.tenant = Tenant.objects.create(name="Sub Money", slug="sub-money")
        self.supplier = Supplier.objects.create(name="Sub Money Supplier", tenant=self.tenant)
        grant(
            self.user,
            self.tenant,
            Role.objects.create(tenant=self.tenant, name="Sub Writer", permissions=WRITE_PERMISSIONS),
        )
        grant(
            self.reader,
            self.tenant,
            Role.objects.create(tenant=self.tenant, name="Sub Reader", permissions=["subscriptions.view_subscription"]),
        )
        self.url = reverse("api:subscriptions_api:subscription-list")

    def _login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

    def _payload(self, **extra):
        return {"name": "Money Sub", "supplier_id": self.supplier.pk, "type": "saas", "status": "active", **extra}

    def _create(self, **extra):
        response = self.client.post(self.url, self._payload(**extra), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        return response

    def _detail_url(self, pk):
        return reverse("api:subscriptions_api:subscription-detail", kwargs={"pk": pk})

    def test_string_and_number_renewal_cost_are_stored_exactly(self):
        self._login(self.user)
        for index, value in enumerate(("19.99", 19.99)):
            response = self._create(name=f"Money {index}", renewal_cost=value)
            self.assertEqual(Subscription.objects.get(pk=response.data["id"]).renewal_cost, Decimal("19.99"))

    def test_three_decimal_renewal_cost_is_rejected_on_create(self):
        self._login(self.user)
        response = self.client.post(self.url, self._payload(renewal_cost="19.999"), format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("renewal_cost", response.data)

    def test_update_stores_exact_decimal(self):
        self._login(self.user)
        created = self._create(renewal_cost="1.00")
        response = self.client.patch(
            self._detail_url(created.data["id"]), {"renewal_cost": 19.99}, format="json", HTTP_IF_MATCH=created["ETag"]
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(Subscription.objects.get(pk=created.data["id"]).renewal_cost, Decimal("19.99"))

    def test_renew_action_stores_exact_decimal_and_rejects_sub_cent(self):
        self._login(self.user)
        created = self._create(renewal_cost="1.00")
        pk = created.data["id"]
        renew = reverse("api:subscriptions_api:subscription-renew", kwargs={"pk": pk})
        rejected = self.client.post(
            renew,
            {"renewal_date": "2031-01-15", "renewal_cost": "19.999"},
            format="json",
            HTTP_IF_MATCH=created["ETag"],
        )
        self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("renewal_cost", rejected.data)
        for value in ("19.99", 19.99):
            current = self.client.get(self._detail_url(pk))
            response = self.client.post(
                renew,
                {"renewal_date": "2031-01-15", "renewal_cost": value},
                format="json",
                HTTP_IF_MATCH=current["ETag"],
            )
            self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
            self.assertEqual(Subscription.objects.get(pk=pk).renewal_cost, Decimal("19.99"))

    def test_read_only_role_cannot_write_or_run_lifecycle_actions(self):
        self._login(self.reader)
        denied = self.client.post(self.url, self._payload(renewal_cost="5.00"), format="json")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        self._login(self.user)
        created = self._create()
        self._login(self.reader)
        suspend = reverse("api:subscriptions_api:subscription-suspend", kwargs={"pk": created.data["id"]})
        response = self.client.post(suspend, format="json", HTTP_IF_MATCH=created["ETag"])
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
