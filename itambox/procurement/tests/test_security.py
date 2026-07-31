import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assets.choices import RequestStatusChoices
from assets.models import AssetRequest, AssetType, Manufacturer, Supplier
from core.tests.mixins import grant
from itambox.api.mixins import ETagMixin
from organization.models import Location, Role, Site, Tenant
from procurement.models import Contract, FulfillmentLink, PurchaseOrder, PurchaseOrderLine

User = get_user_model()


class ContractTenantIsolationSetupMixin:
    def setUp(self):
        super().setUp()
        self.tenant_a = Tenant.objects.create(name="Contract API Tenant A", slug="contract-api-tenant-a")
        self.tenant_b = Tenant.objects.create(name="Contract API Tenant B", slug="contract-api-tenant-b")
        self.member_a = User.objects.create_user(username="contract-api-member-a", password="password")
        role_a = Role.objects.create(
            tenant=self.tenant_a,
            name="Contract reader",
            permissions=["procurement.view_contract"],
        )
        grant(self.member_a, self.tenant_a, role_a)
        self.writer_a = User.objects.create_user(username="contract-api-writer-a", password="password")
        writer_role_a = Role.objects.create(
            tenant=self.tenant_a,
            name="Contract writer",
            permissions=["procurement.view_contract", "procurement.change_contract"],
        )
        grant(self.writer_a, self.tenant_a, writer_role_a)
        self.contract_a = self._make_contract(self.tenant_a, "CTR-TENANT-A")
        self.contract_b = self._make_contract(self.tenant_b, "CTR-TENANT-B")

    def _make_contract(self, tenant, contract_number):
        return Contract.objects.create(
            tenant=tenant,
            name=f"Contract for {tenant.name}",
            contract_number=contract_number,
            contract_type="support",
            status="active",
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2027, 1, 1),
        )

    def _login_to_tenant_a(self):
        self.client.force_login(self.member_a)
        session = self.client.session
        session["active_tenant_id"] = self.tenant_a.pk
        session.save()

    def _login_writer_to_tenant_a(self):
        self.client.force_login(self.writer_a)
        session = self.client.session
        session["active_tenant_id"] = self.tenant_a.pk
        session.save()

    def _etag(self, contract):
        contract.refresh_from_db()
        return 'W/"' + contract.updated_at.isoformat() + '"'

    def _ui_update_payload(self, contract, name):
        return {
            "name": name,
            "contract_number": contract.contract_number,
            "contract_type": contract.contract_type,
            "status": contract.status,
            "currency": contract.currency,
            "billing_cycle": contract.billing_cycle,
            "start_date": contract.start_date.isoformat(),
            "end_date": contract.end_date.isoformat(),
            "tenant": self.tenant_a.pk,
        }


