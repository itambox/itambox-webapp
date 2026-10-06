"""Request action buttons follow the target request's scoped permissions."""

from django.contrib.auth import get_user_model
from django.template import Context
from django.template.loader import get_template
from django.template.loader_tags import BlockNode
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils.translation import override

from assets.choices import RequestStatusChoices
from assets.models import AssetRequest, AssetRole, AssetType, Manufacturer
from assets.services.request_authorization import can_asset_request_action
from core.tests.mixins import TenantTestMixin
from organization.models import AssetHolder, Role

User = get_user_model()


def render_detail_block(name, user, **values):
    template = get_template("assets/requests/assetrequest_detail.html").template
    block = next(node for node in template.nodelist.get_nodes_by_type(BlockNode) if node.name == name)
    request = RequestFactory().get("/assets/requests/")
    request.user = user
    context = Context({"request": request, **values})
    with override("en"), context.bind_template(template):
        return block.render(context)


class RequestActionVisibilityTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(
            name="Issue 497 Tenant",
            slug="issue-497-tenant",
            permissions=["assets.view_assetrequest"],
        )
        self.set_active_tenant(self.tenant, self.tenant_membership)

        self.actors = {
            "staff-only": self.create_actor("staff_only", is_staff=True),
            "approve-only": self.create_actor("approve_only", "assets.approve_assetrequest"),
            "fulfill-only": self.create_actor("fulfill_only", "assets.fulfill_assetrequest"),
            "both": self.create_actor(
                "approve_and_fulfill", "assets.approve_assetrequest", "assets.fulfill_assetrequest"
            ),
            "neither": self.create_actor("neither"),
            "requester": self.tenant_user,
            "assigned user": self.create_actor("assigned_user"),
            "unrelated": self.create_actor("unrelated"),
        }

        self.assigned_holder = AssetHolder.objects.create(
            user=self.actors["assigned user"],
            first_name="Assigned",
            last_name="User",
            upn="assigned-issue-497@example.com",
            tenant=self.tenant,
        )
        manufacturer = Manufacturer.objects.create(name="Issue 497 Manufacturer", slug="issue-497-manufacturer")
        asset_role = AssetRole.objects.create(name="Issue 497 Laptop", slug="issue-497-laptop")
        asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="Issue 497 Laptop",
            slug="issue-497-laptop-type",
            requestable=True,
            asset_role=asset_role,
        )
        self.asset_request = AssetRequest.objects.create(
            requester=self.actors["requester"],
            asset_type=asset_type,
            assigned_user=self.assigned_holder,
            status=RequestStatusChoices.PENDING,
            tenant=self.tenant,
        )

    def create_actor(self, username, *action_permissions, is_staff=False):
        user = User.objects.create_user(
            username=f"issue497_{username}",
            email=f"issue497_{username}@example.com",
            password="password",
            is_staff=is_staff,
        )
        role = Role.objects.create(
            tenant=self.tenant,
            name=f"Issue 497 {username} role",
            permissions=["assets.view_assetrequest", *action_permissions],
        )
        self.grant(user, self.tenant, role)
        return user

    def render_actions(self, user, status):
        self.asset_request.status = status
        return render_detail_block("page_actions", user, object=self.asset_request)

    def assert_action_visibility(self, html, url_name, label, visible):
        action_url = reverse(url_name, kwargs={"pk": self.asset_request.pk})
        if visible:
            self.assertIn(label, html)
            self.assertIn(action_url, html)
        else:
            self.assertNotIn(label, html)
            self.assertNotIn(action_url, html)

    def test_detail_action_matrix_and_cancel_in_procurement(self):
        matrix = (
            ("staff-only", False, False, True, True, True),
            ("approve-only", True, True, True, False, False),
            ("fulfill-only", False, False, False, True, True),
            ("both", True, True, True, True, True),
            ("neither", False, False, False, False, False),
            ("requester", False, False, True, False, True),
            ("assigned user", False, False, False, False, True),
            ("unrelated", False, False, False, False, False),
        )

        for identity, can_approve, can_deny, can_cancel, can_mark_fulfilled, can_claim in matrix:
            user = self.actors[identity]
            with self.subTest(identity=identity):
                pending_html = self.render_actions(user, RequestStatusChoices.PENDING)
                self.assert_action_visibility(pending_html, "assets:request_approve", "Approve...", can_approve)
                self.assert_action_visibility(pending_html, "assets:request_deny", "Deny...", can_deny)
                self.assert_action_visibility(pending_html, "assets:request_cancel", "Cancel Request", can_cancel)
                self.assert_action_visibility(
                    pending_html,
                    "assets:request_mark_fulfilled",
                    "Complete manually...",
                    False,
                )

                approved_html = self.render_actions(user, RequestStatusChoices.APPROVED)
                self.assert_action_visibility(approved_html, "assets:request_approve", "Approve...", False)
                self.assert_action_visibility(approved_html, "assets:request_deny", "Deny...", False)
                self.assert_action_visibility(approved_html, "assets:request_cancel", "Cancel Request", can_cancel)
                self.assert_action_visibility(
                    approved_html,
                    "assets:request_mark_fulfilled",
                    "Complete manually...",
                    can_mark_fulfilled,
                )
                self.assertEqual(
                    can_asset_request_action(user, self.asset_request, "claim"),
                    can_claim,
                )

                procurement_html = self.render_actions(user, RequestStatusChoices.PROCUREMENT)
                self.assert_action_visibility(procurement_html, "assets:request_cancel", "Cancel Request", can_cancel)
                self.assert_action_visibility(procurement_html, "assets:request_approve", "Approve...", False)
                self.assert_action_visibility(procurement_html, "assets:request_deny", "Deny...", False)
                self.assert_action_visibility(
                    procurement_html,
                    "assets:request_mark_fulfilled",
                    "Complete manually...",
                    False,
                )
