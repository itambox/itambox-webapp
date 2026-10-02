"""Identity journeys: SCIM deprovisioning keeps obligations visible and never guesses identity links."""

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from assets.services import checkout_asset
from organization.models import AssetHolder
from organization.services.offboarding import get_offboarding_report
from users.models import Token

from .support import JourneyMixin

User = get_user_model()


class ScimJourneyTests(JourneyMixin, TestCase):
    def setUp(self):
        self.make_tenant("journey-scim")
        self.provisioner = self.make_member(
            "scim-provisioner",
            {"organization.change_membership", "organization.add_membership", "users.view_usergroup"},
        )
        token = Token.objects.create(
            user=self.provisioner, tenant=self.tenant, expires=timezone.now() + timezone.timedelta(days=1)
        )
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {token.key}"}

    def _user_url(self, pk=None):
        if pk is None:
            return reverse("api:scim:user-list", kwargs={"tenant_slug": self.tenant.slug})
        return reverse("api:scim:user-detail", kwargs={"tenant_slug": self.tenant.slug, "pk": pk})

    @pytest.mark.xfail(strict=True, reason="SCIM DELETE soft-deletes a holder with live custody (#608)")
    def test_scim_delete_of_asset_holder_keeps_holder_and_offboarding_report_reachable(self):
        person = self.make_member("leaving-person", set())
        holder = self.make_holder(user=person)
        asset = self.make_asset()
        checkout_asset(asset=asset, holder=holder, request=None)

        response = self.client.delete(self._user_url(person.pk), **self.auth)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # The person is deprovisioned, but what they still hold must stay visible.
        self.assertTrue(AssetHolder.objects.filter(pk=holder.pk).exists(), "holder vanished from the active set")
        report = get_offboarding_report(AssetHolder.all_objects.get(pk=holder.pk))
        self.assertTrue(report.items, "offboarding report lost the outstanding assignment")

        reviewer = self.make_member("offboarding-reviewer", {"organization.view_assetholder", "assets.view_asset"})
        self.client_login_to_tenant(reviewer, self.tenant)
        detail = self.client.get(reverse("organization:assetholder_detail", kwargs={"pk": holder.pk}))
        self.assertEqual(detail.status_code, 200)

    @pytest.mark.xfail(strict=True, reason="SCIM create links a holder by email alone (#608)")
    def test_scim_create_does_not_link_unlinked_holder_by_email_when_upn_differs(self):
        holder = AssetHolder.objects.create(
            first_name="Jane",
            last_name="Smith",
            upn="jane.smith@corp.example.test",
            email="shared@example.test",
            tenant=self.tenant,
        )
        payload = {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "someone-else@example.test",
            "name": {"familyName": "Else", "givenName": "Someone"},
            "emails": [{"value": "shared@example.test", "primary": True}],
            "active": True,
        }

        response = self.client.post(self._user_url(), data=payload, content_type="application/json", **self.auth)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        holder.refresh_from_db()
        self.assertIsNone(holder.user_id, "an unlinked holder was bound to a different login by email alone")
