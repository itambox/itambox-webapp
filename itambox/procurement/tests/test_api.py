from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APITestCase

from assets.models import Asset, AssetType, Manufacturer, StatusLabel, Supplier
from core.tests.mixins import grant
from inventory.models import AccessoryStock, ComponentStock, ConsumableStock
from itambox.api.mixins import ETagMixin
from organization.models import Location, Role, Site, Tenant
from procurement.api.serializers import PurchaseOrderReceiveSerializer
from procurement.models import PurchaseOrder, PurchaseOrderLine

User = get_user_model()


class PurchaseOrderReceiveSerializerTests(SimpleTestCase):
    def test_line_quantities_fail_closed_without_purchase_order_context(self):
        serializer = PurchaseOrderReceiveSerializer(data={"line_quantities": {"1": 1}})

        self.assertFalse(serializer.is_valid())
        self.assertIn("purchase order context", str(serializer.errors["line_quantities"]).lower())


class PurchaseOrderActionAPITests(APITestCase):
    def setUp(self):
        self.creator = User.objects.create_superuser(username="po-creator", password="password")
        self.approver = User.objects.create_superuser(username="po-approver", password="password")
        self.tenant = Tenant.objects.create(name="PO API Tenant", slug="po-api-tenant")
        self.site = Site.objects.create(name="PO API Site", slug="po-api-site")
        self.location = Location.objects.create(
            name="PO API Location",
            slug="po-api-location",
            site=self.site,
            tenant=self.tenant,
        )
        self.supplier = Supplier.objects.create(name="PO API Supplier", slug="po-api-supplier")
        self.manufacturer = Manufacturer.objects.create(name="PO API Manufacturer", slug="po-api-manufacturer")
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="PO API Model",
            slug="po-api-model",
        )
        self.deployable_status = StatusLabel.objects.create(
            name="PO API Deployable",
            slug="po-api-deployable",
            type=StatusLabel.TYPE_DEPLOYABLE,
        )
        self.purchase_order = PurchaseOrder.objects.create(
            tenant=self.tenant,
            order_number="PO-API-001",
            supplier=self.supplier,
            destination_location=self.location,
            created_by=self.creator,
        )
        self.line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=self.purchase_order,
            asset_type=self.asset_type,
            qty_ordered=1,
        )

    def _login_to_tenant(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant.pk
        session.save()

    def _user_with_permissions(self, username, permissions):
        user = User.objects.create_user(username=username, password="password")
        grant(
            user,
            self.tenant,
            Role.objects.create(
                tenant=self.tenant,
                name=f"Role for {username}",
                permissions=permissions,
            ),
        )
        return user

    def test_approve_action_uses_purchase_order_service(self):
        self.client.force_authenticate(user=self.approver)

        response = self.client.post(
            f"/api/procurement/purchase-orders/{self.purchase_order.pk}/approve/",
            data={},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_APPROVED)
        self.assertIn("approved", str(response.data["message"]).lower())

    def test_approve_action_returns_400_for_service_validation_error(self):
        self.client.force_authenticate(user=self.creator)
        self.client.raise_request_exception = False

        response = self.client.post(
            f"/api/procurement/purchase-orders/{self.purchase_order.pk}/approve/",
            data={},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn("cannot be approved", response.content.decode().lower())
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_DRAFT)

    def test_approve_action_requires_custom_permission_not_add_permission(self):
        add_only = self._user_with_permissions(
            "po-add-only",
            ["procurement.view_purchaseorder", "procurement.add_purchaseorder"],
        )
        approve_only = self._user_with_permissions(
            "po-approve-only",
            ["procurement.view_purchaseorder", "procurement.approve_purchaseorder"],
        )
        url = f"/api/procurement/purchase-orders/{self.purchase_order.pk}/approve/"

        self._login_to_tenant(add_only)
        response = self.client.post(url, data={}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.content)
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_DRAFT)

        self.client.logout()
        self._login_to_tenant(approve_only)
        response = self.client.post(url, data={}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_APPROVED)

    def test_direct_status_change_is_rejected_with_approve_action_hint(self):
        self.client.force_authenticate(user=self.approver)
        detail_url = f"/api/procurement/purchase-orders/{self.purchase_order.pk}/"
        etag = ETagMixin._get_etag(self.purchase_order)

        response = self.client.patch(
            detail_url,
            data={"status": PurchaseOrder.STATUS_APPROVED},
            format="json",
            HTTP_IF_MATCH=etag,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn("/approve/", response.content.decode())
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_DRAFT)

    def test_direct_status_change_names_the_sanctioned_action(self):
        self.client.force_authenticate(user=self.approver)
        detail_url = f"/api/procurement/purchase-orders/{self.purchase_order.pk}/"
        cases = (
            (PurchaseOrder.STATUS_APPROVED, "/approve/"),
            (PurchaseOrder.STATUS_ORDERED, "/order/"),
            (PurchaseOrder.STATUS_PARTIAL, "/receive/"),
            (PurchaseOrder.STATUS_RECEIVED, "/receive/"),
            (PurchaseOrder.STATUS_CANCELLED, "/cancel/"),
        )

        for requested_status, expected_action in cases:
            with self.subTest(status=requested_status):
                self.purchase_order.refresh_from_db()
                response = self.client.patch(
                    detail_url,
                    data={"status": requested_status},
                    format="json",
                    HTTP_IF_MATCH=ETagMixin._get_etag(self.purchase_order),
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
                self.assertIn(expected_action, response.content.decode())
                self.purchase_order.refresh_from_db()
                self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_DRAFT)

        self.purchase_order.status = PurchaseOrder.STATUS_CANCELLED
        self.purchase_order.save(update_fields=["status"])
        response = self.client.patch(
            detail_url,
            data={"status": PurchaseOrder.STATUS_DRAFT},
            format="json",
            HTTP_IF_MATCH=ETagMixin._get_etag(self.purchase_order),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn("/reopen/", response.content.decode())
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_CANCELLED)

    def test_direct_status_change_to_unrecognised_value_is_rejected_not_a_server_error(self):
        self.client.force_authenticate(user=self.approver)
        self.client.raise_request_exception = False
        detail_url = f"/api/procurement/purchase-orders/{self.purchase_order.pk}/"

        response = self.client.patch(
            detail_url,
            data={"status": "not-a-real-status"},
            format="json",
            HTTP_IF_MATCH=ETagMixin._get_etag(self.purchase_order),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn("lifecycle action", response.content.decode())
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_DRAFT)

    def test_create_rejects_non_default_status_with_approve_action_hint(self):
        self.client.force_authenticate(user=self.approver)
        initial_count = PurchaseOrder.objects.count()

        response = self.client.post(
            "/api/procurement/purchase-orders/",
            data={
                "order_number": "PO-API-DIRECT-STATUS",
                "status": PurchaseOrder.STATUS_APPROVED,
                "supplier_id": self.supplier.pk,
                "destination_location_id": self.location.pk,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn("/approve/", response.content.decode())
        self.assertEqual(PurchaseOrder.objects.count(), initial_count)

    def test_create_accepts_and_ignores_default_status(self):
        self.client.force_authenticate(user=self.approver)

        response = self.client.post(
            "/api/procurement/purchase-orders/",
            data={
                "order_number": "PO-API-DEFAULT-STATUS",
                "status": PurchaseOrder.STATUS_DRAFT,
                "supplier_id": self.supplier.pk,
                "destination_location_id": self.location.pk,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        created = PurchaseOrder.objects.get(order_number="PO-API-DEFAULT-STATUS")
        self.assertEqual(created.status, PurchaseOrder.STATUS_DRAFT)

    def test_identical_status_is_accepted_and_ignored_for_patch_and_put(self):
        self.client.force_authenticate(user=self.approver)
        detail_url = f"/api/procurement/purchase-orders/{self.purchase_order.pk}/"
        requests = (
            (self.client.patch, {"status": PurchaseOrder.STATUS_DRAFT}),
            (
                self.client.put,
                {
                    "order_number": self.purchase_order.order_number,
                    "status": PurchaseOrder.STATUS_DRAFT,
                    "supplier_id": self.supplier.pk,
                    "destination_location_id": self.location.pk,
                },
            ),
        )

        for request_method, payload in requests:
            with self.subTest(method=request_method.__name__):
                self.purchase_order.refresh_from_db()
                response = request_method(
                    detail_url,
                    data=payload,
                    format="json",
                    HTTP_IF_MATCH=ETagMixin._get_etag(self.purchase_order),
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
                self.purchase_order.refresh_from_db()
                self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_DRAFT)

    def test_create_rejects_non_default_qty_received_with_receive_action_hint(self):
        self.client.force_authenticate(user=self.approver)
        initial_count = PurchaseOrderLine.objects.count()

        response = self.client.post(
            "/api/procurement/purchase-order-lines/",
            data={
                "purchase_order_id": self.purchase_order.pk,
                "asset_type_id": self.asset_type.pk,
                "qty_ordered": 1,
                "qty_received": 1,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn("/receive/", response.content.decode())
        self.assertEqual(PurchaseOrderLine.objects.count(), initial_count)

    def test_create_accepts_and_ignores_default_qty_received(self):
        self.client.force_authenticate(user=self.approver)

        response = self.client.post(
            "/api/procurement/purchase-order-lines/",
            data={
                "purchase_order_id": self.purchase_order.pk,
                "asset_type_id": self.asset_type.pk,
                "qty_ordered": 1,
                "qty_received": 0,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.content)
        created = PurchaseOrderLine.objects.exclude(pk=self.line.pk).get()
        self.assertEqual(created.qty_received, 0)

    def test_identical_qty_received_is_accepted_and_ignored_for_patch_and_put(self):
        self.client.force_authenticate(user=self.approver)
        detail_url = f"/api/procurement/purchase-order-lines/{self.line.pk}/"
        requests = (
            (self.client.patch, {"qty_received": 0}),
            (
                self.client.put,
                {
                    "purchase_order_id": self.purchase_order.pk,
                    "asset_type_id": self.asset_type.pk,
                    "qty_ordered": self.line.qty_ordered,
                    "qty_received": 0,
                },
            ),
        )

        for request_method, payload in requests:
            with self.subTest(method=request_method.__name__):
                self.line.refresh_from_db()
                response = request_method(
                    detail_url,
                    data=payload,
                    format="json",
                    HTTP_IF_MATCH=ETagMixin._get_etag(self.line),
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
                self.line.refresh_from_db()
                self.purchase_order.refresh_from_db()
                self.assertEqual(self.line.qty_received, 0)
                self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_DRAFT)

    def test_order_action_uses_purchase_order_service(self):
        self.purchase_order.status = PurchaseOrder.STATUS_APPROVED
        self.purchase_order.save(update_fields=["status"])
        self.client.force_authenticate(user=self.approver)

        response = self.client.post(
            f"/api/procurement/purchase-orders/{self.purchase_order.pk}/order/",
            data={},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_ORDERED)
        self.assertIn("ordered", str(response.data["message"]).lower())

    def test_order_action_requires_change_not_add_permission(self):
        self.purchase_order.status = PurchaseOrder.STATUS_APPROVED
        self.purchase_order.save(update_fields=["status"])
        add_only = self._user_with_permissions(
            "po-order-add-only",
            ["procurement.view_purchaseorder", "procurement.add_purchaseorder"],
        )
        change_only = self._user_with_permissions(
            "po-order-change-only",
            ["procurement.view_purchaseorder", "procurement.change_purchaseorder"],
        )
        url = f"/api/procurement/purchase-orders/{self.purchase_order.pk}/order/"

        self._login_to_tenant(add_only)
        response = self.client.post(url, data={}, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.content)
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_APPROVED)

        self.client.logout()
        self._login_to_tenant(change_only)
        response = self.client.post(url, data={}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_ORDERED)

    def test_receive_action_uses_purchase_order_service(self):
        self.purchase_order.status = PurchaseOrder.STATUS_ORDERED
        self.purchase_order.save(update_fields=["status"])
        self.client.force_authenticate(user=self.approver)

        response = self.client.post(
            f"/api/procurement/purchase-orders/{self.purchase_order.pk}/receive/",
            data={"line_quantities": {str(self.line.pk): 1}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.purchase_order.refresh_from_db()
        self.line.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_RECEIVED)
        self.assertEqual(self.line.qty_received, 1)
        self.assertIn("received", str(response.data["message"]).lower())

    def test_completed_receive_replay_is_rejected_without_duplicate_materialization(self):
        """A stale retry after full receipt is inert; partial receipt replay needs a durable key owned by WP-5."""
        self.purchase_order.status = PurchaseOrder.STATUS_ORDERED
        self.purchase_order.save(update_fields=["status"])
        self.client.force_authenticate(user=self.approver)
        url = f"/api/procurement/purchase-orders/{self.purchase_order.pk}/receive/"
        payload = {"line_quantities": {str(self.line.pk): 1}}

        first_response = self.client.post(url, data=payload, format="json")
        self.assertEqual(first_response.status_code, status.HTTP_200_OK, first_response.content)
        materialized_asset_ids = list(Asset.objects.filter(purchase_order_line=self.line).values_list("pk", flat=True))
        self.assertEqual(len(materialized_asset_ids), 1)

        replay_response = self.client.post(url, data=payload, format="json")

        self.assertEqual(replay_response.status_code, status.HTTP_400_BAD_REQUEST, replay_response.content)
        self.purchase_order.refresh_from_db()
        self.line.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_RECEIVED)
        self.assertEqual(self.line.qty_received, self.line.qty_ordered)
        self.assertEqual(
            list(Asset.objects.filter(purchase_order_line=self.line).values_list("pk", flat=True)),
            materialized_asset_ids,
        )

    def test_receive_action_rejects_requests_without_positive_quantities(self):
        self.purchase_order.status = PurchaseOrder.STATUS_ORDERED
        self.purchase_order.save(update_fields=["status"])
        self.client.force_authenticate(user=self.approver)
        url = f"/api/procurement/purchase-orders/{self.purchase_order.pk}/receive/"

        for line_quantities in ({}, {str(self.line.pk): 0}):
            with self.subTest(line_quantities=line_quantities):
                response = self.client.post(
                    url,
                    data={"line_quantities": line_quantities},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
                self.assertIn("positive", response.content.decode().lower())
                self.purchase_order.refresh_from_db()
                self.line.refresh_from_db()
                self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_ORDERED)
                self.assertEqual(self.line.qty_received, 0)

    def test_receive_action_rejects_line_from_another_purchase_order(self):
        self.purchase_order.status = PurchaseOrder.STATUS_ORDERED
        self.purchase_order.save(update_fields=["status"])
        other_purchase_order = PurchaseOrder.objects.create(
            tenant=self.tenant,
            order_number="PO-API-002",
            supplier=self.supplier,
            destination_location=self.location,
            created_by=self.creator,
        )
        foreign_line = PurchaseOrderLine.objects.create(
            tenant=self.tenant,
            purchase_order=other_purchase_order,
            asset_type=self.asset_type,
            qty_ordered=1,
        )
        self.client.force_authenticate(user=self.approver)

        response = self.client.post(
            f"/api/procurement/purchase-orders/{self.purchase_order.pk}/receive/",
            data={"line_quantities": {str(foreign_line.pk): 1}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn("line_quantities", response.content.decode())
        self.purchase_order.refresh_from_db()
        self.line.refresh_from_db()
        foreign_line.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_ORDERED)
        self.assertEqual(self.line.qty_received, 0)
        self.assertEqual(foreign_line.qty_received, 0)

    def test_receive_action_rejects_ambiguous_line_id_spellings(self):
        self.purchase_order.status = PurchaseOrder.STATUS_ORDERED
        self.purchase_order.save(update_fields=["status"])
        self.client.force_authenticate(user=self.approver)
        url = f"/api/procurement/purchase-orders/{self.purchase_order.pk}/receive/"

        canonical = str(self.line.pk)
        aliases = [
            f" {canonical}",
            f"+{canonical}",
            f"0{canonical}",
            canonical.translate(str.maketrans("0123456789", "０１２３４５６７８９")),
        ]
        if len(canonical) > 1:
            aliases.append(f"{canonical[:-1]}_{canonical[-1]}")
        for alias in aliases:
            with self.subTest(alias=alias):
                response = self.client.post(
                    url,
                    data={"line_quantities": {alias: 1}},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
                self.line.refresh_from_db()
                self.assertEqual(self.line.qty_received, 0)

    def test_receive_action_ignores_sparse_field_query_parameters(self):
        self.purchase_order.status = PurchaseOrder.STATUS_ORDERED
        self.purchase_order.save(update_fields=["status"])
        self.client.force_authenticate(user=self.approver)

        response = self.client.post(
            f"/api/procurement/purchase-orders/{self.purchase_order.pk}/receive/?fields=id&omit=url",
            data={"line_quantities": {str(self.line.pk): 1}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.line.refresh_from_db()
        self.assertEqual(self.line.qty_received, 1)

    def test_tenant_b_cannot_invoke_actions_for_tenant_a_purchase_order(self):
        other_tenant = Tenant.objects.create(name="Other PO API Tenant", slug="other-po-api-tenant")
        other_user = User.objects.create_user(username="other-po-user", password="password")
        grant(
            other_user,
            other_tenant,
            Role.objects.create(
                tenant=other_tenant,
                name="Other tenant purchase order operator",
                permissions=[
                    "procurement.view_purchaseorder",
                    "procurement.change_purchaseorder",
                    "procurement.approve_purchaseorder",
                    "procurement.receive_purchaseorder",
                ],
            ),
        )
        self.client.force_login(other_user)
        session = self.client.session
        session["active_tenant_id"] = other_tenant.pk
        session.save()

        initial_counts = {
            "assets": Asset.objects.count(),
            "accessory_stock": AccessoryStock.objects.count(),
            "component_stock": ComponentStock.objects.count(),
            "consumable_stock": ConsumableStock.objects.count(),
        }
        cases = (
            ("approve", PurchaseOrder.STATUS_DRAFT, {}),
            ("order", PurchaseOrder.STATUS_APPROVED, {}),
            ("receive", PurchaseOrder.STATUS_ORDERED, {"line_quantities": {str(self.line.pk): 1}}),
            ("cancel", PurchaseOrder.STATUS_DRAFT, {}),
            ("reopen", PurchaseOrder.STATUS_CANCELLED, {}),
        )

        for action_name, initial_status, payload in cases:
            with self.subTest(action=action_name):
                self.purchase_order.status = initial_status
                self.purchase_order.save(update_fields=["status"])
                self.line.qty_received = 0
                self.line.save(update_fields=["qty_received"])

                response = self.client.post(
                    f"/api/procurement/purchase-orders/{self.purchase_order.pk}/{action_name}/",
                    data=payload,
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND, response.content)
                self.purchase_order.refresh_from_db()
                self.line.refresh_from_db()
                self.assertEqual(self.purchase_order.status, initial_status)
                self.assertEqual(self.line.qty_received, 0)
                self.assertEqual(Asset.objects.count(), initial_counts["assets"])
                self.assertEqual(AccessoryStock.objects.count(), initial_counts["accessory_stock"])
                self.assertEqual(ComponentStock.objects.count(), initial_counts["component_stock"])
                self.assertEqual(ConsumableStock.objects.count(), initial_counts["consumable_stock"])

    def test_receive_action_requires_custom_permission_not_add_permission(self):
        self.purchase_order.status = PurchaseOrder.STATUS_ORDERED
        self.purchase_order.save(update_fields=["status"])
        add_only = self._user_with_permissions(
            "po-receive-add-only",
            ["procurement.view_purchaseorder", "procurement.add_purchaseorder"],
        )
        receive_only = self._user_with_permissions(
            "po-receive-only",
            ["procurement.view_purchaseorder", "procurement.receive_purchaseorder"],
        )
        url = f"/api/procurement/purchase-orders/{self.purchase_order.pk}/receive/"
        payload = {"line_quantities": {str(self.line.pk): 1}}

        self._login_to_tenant(add_only)
        response = self.client.post(url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.content)
        self.purchase_order.refresh_from_db()
        self.line.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_ORDERED)
        self.assertEqual(self.line.qty_received, 0)

        self.client.logout()
        self._login_to_tenant(receive_only)
        response = self.client.post(url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.purchase_order.refresh_from_db()
        self.line.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_RECEIVED)
        self.assertEqual(self.line.qty_received, 1)

    def test_direct_qty_received_change_is_rejected_with_receive_action_hint(self):
        self.client.force_authenticate(user=self.approver)
        detail_url = f"/api/procurement/purchase-order-lines/{self.line.pk}/"

        response = self.client.patch(
            detail_url,
            data={"qty_received": 1},
            format="json",
            HTTP_IF_MATCH=ETagMixin._get_etag(self.line),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertIn("/receive/", response.content.decode())
        self.line.refresh_from_db()
        self.assertEqual(self.line.qty_received, 0)

    def test_cancel_action_uses_purchase_order_service(self):
        self.client.force_authenticate(user=self.approver)

        response = self.client.post(
            f"/api/procurement/purchase-orders/{self.purchase_order.pk}/cancel/",
            data={},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_CANCELLED)
        self.assertIn("cancelled", str(response.data["message"]).lower())

    def test_reopen_action_uses_purchase_order_service(self):
        self.purchase_order.status = PurchaseOrder.STATUS_CANCELLED
        self.purchase_order.save(update_fields=["status"])
        self.client.force_authenticate(user=self.approver)

        response = self.client.post(
            f"/api/procurement/purchase-orders/{self.purchase_order.pk}/reopen/",
            data={},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.purchase_order.refresh_from_db()
        self.assertEqual(self.purchase_order.status, PurchaseOrder.STATUS_DRAFT)
        self.assertIn("reopened", str(response.data["message"]).lower())

    def test_cancel_and_reopen_require_change_not_add_permission(self):
        add_only = self._user_with_permissions(
            "po-lifecycle-add-only",
            ["procurement.view_purchaseorder", "procurement.add_purchaseorder"],
        )
        change_only = self._user_with_permissions(
            "po-lifecycle-change-only",
            ["procurement.view_purchaseorder", "procurement.change_purchaseorder"],
        )
        cases = (
            ("cancel", PurchaseOrder.STATUS_DRAFT, PurchaseOrder.STATUS_CANCELLED),
            ("reopen", PurchaseOrder.STATUS_CANCELLED, PurchaseOrder.STATUS_DRAFT),
        )

        for action_name, initial_status, final_status in cases:
            with self.subTest(action=action_name):
                self.purchase_order.status = initial_status
                self.purchase_order.save(update_fields=["status"])
                url = f"/api/procurement/purchase-orders/{self.purchase_order.pk}/{action_name}/"

                self._login_to_tenant(add_only)
                response = self.client.post(url, data={}, format="json")
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN, response.content)
                self.purchase_order.refresh_from_db()
                self.assertEqual(self.purchase_order.status, initial_status)

                self.client.logout()
                self._login_to_tenant(change_only)
                response = self.client.post(url, data={}, format="json")
                self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
                self.purchase_order.refresh_from_db()
                self.assertEqual(self.purchase_order.status, final_status)
                self.client.logout()


class PurchaseOrderActionSchemaContractTests(SimpleTestCase):
    """
    The generated OpenAPI contract must describe the lifecycle actions as they behave.

    Without explicit annotations drf-spectacular infers the viewset's own
    `serializer_class` for every extra action, so the published contract claims a
    required `PurchaseOrder` request body and a `PurchaseOrder` response. All five
    actions in fact return `{"message": ...}`, and only `/receive/` accepts a body.
    """

    BODYLESS_ACTIONS = ("approve", "order", "cancel", "reopen")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # inline import: cycle: drf_spectacular.generators -> rest_framework.views <-> itambox.api at collection time
        from drf_spectacular.generators import SchemaGenerator

        cls.schema = SchemaGenerator().get_schema(request=None, public=True)

    def _operation(self, action_name):
        path = f"/api/procurement/purchase-orders/{{id}}/{action_name}/"
        self.assertIn(path, self.schema["paths"])
        return self.schema["paths"][path]["post"]

    def _resolve(self, node):
        ref = node.get("$ref")
        if ref is None:
            return node
        return self.schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]

    def test_action_contract_matches_action_behaviour(self):
        for action_name in self.BODYLESS_ACTIONS:
            with self.subTest(action=action_name, contract="request"):
                self.assertNotIn("requestBody", self._operation(action_name))

        with self.subTest(action="receive", contract="request"):
            body = self._operation("receive")["requestBody"]["content"]["application/json"]["schema"]
            self.assertEqual(body.get("$ref"), "#/components/schemas/PurchaseOrderReceiveRequest")

        for action_name in (*self.BODYLESS_ACTIONS, "receive"):
            with self.subTest(action=action_name, contract="response"):
                operation = self._operation(action_name)
                response = self._resolve(operation["responses"]["200"]["content"]["application/json"]["schema"])
                self.assertEqual(response.get("type"), "object")
                self.assertEqual(response.get("properties", {}).get("message", {}).get("type"), "string")

        for action_name in (*self.BODYLESS_ACTIONS, "receive"):
            with self.subTest(action=action_name, contract="maturity"):
                operation = self._operation(action_name)
                self.assertEqual(operation.get("x-itambox-maturity"), "stable")

            with self.subTest(action=action_name, contract="description"):
                description = self._operation(action_name).get("description", "").lower()
                self.assertIn(action_name, description)
                self.assertNotIn("crud api", description)
