from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assets.choices import RequestStatusChoices
from assets.models import AssetRequest
from assets.services.request_authorization import can_asset_request_action
from assets.tests import test_requests
from core.tests.mixins import TenantTestMixin
from organization.models import Role, Tenant, TenantGroup

User = get_user_model()


class RequestActionScopeTests(TenantTestMixin, TestCase):
    def setUp(self):
        test_requests.RequisitionSystemTestCase.setUp(self)
        self.group = TenantGroup.objects.create(name="Request Scope Group", slug="request-scope-group")
        self.tenant.group = self.group
        self.tenant.save(update_fields=["group"])
        self.tenant_b = Tenant.objects.create(
            name="Request Scope Tenant B",
            slug="request-scope-tenant-b",
            group=self.group,
        )
        self.tenant_c = Tenant.objects.create(
            name="Request Scope Tenant C",
            slug="request-scope-tenant-c",
            group=self.group,
        )

    def make_request(self, tenant, notes, status=RequestStatusChoices.PENDING):
        request_obj = AssetRequest(
            requester=self.requester_user,
            tenant=tenant,
            asset_type=self.type_requestable,
            notes=notes,
            status=status,
        )
        request_obj._skip_duplicate_check = True
        request_obj.save()
        return request_obj

    def grant_permissions(self, user, tenant, name, permissions):
        role = Role.objects.create(tenant=tenant, name=name, permissions=permissions)
        self.grant(user, tenant, role)

    def login_to_group(self, user, group):
        self.client.force_login(user)
        session = self.client.session
        session.pop("active_tenant_id", None)
        session.pop("active_all_accessible", None)
        session["active_tenant_group_id"] = group.pk
        session.save()

    def login_to_all_accessible(self, user):
        self.client.force_login(user)
        session = self.client.session
        session.pop("active_tenant_id", None)
        session.pop("active_tenant_group_id", None)
        session["active_all_accessible"] = True
        session.save()

    def test_group_scope_approver_is_bound_to_each_request_tenant(self):
        approver = User.objects.create_user(username="group_request_approver", password="pw")
        self.assertFalse(approver.is_staff)
        self.grant_permissions(
            approver,
            self.tenant,
            "Request Scope Approver",
            ["assets.approve_assetrequest"],
        )
        # Keep tenant B in the actor's accessible group without granting approval there.
        self.grant_permissions(
            approver,
            self.tenant_b,
            "Request Scope Viewer",
            ["assets.view_assetrequest"],
        )
        request_a = self.make_request(self.tenant, "Tenant A request")
        request_b = self.make_request(self.tenant_b, "Tenant B request")

        self.assertTrue(can_asset_request_action(approver, request_a, "approve"))
        self.assertFalse(can_asset_request_action(approver, request_b, "approve"))
        self.assertTrue(can_asset_request_action(approver, request_a, "cancel"))
        self.assertFalse(can_asset_request_action(approver, request_b, "cancel"))

        self.login_to_group(approver, self.group)
        response_a = self.client.post(
            reverse("assets:request_cancel", kwargs={"pk": request_a.pk}),
        )
        self.assertEqual(response_a.status_code, 302)
        request_a.refresh_from_db()
        self.assertEqual(request_a.status, RequestStatusChoices.CANCELLED)

        before_b = (
            request_b.status,
            request_b.response_date,
            request_b.responded_by_id,
            request_b.response_notes,
        )
        response_b = self.client.post(
            reverse("assets:request_cancel", kwargs={"pk": request_b.pk}),
        )
        self.assertIn(response_b.status_code, (403, 404))
        request_b.refresh_from_db()
        self.assertEqual(
            (
                request_b.status,
                request_b.response_date,
                request_b.responded_by_id,
                request_b.response_notes,
            ),
            before_b,
        )

        request_c = self.make_request(self.tenant_c, "Tenant C request with no user grant")
        before_c = (
            request_c.status,
            request_c.response_date,
            request_c.responded_by_id,
            request_c.response_notes,
        )
        self.assertFalse(can_asset_request_action(approver, request_c, "cancel"))
        response_c = self.client.post(reverse("assets:request_cancel", kwargs={"pk": request_c.pk}))
        self.assertIn(response_c.status_code, (403, 404))
        request_c.refresh_from_db()
        self.assertEqual(
            (
                request_c.status,
                request_c.response_date,
                request_c.responded_by_id,
                request_c.response_notes,
            ),
            before_c,
        )

    def test_all_accessible_scope_fulfiller_can_manually_complete_target_request(self):
        fulfiller = User.objects.create_user(username="aggregate_request_fulfiller", password="pw")
        self.assertFalse(fulfiller.is_staff)
        self.grant_permissions(
            fulfiller,
            self.tenant,
            "Aggregate Request Fulfiller",
            ["assets.fulfill_assetrequest"],
        )
        request_obj = self.make_request(
            self.tenant,
            "All-accessible request",
            status=RequestStatusChoices.APPROVED,
        )

        self.login_to_all_accessible(fulfiller)
        response = self.client.post(
            reverse("assets:request_mark_fulfilled", kwargs={"pk": request_obj.pk}),
            {"reason": "Completed in the target tenant", "confirmed_no_handover": "on"},
        )

        self.assertEqual(response.status_code, 302)
        persisted_request = AssetRequest._base_manager.get(pk=request_obj.pk)
        self.assertEqual(persisted_request.status, RequestStatusChoices.FULFILLED)

    def test_scoped_approver_can_approve_then_manually_complete_request(self):
        approver = User.objects.create_user(username="workflow_request_approver", password="pw")
        self.assertFalse(approver.is_staff)
        self.grant_permissions(
            approver,
            self.tenant,
            "Request Workflow Manager",
            ["assets.approve_assetrequest", "assets.fulfill_assetrequest"],
        )
        request_obj = self.make_request(self.tenant, "Scoped approval and completion")
        self.client_login_to_tenant(approver, self.tenant)

        approval_response = self.client.post(
            reverse("assets:request_approve", kwargs={"pk": request_obj.pk}),
            {"response_notes": "Approved by scoped operator"},
        )
        self.assertEqual(approval_response.status_code, 302)
        persisted_request = AssetRequest._base_manager.get(pk=request_obj.pk)
        self.assertEqual(persisted_request.status, RequestStatusChoices.APPROVED)
        self.assertEqual(persisted_request.responded_by_id, approver.pk)

        completion_response = self.client.post(
            reverse("assets:request_mark_fulfilled", kwargs={"pk": request_obj.pk}),
            {"reason": "Completed by scoped operator", "confirmed_no_handover": "on"},
        )
        self.assertEqual(completion_response.status_code, 302)
        persisted_request = AssetRequest._base_manager.get(pk=request_obj.pk)
        self.assertEqual(persisted_request.status, RequestStatusChoices.FULFILLED)
