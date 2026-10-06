import pytest
from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.test import TestCase
from django.urls import reverse

from assets.choices import RequestStatusChoices
from assets.models import Asset, AssetRequest, AssetRole, AssetType, Manufacturer, StatusLabel
from core.tests.mixins import TenantTestMixin
from organization.models import AssetHolder, Role

User = get_user_model()


class AssetDetailClaimVisibilityTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(slug="issue-497-claim", permissions=["assets.view_asset"])
        self.set_active_tenant(self.tenant, self.tenant_membership)

        self.manufacturer = Manufacturer.objects.create(name="Issue 497 Manufacturer", slug="issue-497-manufacturer")
        self.asset_role = AssetRole.objects.create(name="Issue 497 Laptop", slug="issue-497-laptop")
        self.deployable = StatusLabel.objects.create(
            name="Issue 497 Deployable", slug="issue-497-deployable", type=StatusLabel.TYPE_DEPLOYABLE
        )
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="Issue 497 Model",
            slug="issue-497-model",
            requestable=True,
            asset_role=self.asset_role,
        )
        self.asset = Asset.objects.create(
            name="Issue 497 Asset",
            asset_tag="ISSUE497-001",
            asset_type=self.asset_type,
            asset_role=self.asset_role,
            status=self.deployable,
            requestable=True,
            tenant=self.tenant,
        )

        self.requester = self.tenant_user
        self.assignee = self._create_viewer("issue497-assignee")
        self.fulfiller = self._create_viewer("issue497-fulfiller", "assets.fulfill_assetrequest")
        self.unrelated_user = self._create_viewer("issue497-unrelated")
        self.assignee_holder = AssetHolder.objects.create(
            user=self.assignee,
            first_name="Assigned",
            last_name="User",
            upn="assigned@example.com",
            tenant=self.tenant,
        )
        self.asset_request = AssetRequest.objects.create(
            requester=self.requester,
            asset=self.asset,
            assigned_user=self.assignee_holder,
            status=RequestStatusChoices.APPROVED,
            tenant=self.tenant,
        )

    def _create_viewer(self, username, *permissions):
        user = User.objects.create_user(username=username, password="password")
        role = Role.objects.create(
            tenant=self.tenant,
            name=f"{username} Role",
            permissions=["assets.view_asset", *permissions],
        )
        self.grant(user, self.tenant, role)
        return user

    def _asset_detail(self, user):
        self.client_login_to_tenant(user, self.tenant)
        return self.client.get(reverse("assets:asset_detail", kwargs={"pk": self.asset.pk}))

    def test_nonstaff_fulfiller_can_claim_when_neither_requester_nor_assignee(self):
        self.assertFalse(self.fulfiller.is_staff)
        self.assertNotEqual(self.fulfiller.pk, self.asset_request.requester_id)
        self.assertNotEqual(self.fulfiller.pk, self.asset_request.assigned_user.user_id)

        response = self._asset_detail(self.fulfiller)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["approved_request"].pk, self.asset_request.pk)
        claim_url = reverse("assets:request_claim", kwargs={"pk": self.asset_request.pk})
        self.assertContains(response, 'action="' + claim_url + '"')
        # An unrelated fulfilment actor records a handover; the self-service
        # wording would be wrong for them.
        self.assertContains(response, "Record Handover &amp; Fulfill")
        self.assertNotContains(response, "Claim &amp; Confirm Pickup")

    def test_requester_and_assigned_user_keep_claim_affordance(self):
        for actor in (self.requester, self.assignee):
            with self.subTest(username=actor.username):
                response = self._asset_detail(actor)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["approved_request"].pk, self.asset_request.pk)
                self.assertContains(response, "Claim &amp; Confirm Pickup")

    def test_user_without_qualifying_claim_grant_does_not_get_affordance(self):
        response = self._asset_detail(self.unrelated_user)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["approved_request"])
        self.assertNotContains(response, "Claim &amp; Confirm Pickup")

    def test_claim_label_stays_self_service_wording_for_requester_and_assignee(self):
        for actor in (self.requester, self.assignee):
            with self.subTest(username=actor.username):
                response = self._asset_detail(actor)

                self.assertContains(response, "Claim &amp; Confirm Pickup")
                self.assertNotContains(response, "Record Handover &amp; Fulfill")

    def test_claim_copy_renders_in_german(self):
        self.client_login_to_tenant(self.fulfiller, self.tenant)
        response = self.client.get(
            reverse("assets:asset_detail", kwargs={"pk": self.asset.pk}),
            headers={"accept-language": "de"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Übergabe erfassen und Anforderung erfüllen")
        self.assertContains(response, "Möchten Sie dieses Asset übernehmen und die Abholung bestätigen?")

    def test_request_detail_offers_handover_to_the_scoped_fulfiller(self):
        self.client_login_to_tenant(self.fulfiller, self.tenant)
        response = self.client.get(reverse("assets:request_detail", kwargs={"pk": self.asset_request.pk}))

        self.assertEqual(response.status_code, 200)
        claim_url = reverse("assets:request_claim", kwargs={"pk": self.asset_request.pk})
        self.assertContains(response, 'action="' + claim_url + '"')
        self.assertContains(response, "Record Handover &amp; Fulfill")

    def test_request_detail_hides_handover_from_a_user_without_a_qualifying_grant(self):
        self.client_login_to_tenant(self.unrelated_user, self.tenant)
        response = self.client.get(reverse("assets:request_detail", kwargs={"pk": self.asset_request.pk}))

        self.assertIn(response.status_code, (403, 404))


def _render_asset_request_toolbar(**extra_context):
    context = {"is_deleted_view": False, "model_name_str": "assets.assetrequest", **extra_context}
    return get_template("generic/includes/bulk_action_toolbar.html").render(context)


@pytest.mark.parametrize(
    "extra_context",
    [{"asset_request_bulk_receive_available": False}, {}],
    ids=["false", "absent"],
)
def test_bulk_receive_button_is_hidden_when_context_key_is_false_or_absent(extra_context):
    html = _render_asset_request_toolbar(**extra_context)
    assert "btn-bulk-receive" not in html


def test_bulk_receive_button_posts_selected_requests_to_bulk_receive_endpoint():
    html = _render_asset_request_toolbar(asset_request_bulk_receive_available=True)

    assert 'method="post"' in html
    bulk_receive_url = reverse("assets:request_bulk_receive")
    assert 'action="' + bulk_receive_url + '"' in html
    assert 'class="bulk-receive-form d-inline"' in html
    assert 'class="bulk-pks-container d-inline"></div>' in html
    assert 'class="btn btn-sm btn-success bulk-action-btn btn-bulk-receive"' in html