class ContractTenantIsolationAPITests(ContractTenantIsolationSetupMixin, APITestCase):
    """Characterize the Stable REST boundary for tenant-owned contracts."""

    def test_list_excludes_other_tenant_contract(self):
        self._login_to_tenant_a()

        response = self.client.get(reverse("api:procurement_api:contract-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        rows = response.data["results"] if isinstance(response.data, dict) else response.data
        returned_ids = {row["id"] for row in rows}
        self.assertEqual(returned_ids, {self.contract_a.pk})

    def test_detail_for_other_tenant_contract_returns_404(self):
        self._login_to_tenant_a()
        own_detail_url = reverse("api:procurement_api:contract-detail", kwargs={"pk": self.contract_a.pk})
        foreign_detail_url = reverse("api:procurement_api:contract-detail", kwargs={"pk": self.contract_b.pk})

        own_response = self.client.get(own_detail_url)
        foreign_response = self.client.get(foreign_detail_url)

        self.assertEqual(own_response.status_code, status.HTTP_200_OK, own_response.content)
        self.assertEqual(foreign_response.status_code, status.HTTP_404_NOT_FOUND, foreign_response.content)

    def test_update_for_other_tenant_contract_returns_404_without_mutation(self):
        self._login_writer_to_tenant_a()
        own_response = self.client.patch(
            reverse("api:procurement_api:contract-detail", kwargs={"pk": self.contract_a.pk}),
            {"name": "Authorized tenant update"},
            format="json",
            HTTP_IF_MATCH=self._etag(self.contract_a),
        )
        self.assertEqual(own_response.status_code, status.HTTP_200_OK, own_response.content)
        self.contract_a.refresh_from_db()
        self.assertEqual(self.contract_a.name, "Authorized tenant update")

        self.contract_b.refresh_from_db()
        original_name = self.contract_b.name
        original_updated_at = self.contract_b.updated_at

        response = self.client.patch(
            reverse("api:procurement_api:contract-detail", kwargs={"pk": self.contract_b.pk}),
            {"name": "Cross-tenant overwrite"},
            format="json",
            HTTP_IF_MATCH=self._etag(self.contract_b),
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.content)
        self.contract_b.refresh_from_db()
        self.assertEqual(self.contract_b.name, original_name)
        self.assertEqual(self.contract_b.updated_at, original_updated_at)


class ContractTenantIsolationUITests(ContractTenantIsolationSetupMixin, TestCase):
    """Characterize the Stable UI boundary for tenant-owned contracts."""

    def test_ui_list_excludes_other_tenant_contract(self):
        self._login_to_tenant_a()

        response = self.client.get(reverse("procurement:contract_list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertContains(response, self.contract_a.contract_number)
        self.assertNotContains(response, self.contract_b.contract_number)

    def test_ui_detail_for_other_tenant_contract_returns_404(self):
        self._login_to_tenant_a()
        own_detail_url = reverse("procurement:contract_detail", kwargs={"pk": self.contract_a.pk})
        foreign_detail_url = reverse("procurement:contract_detail", kwargs={"pk": self.contract_b.pk})

        own_response = self.client.get(own_detail_url)
        foreign_response = self.client.get(foreign_detail_url)

        self.assertEqual(own_response.status_code, status.HTTP_200_OK, own_response.content)
        self.assertContains(own_response, self.contract_a.contract_number)
        self.assertEqual(foreign_response.status_code, status.HTTP_404_NOT_FOUND, foreign_response.content)
        self.assertNotContains(
            foreign_response,
            self.contract_b.contract_number,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    def test_ui_update_for_other_tenant_contract_returns_404_without_mutation(self):
        self._login_writer_to_tenant_a()
        own_response = self.client.post(
            reverse("procurement:contract_edit", kwargs={"pk": self.contract_a.pk}),
            self._ui_update_payload(self.contract_a, "Authorized tenant UI update"),
        )
        self.assertEqual(own_response.status_code, status.HTTP_302_FOUND, own_response.content)
        self.contract_a.refresh_from_db()
        self.assertEqual(self.contract_a.name, "Authorized tenant UI update")

        self.contract_b.refresh_from_db()
        original_name = self.contract_b.name
        original_updated_at = self.contract_b.updated_at

        response = self.client.post(
            reverse("procurement:contract_edit", kwargs={"pk": self.contract_b.pk}),
            self._ui_update_payload(self.contract_b, "Cross-tenant overwrite"),
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.content)
        self.contract_b.refresh_from_db()
        self.assertEqual(self.contract_b.name, original_name)
        self.assertEqual(self.contract_b.updated_at, original_updated_at)


class PurchaseOrderTenantIsolationSetupMixin:
    def setUp(self):
        super().setUp()
        self.tenant_a = Tenant.objects.create(name="PO API Tenant A", slug="po-security-api-tenant-a")
        self.tenant_b = Tenant.objects.create(name="PO API Tenant B", slug="po-security-api-tenant-b")
        self.writer_a = User.objects.create_user(username="po-security-api-writer-a", password="password")
        writer_role_a = Role.objects.create(
            tenant=self.tenant_a,
            name="Purchase order writer",
            permissions=[
                "procurement.view_purchaseorder",
                "procurement.change_purchaseorder",
                "procurement.approve_purchaseorder",
                "procurement.receive_purchaseorder",
            ],
        )
        grant(self.writer_a, self.tenant_a, writer_role_a)
        self.writer_b = User.objects.create_user(username="po-security-api-writer-b", password="password")
        writer_role_b = Role.objects.create(
            tenant=self.tenant_b,
            name="Purchase order writer",
            permissions=[
                "procurement.view_purchaseorder",
                "procurement.change_purchaseorder",
                "procurement.approve_purchaseorder",
                "procurement.receive_purchaseorder",
            ],
        )
        grant(self.writer_b, self.tenant_b, writer_role_b)
        self.site = Site.objects.create(name="PO Security API Site", slug="po-security-api-site")
        self.supplier = Supplier.objects.create(name="PO Security API Supplier", slug="po-security-api-supplier")
        self.purchase_order_a = self._make_purchase_order(
            self.tenant_a,
            "PO-SECURITY-TENANT-A",
            self.writer_a,
        )
        self.purchase_order_b = self._make_purchase_order(
            self.tenant_b,
            "PO-SECURITY-TENANT-B",
            self.writer_b,
        )

    def _make_purchase_order(self, tenant, order_number, created_by):
        location = Location.objects.create(
            name=f"Location for {tenant.name}",
            slug=f"po-security-location-{tenant.pk}",
            site=self.site,
            tenant=tenant,
        )
        return PurchaseOrder.objects.create(
            tenant=tenant,
            order_number=order_number,
            supplier=self.supplier,
            destination_location=location,
            notes=f"Notes for {tenant.name}",
            created_by=created_by,
        )

    def _login_to_tenant(self, user, tenant):
        self.client.force_login(user)
        session = self.client.session
        session["active_tenant_id"] = tenant.pk
        session.save()

    def _detail_url(self, purchase_order):
        return reverse(
            "api:procurement_api:purchaseorder-detail",
            kwargs={"pk": purchase_order.pk},
        )

    def _ui_update_payload(self, purchase_order, notes):
        return {
            "order_number": purchase_order.order_number,
            "status": purchase_order.status,
            "supplier": purchase_order.supplier_id,
            "currency": purchase_order.currency,
            "order_date": purchase_order.order_date.isoformat() if purchase_order.order_date else "",
            "expected_delivery_date": (
                purchase_order.expected_delivery_date.isoformat() if purchase_order.expected_delivery_date else ""
            ),
            "destination_location": purchase_order.destination_location_id,
            "tenant": purchase_order.tenant_id,
            "notes": notes,
        }


class PurchaseOrderTenantIsolationAPITests(PurchaseOrderTenantIsolationSetupMixin, APITestCase):
    """Characterize the Stable REST boundary for tenant-owned purchase orders."""

    def test_rest_list_excludes_other_tenant_purchase_order(self):
        self._login_to_tenant(self.writer_a, self.tenant_a)

        response = self.client.get(reverse("api:procurement_api:purchaseorder-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        rows = response.data["results"]
        self.assertEqual({row["id"] for row in rows}, {self.purchase_order_a.pk})

    def test_rest_detail_for_other_tenant_purchase_order_returns_404(self):
        self._login_to_tenant(self.writer_a, self.tenant_a)

        own_response = self.client.get(self._detail_url(self.purchase_order_a))
        foreign_response = self.client.get(self._detail_url(self.purchase_order_b))

        self.assertEqual(own_response.status_code, status.HTTP_200_OK, own_response.content)
        self.assertEqual(foreign_response.status_code, status.HTTP_404_NOT_FOUND, foreign_response.content)

        self._login_to_tenant(self.writer_b, self.tenant_b)
        tenant_b_response = self.client.get(self._detail_url(self.purchase_order_b))
        self.assertEqual(tenant_b_response.status_code, status.HTTP_200_OK, tenant_b_response.content)

    def test_rest_update_for_other_tenant_purchase_order_returns_404_without_mutation(self):
        self._login_to_tenant(self.writer_b, self.tenant_b)
        tenant_b_response = self.client.get(self._detail_url(self.purchase_order_b))
        self.assertEqual(tenant_b_response.status_code, status.HTTP_200_OK, tenant_b_response.content)
        foreign_etag = ETagMixin._get_etag(self.purchase_order_b)

        self._login_to_tenant(self.writer_a, self.tenant_a)
        own_response = self.client.get(self._detail_url(self.purchase_order_a))
        self.assertEqual(own_response.status_code, status.HTTP_200_OK, own_response.content)
        own_update = self.client.patch(
            self._detail_url(self.purchase_order_a),
            {"notes": "Authorized tenant update"},
            format="json",
            HTTP_IF_MATCH=ETagMixin._get_etag(self.purchase_order_a),
        )
        self.assertEqual(own_update.status_code, status.HTTP_200_OK, own_update.content)
        self.purchase_order_a.refresh_from_db()
        self.assertEqual(self.purchase_order_a.notes, "Authorized tenant update")

        self.purchase_order_b.refresh_from_db()
        original_notes = self.purchase_order_b.notes
        original_updated_at = self.purchase_order_b.updated_at
        foreign_update = self.client.patch(
            self._detail_url(self.purchase_order_b),
            {"notes": "Cross-tenant overwrite"},
            format="json",
            HTTP_IF_MATCH=foreign_etag,
        )

        self.assertEqual(foreign_update.status_code, status.HTTP_404_NOT_FOUND, foreign_update.content)
        self.purchase_order_b.refresh_from_db()
        self.assertEqual(self.purchase_order_b.notes, original_notes)
        self.assertEqual(self.purchase_order_b.updated_at, original_updated_at)


class PurchaseOrderTenantIsolationUITests(PurchaseOrderTenantIsolationSetupMixin, TestCase):
    """Characterize the Stable UI boundary for tenant-owned purchase orders."""

    def test_ui_list_excludes_other_tenant_purchase_order(self):
        self._login_to_tenant(self.writer_a, self.tenant_a)

        response = self.client.get(reverse("procurement:purchaseorder_list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertContains(response, self.purchase_order_a.order_number)
        self.assertNotContains(response, self.purchase_order_b.order_number)

    def test_ui_detail_for_other_tenant_purchase_order_returns_404(self):
        self._login_to_tenant(self.writer_a, self.tenant_a)

        own_response = self.client.get(
            reverse("procurement:purchaseorder_detail", kwargs={"pk": self.purchase_order_a.pk})
        )
        foreign_response = self.client.get(
            reverse("procurement:purchaseorder_detail", kwargs={"pk": self.purchase_order_b.pk})
        )

        self.assertEqual(own_response.status_code, status.HTTP_200_OK, own_response.content)
        self.assertContains(own_response, self.purchase_order_a.order_number)
        self.assertEqual(foreign_response.status_code, status.HTTP_404_NOT_FOUND, foreign_response.content)
        self.assertNotContains(
            foreign_response,
            self.purchase_order_b.order_number,
            status_code=status.HTTP_404_NOT_FOUND,
        )

        self._login_to_tenant(self.writer_b, self.tenant_b)
        tenant_b_response = self.client.get(
            reverse("procurement:purchaseorder_detail", kwargs={"pk": self.purchase_order_b.pk})
        )
        self.assertEqual(tenant_b_response.status_code, status.HTTP_200_OK, tenant_b_response.content)
        self.assertContains(tenant_b_response, self.purchase_order_b.order_number)

    def test_ui_update_for_other_tenant_purchase_order_returns_404_without_mutation(self):
        self._login_to_tenant(self.writer_a, self.tenant_a)
        own_response = self.client.post(
            reverse("procurement:purchaseorder_edit", kwargs={"pk": self.purchase_order_a.pk}),
            self._ui_update_payload(self.purchase_order_a, "Authorized tenant UI update"),
        )
        self.assertEqual(own_response.status_code, status.HTTP_302_FOUND, own_response.content)
        self.purchase_order_a.refresh_from_db()
        self.assertEqual(self.purchase_order_a.notes, "Authorized tenant UI update")

        self._login_to_tenant(self.writer_b, self.tenant_b)
        tenant_b_response = self.client.post(
            reverse("procurement:purchaseorder_edit", kwargs={"pk": self.purchase_order_b.pk}),
            self._ui_update_payload(self.purchase_order_b, "Authorized tenant B UI update"),
        )
        self.assertEqual(tenant_b_response.status_code, status.HTTP_302_FOUND, tenant_b_response.content)
        self.purchase_order_b.refresh_from_db()
        self.assertEqual(self.purchase_order_b.notes, "Authorized tenant B UI update")

        original_notes = self.purchase_order_b.notes
        original_updated_at = self.purchase_order_b.updated_at

        self._login_to_tenant(self.writer_a, self.tenant_a)
        foreign_response = self.client.post(
            reverse("procurement:purchaseorder_edit", kwargs={"pk": self.purchase_order_b.pk}),
            self._ui_update_payload(self.purchase_order_b, "Cross-tenant overwrite"),
        )

        self.assertEqual(foreign_response.status_code, status.HTTP_404_NOT_FOUND, foreign_response.content)
        self.purchase_order_b.refresh_from_db()
        self.assertEqual(self.purchase_order_b.notes, original_notes)
        self.assertEqual(self.purchase_order_b.updated_at, original_updated_at)

    def test_ui_edit_cannot_bypass_lifecycle_action_or_creator_approver_separation(self):
        self._login_to_tenant(self.writer_a, self.tenant_a)
        payload = self._ui_update_payload(self.purchase_order_a, "Attempted lifecycle bypass")
        payload["status"] = PurchaseOrder.STATUS_APPROVED

        response = self.client.post(
            reverse("procurement:purchaseorder_edit", kwargs={"pk": self.purchase_order_a.pk}),
            payload,
        )

        self.assertEqual(response.status_code, status.HTTP_302_FOUND, response.content)
        self.purchase_order_a.refresh_from_db()
        self.assertEqual(self.purchase_order_a.status, PurchaseOrder.STATUS_DRAFT)
        self.assertEqual(self.purchase_order_a.notes, "Attempted lifecycle bypass")

    def test_ui_lifecycle_actions_reject_foreign_purchase_order_ids(self):
        self._login_to_tenant(self.writer_a, self.tenant_a)
        original_status = self.purchase_order_b.status

        for route_name in (
            "procurement:purchaseorder_approve",
            "procurement:purchaseorder_order",
            "procurement:purchaseorder_cancel",
            "procurement:purchaseorder_reopen",
            "procurement:purchaseorder_receive",
        ):
            with self.subTest(route_name=route_name):
                response = self.client.post(reverse(route_name, kwargs={"pk": self.purchase_order_b.pk}))
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.content)

        self.purchase_order_b.refresh_from_db()
        self.assertEqual(self.purchase_order_b.status, original_status)


class PurchaseOrderLineTenantIsolationSetupMixin:
    """Provide an authorized purchase-order-line writer in each tenant."""

    def setUp(self):
        super().setUp()
        self.tenant_a = Tenant.objects.create(name="PO Line Tenant A", slug="po-line-security-tenant-a")
        self.tenant_b = Tenant.objects.create(name="PO Line Tenant B", slug="po-line-security-tenant-b")
        self.site = Site.objects.create(name="PO Line Security Site", slug="po-line-security-site")
        self.supplier = Supplier.objects.create(name="PO Line Security Supplier", slug="po-line-security-supplier")
        manufacturer = Manufacturer.objects.create(
            name="PO Line Security Manufacturer",
            slug="po-line-security-manufacturer",
        )
        self.asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="PO Line Security Model",
            slug="po-line-security-model",
        )
        self.writer_a = self._make_writer("po-line-security-writer-a", self.tenant_a)
        self.writer_b = self._make_writer("po-line-security-writer-b", self.tenant_b)
        self.line_a = self._make_line(self.tenant_a, "PO-LINE-SECURITY-TENANT-A", self.writer_a)
        self.line_b = self._make_line(self.tenant_b, "PO-LINE-SECURITY-TENANT-B", self.writer_b)

    def _make_writer(self, username, tenant):
        user = User.objects.create_user(username=username, password="password")
        role = Role.objects.create(
            tenant=tenant,
            name=f"Purchase order line writer {tenant.pk}",
            permissions=[
                "procurement.view_purchaseorder",
                "procurement.change_purchaseorder",
                "procurement.view_purchaseorderline",
                "procurement.change_purchaseorderline",
            ],
        )
        grant(user, tenant, role)
        return user

    def _make_line(self, tenant, order_number, created_by):
        location = Location.objects.create(
            name=f"PO line location for {tenant.name}",
            slug=f"po-line-security-location-{tenant.pk}",
            site=self.site,
            tenant=tenant,
        )
        purchase_order = PurchaseOrder.objects.create(
            tenant=tenant,
            order_number=order_number,
            supplier=self.supplier,
            destination_location=location,
            created_by=created_by,
        )
        return PurchaseOrderLine.objects.create(
            tenant=tenant,
            purchase_order=purchase_order,
            asset_type=self.asset_type,
            qty_ordered=1,
            unit_price=Decimal("10.00"),
        )

    def _login_to_tenant(self, user, tenant):
        self.client.force_login(user)
        session = self.client.session
        session["active_tenant_id"] = tenant.pk
        session.save()

    def _rest_detail_url(self, line):
        return reverse("api:procurement_api:purchaseorderline-detail", kwargs={"pk": line.pk})

    def _ui_edit_url(self, line):
        return reverse("procurement:purchaseorderline_edit", kwargs={"pk": line.pk})


class PurchaseOrderLineTenantIsolationAPITests(PurchaseOrderLineTenantIsolationSetupMixin, APITestCase):
    """Characterize the Stable REST boundary for tenant-owned purchase order lines."""

    def test_rest_list_excludes_other_tenant_purchase_order_line(self):
        self._login_to_tenant(self.writer_b, self.tenant_b)
        tenant_b_response = self.client.get(reverse("api:procurement_api:purchaseorderline-list"))
        self.assertEqual(tenant_b_response.status_code, status.HTTP_200_OK, tenant_b_response.content)
        self.assertEqual({row["id"] for row in tenant_b_response.data["results"]}, {self.line_b.pk})

        self._login_to_tenant(self.writer_a, self.tenant_a)
        response = self.client.get(reverse("api:procurement_api:purchaseorderline-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual({row["id"] for row in response.data["results"]}, {self.line_a.pk})

    def test_rest_detail_for_other_tenant_purchase_order_line_returns_404(self):
        self._login_to_tenant(self.writer_b, self.tenant_b)
        tenant_b_response = self.client.get(self._rest_detail_url(self.line_b))
        self.assertEqual(tenant_b_response.status_code, status.HTTP_200_OK, tenant_b_response.content)

        self._login_to_tenant(self.writer_a, self.tenant_a)
        own_response = self.client.get(self._rest_detail_url(self.line_a))
        foreign_response = self.client.get(self._rest_detail_url(self.line_b))

        self.assertEqual(own_response.status_code, status.HTTP_200_OK, own_response.content)
        self.assertEqual(foreign_response.status_code, status.HTTP_404_NOT_FOUND, foreign_response.content)

    def test_rest_update_for_other_tenant_purchase_order_line_returns_404_without_mutation(self):
        self._login_to_tenant(self.writer_b, self.tenant_b)
        tenant_b_update = self.client.patch(
            self._rest_detail_url(self.line_b),
            {"unit_price": "22.00"},
            format="json",
            HTTP_IF_MATCH=ETagMixin._get_etag(self.line_b),
        )
        self.assertEqual(tenant_b_update.status_code, status.HTTP_200_OK, tenant_b_update.content)
        self.line_b.refresh_from_db()
        self.assertEqual(self.line_b.unit_price, Decimal("22.00"))
        foreign_etag = ETagMixin._get_etag(self.line_b)

        self._login_to_tenant(self.writer_a, self.tenant_a)
        own_update = self.client.patch(
            self._rest_detail_url(self.line_a),
            {"unit_price": "33.00"},
            format="json",
            HTTP_IF_MATCH=ETagMixin._get_etag(self.line_a),
        )
        self.assertEqual(own_update.status_code, status.HTTP_200_OK, own_update.content)
        self.line_a.refresh_from_db()
        self.assertEqual(self.line_a.unit_price, Decimal("33.00"))

        original_unit_price = self.line_b.unit_price
        original_updated_at = self.line_b.updated_at
        foreign_update = self.client.patch(
            self._rest_detail_url(self.line_b),
            {"unit_price": "99.00"},
            format="json",
            HTTP_IF_MATCH=foreign_etag,
        )

        self.assertEqual(foreign_update.status_code, status.HTTP_404_NOT_FOUND, foreign_update.content)
        self.line_b.refresh_from_db()
        self.assertEqual(self.line_b.unit_price, original_unit_price)
        self.assertEqual(self.line_b.updated_at, original_updated_at)


class PurchaseOrderLineTenantIsolationUITests(PurchaseOrderLineTenantIsolationSetupMixin, TestCase):
    """Characterize the parent-detail listing and edit UI; no standalone line detail exists."""

    def test_ui_parent_detail_for_other_tenant_purchase_order_line_returns_404(self):
        tenant_b_url = reverse(
            "procurement:purchaseorder_detail",
            kwargs={"pk": self.line_b.purchase_order_id},
        )
        self._login_to_tenant(self.writer_b, self.tenant_b)
        tenant_b_response = self.client.get(tenant_b_url)
        self.assertEqual(tenant_b_response.status_code, status.HTTP_200_OK, tenant_b_response.content)
        self.assertContains(tenant_b_response, self._ui_edit_url(self.line_b))

        self._login_to_tenant(self.writer_a, self.tenant_a)
        own_response = self.client.get(
            reverse("procurement:purchaseorder_detail", kwargs={"pk": self.line_a.purchase_order_id})
        )
        foreign_response = self.client.get(tenant_b_url)

        self.assertEqual(own_response.status_code, status.HTTP_200_OK, own_response.content)
        self.assertContains(own_response, self._ui_edit_url(self.line_a))
        self.assertEqual(foreign_response.status_code, status.HTTP_404_NOT_FOUND, foreign_response.content)

    def test_ui_edit_read_for_other_tenant_purchase_order_line_returns_404(self):
        self._login_to_tenant(self.writer_b, self.tenant_b)
        tenant_b_response = self.client.get(self._ui_edit_url(self.line_b))
        self.assertEqual(tenant_b_response.status_code, status.HTTP_200_OK, tenant_b_response.content)

        self._login_to_tenant(self.writer_a, self.tenant_a)
        own_response = self.client.get(self._ui_edit_url(self.line_a))
        foreign_response = self.client.get(self._ui_edit_url(self.line_b))

        self.assertEqual(own_response.status_code, status.HTTP_200_OK, own_response.content)
        self.assertEqual(foreign_response.status_code, status.HTTP_404_NOT_FOUND, foreign_response.content)

    def test_ui_update_for_other_tenant_purchase_order_line_returns_404_without_mutation(self):
        self._login_to_tenant(self.writer_a, self.tenant_a)
        own_response = self.client.post(
            self._ui_edit_url(self.line_a),
            {"qty_ordered": 4, "unit_price": "44.00"},
        )
        self.assertEqual(own_response.status_code, status.HTTP_200_OK, own_response.content)
        self.line_a.refresh_from_db()
        self.assertEqual(self.line_a.qty_ordered, 4)
        self.assertEqual(self.line_a.unit_price, Decimal("44.00"))

        self._login_to_tenant(self.writer_b, self.tenant_b)
        tenant_b_response = self.client.post(
            self._ui_edit_url(self.line_b),
            {"qty_ordered": 5, "unit_price": "55.00"},
        )
        self.assertEqual(tenant_b_response.status_code, status.HTTP_200_OK, tenant_b_response.content)
        self.line_b.refresh_from_db()
        self.assertEqual(self.line_b.qty_ordered, 5)
        self.assertEqual(self.line_b.unit_price, Decimal("55.00"))

        original_qty_ordered = self.line_b.qty_ordered
        original_unit_price = self.line_b.unit_price
        original_updated_at = self.line_b.updated_at

        self._login_to_tenant(self.writer_a, self.tenant_a)
        foreign_response = self.client.post(
            self._ui_edit_url(self.line_b),
            {"qty_ordered": 99, "unit_price": "99.00"},
        )

        self.assertEqual(foreign_response.status_code, status.HTTP_404_NOT_FOUND, foreign_response.content)
        self.line_b.refresh_from_db()
        self.assertEqual(self.line_b.qty_ordered, original_qty_ordered)
        self.assertEqual(self.line_b.unit_price, original_unit_price)
        self.assertEqual(self.line_b.updated_at, original_updated_at)


class AssetRequestProcurementPermissionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Seam Tenant", slug="seam-tenant")
        site = Site.objects.create(name="Seam Site", slug="seam-site")
        self.location = Location.objects.create(
            tenant=self.tenant,
            site=site,
            name="Seam Location",
            slug="seam-location",
        )
        self.supplier = Supplier.objects.create(name="Seam Supplier", slug="seam-supplier")
        manufacturer = Manufacturer.objects.create(name="Seam Manufacturer", slug="seam-manufacturer")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Seam Model",
            slug="seam-model",
            requestable=True,
        )
        self.user = User.objects.create_user(username="seam-po-writer", password="password")
        self.admin = User.objects.create_superuser(username="seam-admin", password="password")
        role = Role.objects.create(
            tenant=self.tenant,
            name="PO writer without fulfillment",
            permissions=[
                "procurement.add_purchaseorder",
                "procurement.change_purchaseorder",
                "procurement.view_purchaseorder",
            ],
        )
        grant(self.user, self.tenant, role)
        self.asset_request = AssetRequest.objects.create(
            tenant=self.tenant,
            requester=self.user,
            asset_type=asset_type,
            status=RequestStatusChoices.APPROVED,
        )
        self.create_from_request_url = (
            f"{reverse('procurement:purchaseorder_create')}?from_request={self.asset_request.pk}"
        )

    def _login_to_tenant(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

    def _payload(self, order_number):
        return {
            "order_number": order_number,
            "supplier": self.supplier.pk,
            "currency": "EUR",
            "destination_location": self.location.pk,
            "tenant": self.tenant.pk,
            "notes": "must roll back",
        }

    @override_settings(ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS={"accessory": 3, "consumable": 5})
    def test_po_writer_without_asset_request_fulfillment_permission_cannot_link_request(self):
        self._login_to_tenant(self.user)

        get_response = self.client.get(self.create_from_request_url)
        response = self.client.post(self.create_from_request_url, self._payload("PO-SEAM-DENIED"))

        self.assertEqual(get_response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        emitted_messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertFalse(any("Created" in message for message in emitted_messages))
        self.assertFalse(PurchaseOrder.objects.filter(order_number="PO-SEAM-DENIED").exists())
        self.assertFalse(FulfillmentLink.objects.filter(asset_request=self.asset_request).exists())
        self.asset_request.refresh_from_db()
        self.assertEqual(self.asset_request.status, RequestStatusChoices.APPROVED)

    @override_settings(
        ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS=None,
        REQUISITION_AUTO_APPROVAL_THRESHOLDS=None,
    )
    def test_unconfigured_seam_rolls_back_purchase_order_without_success_message(self):
        self._login_to_tenant(self.admin)

        response = self.client.post(self.create_from_request_url, self._payload("PO-SEAM-INACTIVE"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        emitted_messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertFalse(any("Created" in message for message in emitted_messages))
        self.assertFalse(PurchaseOrder.objects.filter(order_number="PO-SEAM-INACTIVE").exists())
        self.assertFalse(FulfillmentLink.objects.filter(asset_request=self.asset_request).exists())

    @override_settings(ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS={"accessory": 3})
    def test_malformed_asset_request_id_fails_closed(self):
        self._login_to_tenant(self.admin)
        malformed_url = f"{reverse('procurement:purchaseorder_create')}?from_request=not-an-id"

        response = self.client.get(malformed_url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS={"accessory": 3})
    def test_authorized_create_from_request_commits_complete_fulfillment_graph_and_messages(self):
        self._login_to_tenant(self.admin)

        response = self.client.post(self.create_from_request_url, self._payload("PO-SEAM-SUCCESS"))

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        purchase_order = PurchaseOrder.objects.get(order_number="PO-SEAM-SUCCESS")
        link = FulfillmentLink.objects.select_related("purchase_order_line").get(asset_request=self.asset_request)
        self.asset_request.refresh_from_db()
        self.assertEqual(purchase_order.tenant, self.tenant)
        self.assertEqual(link.tenant, self.tenant)
        self.assertEqual(link.purchase_order_line.purchase_order, purchase_order)
        self.assertEqual(link.purchase_order_line.asset_type, self.asset_request.asset_type)
        self.assertEqual(self.asset_request.status, RequestStatusChoices.PROCUREMENT)
        emitted_messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("Created" in message for message in emitted_messages))
        self.assertTrue(any("Linked Purchase Order" in message for message in emitted_messages))

    @override_settings(
        ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS=None,
        REQUISITION_AUTO_APPROVAL_THRESHOLDS=None,
    )
    def test_unconfigured_seam_hides_purchase_order_action(self):
        self._login_to_tenant(self.admin)

        response = self.client.get(reverse("assets:request_detail", kwargs={"pk": self.asset_request.pk}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotContains(response, "Create Purchase Order")

    @override_settings(ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS={"accessory": 3})
    def test_configured_seam_shows_purchase_order_action_to_authorized_user(self):
        self._login_to_tenant(self.admin)

        response = self.client.get(reverse("assets:request_detail", kwargs={"pk": self.asset_request.pk}))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "Create Purchase Order")


class AssetRequestCrossTenantLifecycleUITests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Request Tenant A", slug="request-tenant-a")
        self.tenant_b = Tenant.objects.create(name="Request Tenant B", slug="request-tenant-b")
        requester = User.objects.create_user(username="request-tenant-a-requester", password="password")
        approved_requester = User.objects.create_user(
            username="request-tenant-a-approved-requester",
            password="password",
        )
        self.actor_b = User.objects.create_user(username="request-tenant-b-actor", password="password")
        role_b = Role.objects.create(
            tenant=self.tenant_b,
            name="Request lifecycle actor",
            permissions=[
                "assets.view_assetrequest",
                "assets.approve_assetrequest",
                "assets.fulfill_assetrequest",
            ],
        )
        grant(self.actor_b, self.tenant_b, role_b)
        manufacturer = Manufacturer.objects.create(name="Request Tenant Manufacturer", slug="request-tenant-maker")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Request Tenant Model",
            slug="request-tenant-model",
            requestable=True,
        )
        self.pending_request_a = AssetRequest.objects.create(
            tenant=self.tenant_a,
            requester=requester,
            asset_type=asset_type,
            status=RequestStatusChoices.PENDING,
        )
        self.approved_request_a = AssetRequest.objects.create(
            tenant=self.tenant_a,
            requester=approved_requester,
            asset_type=asset_type,
            status=RequestStatusChoices.APPROVED,
        )
        self.client.force_login(self.actor_b)
        session = self.client.session
        session["active_tenant_id"] = self.tenant_b.pk
        session.save()

    def test_tenant_b_cannot_drive_tenant_a_asset_request_lifecycle_by_foreign_id(self):
        actions = (
            ("assets:request_approve", self.pending_request_a),
            ("assets:request_deny", self.pending_request_a),
            ("assets:request_cancel", self.pending_request_a),
            ("assets:request_claim", self.approved_request_a),
            ("assets:request_mark_fulfilled", self.approved_request_a),
        )

        for route_name, asset_request in actions:
            with self.subTest(route_name=route_name):
                response = self.client.post(reverse(route_name, kwargs={"pk": asset_request.pk}))
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.content)

        self.pending_request_a.refresh_from_db()
        self.approved_request_a.refresh_from_db()
        self.assertEqual(self.pending_request_a.status, RequestStatusChoices.PENDING)
        self.assertEqual(self.approved_request_a.status, RequestStatusChoices.APPROVED)
