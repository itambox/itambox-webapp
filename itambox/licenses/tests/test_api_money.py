"""REST regression tests for the license money field (#612, XM-06).

The GraphQL mutations typed ``purchaseCost`` as a binary float. The REST surface is the only write path
now, so it must store exact decimals and reject sub-cent precision.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assets.models import Manufacturer
from core.tests.mixins import grant
from licenses.models import License
from organization.models import Role, Tenant
from software.models import Software


class LicensePurchaseCostRestTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="lic-money", email="lic-money@example.com", password="pw")
        self.reader = user_model.objects.create_user(
            username="lic-reader", email="lic-reader@example.com", password="pw"
        )
        self.tenant = Tenant.objects.create(name="Lic Money", slug="lic-money")
        manufacturer = Manufacturer.objects.create(name="Lic Money Maker")
        self.software = Software.objects.create(name="Lic Money App", manufacturer=manufacturer, tenant=self.tenant)
        grant(
            self.user,
            self.tenant,
            Role.objects.create(
                tenant=self.tenant,
                name="License Writer",
                permissions=["licenses.view_license", "licenses.add_license", "licenses.change_license"],
            ),
        )
        grant(
            self.reader,
            self.tenant,
            Role.objects.create(tenant=self.tenant, name="License Reader", permissions=["licenses.view_license"]),
        )
        self.url = reverse("api:licenses_api:license-list")

    def _login(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

    def _payload(self, **extra):
        return {"name": "Money License", "software_id": self.software.pk, "seats": 5, **extra}

    def test_string_and_number_purchase_cost_are_stored_exactly(self):
        self._login(self.user)
        for index, value in enumerate(("19.99", 19.99)):
            response = self.client.post(
                self.url, self._payload(name=f"Money {index}", purchase_cost=value), format="json"
            )
            self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
            self.assertEqual(License.objects.get(pk=response.data["id"]).purchase_cost, Decimal("19.99"))

    def test_three_decimal_purchase_cost_is_rejected(self):
        self._login(self.user)
        response = self.client.post(self.url, self._payload(purchase_cost="19.999"), format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("purchase_cost", response.data)

    def test_update_stores_exact_decimal(self):
        self._login(self.user)
        created = self.client.post(self.url, self._payload(purchase_cost="1.00"), format="json")
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.content)
        detail = reverse("api:licenses_api:license-detail", kwargs={"pk": created.data["id"]})
        response = self.client.patch(detail, {"purchase_cost": 19.99}, format="json", HTTP_IF_MATCH=created["ETag"])
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(License.objects.get(pk=created.data["id"]).purchase_cost, Decimal("19.99"))

    def test_read_only_role_cannot_write(self):
        self._login(self.reader)
        response = self.client.post(self.url, self._payload(purchase_cost="5.00"), format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
