"""WS2-1: the REST inventory-assignment CRUD path must enforce availability like
checkout_inventory_item(). Previously a POST with from_location omitted (or qty > available)
bypassed every check because adjust_inventory_stock only checks/deducts when from_location is set."""

from datetime import datetime, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.exceptions import ValidationError

from assets.models import Category, Manufacturer
from core.tests.mixins import grant
from inventory.models import Accessory, AccessoryAssignment, AccessoryStock
from inventory.services import checkout_inventory_item
from organization.models import (
    AssetHolder,
    Location,
    Role,
    Site,
    Tenant,
)

User = get_user_model()


class InventoryAssignmentOverAllocationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Tenant", slug="t-inv")
        self.user = User.objects.create_user(username="invuser", password="pw")
        role = Role.objects.create(
            tenant=self.tenant,
            name="Admin",
            permissions=[
                "inventory.add_accessoryassignment",
                "inventory.view_accessoryassignment",
                "inventory.change_accessoryassignment",
            ],
        )
        grant(self.user, self.tenant, role)
        self.mfr = Manufacturer.objects.create(name="Logitech", slug="logi-inv")
        self.cat = Category.objects.create(name="Acc Cat", slug="acc-cat-inv", applies_to={"accessory": True})
        self.accessory = Accessory.objects.create(
            name="Mouse", manufacturer=self.mfr, category=self.cat, tenant=self.tenant
        )
        self.site = Site.objects.create(name="Site", slug="site-inv", tenant=self.tenant)
        self.location = Location.objects.create(name="Loc", slug="loc-inv", site=self.site, tenant=self.tenant)
        # total_stock == 2 -> available == 2
        AccessoryStock.objects.create(accessory=self.accessory, location=self.location, qty=2)
        self.holder = AssetHolder.objects.create(first_name="H", last_name="H", upn="h-inv", tenant=self.tenant)

    def _activate(self):
        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

    def test_cannot_over_allocate_without_from_location(self):
        self._activate()
        url = reverse("api:inventory_api:accessoryassignment-list")
        resp = self.client.post(
            url,
            {
                "accessory_id": self.accessory.pk,
                "assigned_holder_id": self.holder.pk,
                "qty": 5,  # > available (2), from_location omitted -> must be rejected
            },
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)

    def test_within_availability_succeeds(self):
        self._activate()
        url = reverse("api:inventory_api:accessoryassignment-list")
        resp = self.client.post(
            url,
            {
                "accessory_id": self.accessory.pk,
                "assigned_holder_id": self.holder.pk,
                "qty": 2,
            },
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)

    def test_omitted_qty_defaults_to_one(self):
        self._activate()
        response = self.client.post(
            reverse("api:inventory_api:accessoryassignment-list"),
            {
                "accessory_id": self.accessory.pk,
                "assigned_holder_id": self.holder.pk,
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["qty"], 1)
        self.assertEqual(AccessoryAssignment._base_manager.get().qty, 1)

    def test_assigned_date_is_preserved_through_service_adapter(self):
        self._activate()
        response = self.client.post(
            reverse("api:inventory_api:accessoryassignment-list"),
            {
                "accessory_id": self.accessory.pk,
                "assigned_holder_id": self.holder.pk,
                "assigned_date": "2024-01-02T03:04:05Z",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        assignment = AccessoryAssignment._base_manager.get()
        self.assertEqual(assignment.assigned_date, datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc))

    def test_bulk_post_defaults_qty_and_returns_list(self):
        self._activate()
        response = self.client.post(
            reverse("api:inventory_api:accessoryassignment-list"),
            [
                {
                    "accessory_id": self.accessory.pk,
                    "assigned_holder_id": self.holder.pk,
                },
                {
                    "accessory_id": self.accessory.pk,
                    "assigned_holder_id": self.holder.pk,
                    "qty": 1,
                },
            ],
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual([row["qty"] for row in response.json()], [1, 1])
        self.assertEqual(AccessoryAssignment._base_manager.count(), 2)

    def test_bulk_post_rolls_back_when_second_service_call_fails(self):
        self._activate()
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ValidationError("injected second-row failure")
            return checkout_inventory_item(*args, **kwargs)

        with patch("inventory.api.views.checkout_inventory_item", side_effect=fail_second):
            response = self.client.post(
                reverse("api:inventory_api:accessoryassignment-list"),
                [
                    {
                        "accessory_id": self.accessory.pk,
                        "assigned_holder_id": self.holder.pk,
                    },
                    {
                        "accessory_id": self.accessory.pk,
                        "assigned_holder_id": self.holder.pk,
                    },
                ],
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(AccessoryAssignment._base_manager.count(), 0)
        self.assertEqual(AccessoryStock._base_manager.get().qty, 2)

    def test_cannot_over_allocate_on_patch_without_from_location(self):
        # D5-1: the over-allocation guard was create-only. A PATCH raising `qty`
        # on an existing assignment (from_location still omitted) bypassed it
        # entirely and drove Accessory.available negative with no error.
        self._activate()
        list_url = reverse("api:inventory_api:accessoryassignment-list")
        create_resp = self.client.post(
            list_url,
            {
                "accessory_id": self.accessory.pk,
                "assigned_holder_id": self.holder.pk,
                "qty": 2,  # == available (2) -> succeeds
            },
            content_type="application/json",
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.content)
        assignment_id = create_resp.json()["id"]
        etag = create_resp["ETag"]

        detail_url = reverse("api:inventory_api:accessoryassignment-detail", kwargs={"pk": assignment_id})
        patch_resp = self.client.patch(detail_url, {"qty": 1000}, content_type="application/json", HTTP_IF_MATCH=etag)
        self.assertEqual(patch_resp.status_code, 400, patch_resp.content)
        self.accessory.refresh_from_db()
        self.assertEqual(self.accessory.available, 0)
