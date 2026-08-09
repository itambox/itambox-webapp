"""Custody RBAC, tenant-boundary, token, and consent regression tests for issue #259.

The fixture values in this module are deliberately dummy values. They are not
bearer credentials, signature payloads, or production EULA content.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import NoReverseMatch, reverse
from model_bakery import baker
from rest_framework.test import APITestCase

from assets.models import Asset
from compliance.models import CustodyReceipt, CustodyTemplate
from core.tests.mixins import TenantTestMixin, grant
from organization.models import AssetHolder, Role, Tenant

User = get_user_model()

DUMMY_TOKEN_A = "dummy-custody-token-a"
DUMMY_TOKEN_B = "dummy-custody-token-b"
DUMMY_SIGNATURE = "dummy-signature-payload"
DUMMY_EULA = "dummy-eula-marker"
WRONG_RECIPIENT_MESSAGE = "wrong-recipient"


class CustodyRBACFixtureMixin(TenantTestMixin):
    """Build two tenants and the principals used by the issue matrix."""

    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Custody Tenant A", slug="custody-tenant-a")
        self.tenant_b = Tenant.objects.create(name="Custody Tenant B", slug="custody-tenant-b")

        self.superadmin = User.objects.create_superuser(
            username="custody-superadmin",
            email="custody-superadmin@example.test",
            password="password",
        )
        self.tenant_admin = self._make_member(
            "custody-admin",
            self.tenant_a,
            {
                "compliance.view_custodytemplate",
                "compliance.add_custodytemplate",
                "compliance.change_custodytemplate",
                "compliance.delete_custodytemplate",
                "compliance.view_custodyreceipt",
                "compliance.prepare_custodyreceipt",
                "compliance.export_custodyreceipt",
            },
        )
        self.technician = self._make_member(
            "custody-technician",
            self.tenant_a,
            {
                "compliance.view_custodytemplate",
                "compliance.view_custodyreceipt",
                "compliance.prepare_custodyreceipt",
            },
        )
        self.recipient = self._make_member("custody-recipient", self.tenant_a, set())
        self.unrelated = self._make_member("custody-unrelated", self.tenant_a, set())
        self.cross_tenant_user = self._make_member(
            "custody-cross-tenant",
            self.tenant_b,
            {"compliance.view_custodyreceipt"},
        )

        self.recipient_holder = AssetHolder.objects.create(
            user=self.recipient,
            first_name="Dummy",
            last_name="Recipient",
            upn="dummy-recipient@example.test",
            email="dummy-recipient@example.test",
            tenant=self.tenant_a,
        )
        self.unrelated_holder = AssetHolder.objects.create(
            user=self.unrelated,
            first_name="Dummy",
            last_name="Unrelated",
            upn="dummy-unrelated@example.test",
            email="dummy-unrelated@example.test",
            tenant=self.tenant_a,
        )
        self.cross_holder = AssetHolder.objects.create(
            user=self.cross_tenant_user,
            first_name="Dummy",
            last_name="CrossTenant",
            upn="dummy-cross-tenant@example.test",
            email="dummy-cross-tenant@example.test",
            tenant=self.tenant_b,
        )

        self.asset_a = baker.make(
            Asset,
            name="Dummy Custody Asset A",
            asset_tag="DUMMY-CUSTODY-ASSET-A",
            tenant=self.tenant_a,
        )
        self.asset_b = baker.make(
            Asset,
            name="Dummy Custody Asset B",
            asset_tag="DUMMY-CUSTODY-ASSET-B",
            tenant=self.tenant_b,
        )
        self.template_a = CustodyTemplate.objects.create(
            name="Dummy Custody Template A",
            tenant=self.tenant_a,
            eula_text=DUMMY_EULA,
        )
        self.template_b = CustodyTemplate.objects.create(
            name="Dummy Custody Template B",
            tenant=self.tenant_b,
            eula_text=DUMMY_EULA,
        )
        self.receipt_a = CustodyReceipt.objects.create(
            asset=self.asset_a,
            holder=self.recipient_holder,
            custody_template=self.template_a,
            token=DUMMY_TOKEN_A,
            eula_text=DUMMY_EULA,
        )
        self.receipt_b = CustodyReceipt.objects.create(
            asset=self.asset_b,
            holder=self.cross_holder,
            custody_template=self.template_b,
            token=DUMMY_TOKEN_B,
            eula_text=DUMMY_EULA,
        )

    @staticmethod
    def _make_member(username, tenant, permissions):
        user = User.objects.create_user(
            username=username,
            email=f"{username}@example.test",
            password="password",
        )
        role = Role.objects.create(
            tenant=tenant,
            name=f"{username} role",
            permissions=sorted(permissions),
        )
        grant(user, tenant, role)
        return user

    def _login_to_tenant(self, user, tenant):
        self.client_login_to_tenant(user, tenant)

    def _sign_url(self, token):
        return reverse("compliance:custody_eula_sign", kwargs={"token": token})

    def _assert_no_receipt_payload(self, response, receipt):
        self.assertNotContains(response, receipt.asset.asset_tag)
        self.assertNotContains(response, str(receipt.holder))
        self.assertNotContains(response, DUMMY_EULA)


@override_settings(REQUIRE_CUSTODY_SIGNIN=True)
class CustodyRecipientConsentTests(CustodyRBACFixtureMixin, TestCase):
    """Recipient-only consent and the public token error contract."""

    def test_intended_recipient_can_accept(self):
        # AC §6: Rollen und Tenant-Grenze — intended Recipient can consent.
        self._login_to_tenant(self.recipient, self.tenant_a)

        response = self.client.post(
            self._sign_url(DUMMY_TOKEN_A),
            {"action": "accept", "signature_canvas": DUMMY_SIGNATURE},
        )

        self.assertEqual(response.status_code, 200)
        self.receipt_a.refresh_from_db()
        self.assertEqual(self.receipt_a.acceptance_status, CustodyReceipt.STATUS_ACCEPTED)
        self.assertTrue(self.receipt_a.accepted)

    def test_superadmin_cannot_override_recipient_binding(self):
        # AC §6: Rollen und Tenant-Grenze — superadmin has global internal power but no Recipient override.
        self._login_to_tenant(self.superadmin, self.tenant_a)

        response = self.client.post(
            self._sign_url(DUMMY_TOKEN_A),
            {"action": "accept", "signature_canvas": DUMMY_SIGNATURE},
        )

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, WRONG_RECIPIENT_MESSAGE)
        self._assert_no_receipt_payload(response, self.receipt_a)
        self.receipt_a.refresh_from_db()
        self.assertEqual(self.receipt_a.acceptance_status, CustodyReceipt.STATUS_PENDING)

    def test_unrelated_same_tenant_is_wrong_recipient_without_mutation(self):
        # AC §6: Rollen und Tenant-Grenze — unrelated same-tenant user gets wrong-recipient.
        self._login_to_tenant(self.unrelated, self.tenant_a)

        response = self.client.post(
            self._sign_url(DUMMY_TOKEN_A),
            {"action": "accept", "signature_canvas": DUMMY_SIGNATURE},
        )

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, WRONG_RECIPIENT_MESSAGE)
        self.assertNotContains(response, "internal custody permission")
        self._assert_no_receipt_payload(response, self.receipt_a)
        self.receipt_a.refresh_from_db()
        self.assertEqual(self.receipt_a.acceptance_status, CustodyReceipt.STATUS_PENDING)

    def test_invalid_token_is_neutral_404_without_payload(self):
        # AC §6: Token, Ablauf und Fehler — invalid token → 404.
        self._login_to_tenant(self.recipient, self.tenant_a)

        response = self.client.get(self._sign_url("invalid-dummy-token"))

        self.assertEqual(response.status_code, 404)
        self._assert_no_receipt_payload(response, self.receipt_a)

    def test_expired_token_is_410_without_payload(self):
        # AC §6: Token, Ablauf und Fehler — expired token → 410 without payload.
        CustodyReceipt.objects.filter(pk=self.receipt_a.pk).update(
            created_date=self.receipt_a.created_date - timedelta(days=8),
        )
        self._login_to_tenant(self.recipient, self.tenant_a)

        response = self.client.get(self._sign_url(DUMMY_TOKEN_A))

        self.assertEqual(response.status_code, 410)
        self._assert_no_receipt_payload(response, self.receipt_a)

    def test_empty_signature_does_not_accept(self):
        # AC §6: Prepare- und Consent-Semantik — empty signature is not acceptance.
        self._login_to_tenant(self.recipient, self.tenant_a)

        response = self.client.post(
            self._sign_url(DUMMY_TOKEN_A),
            {"action": "accept", "signature_canvas": "empty"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "valid signature")
        self.receipt_a.refresh_from_db()
        self.assertEqual(self.receipt_a.acceptance_status, CustodyReceipt.STATUS_PENDING)
        self.assertFalse(self.receipt_a.accepted)

    def test_decline_is_distinct_from_empty_and_accept(self):
        # AC §6: Prepare- und Consent-Semantik — decline remains a separate terminal state.
        self._login_to_tenant(self.recipient, self.tenant_a)

        response = self.client.post(self._sign_url(DUMMY_TOKEN_A), {"action": "decline"})

        self.assertEqual(response.status_code, 200)
        self.receipt_a.refresh_from_db()
        self.assertEqual(self.receipt_a.acceptance_status, CustodyReceipt.STATUS_DECLINED)
        self.assertFalse(self.receipt_a.accepted)

    def test_completed_receipt_cannot_be_accepted_again(self):
        # AC §6: Prepare- und Consent-Semantik — already accepted remains completed.
        self.receipt_a.acceptance_status = CustodyReceipt.STATUS_ACCEPTED
        self.receipt_a.accepted = True
        self.receipt_a.save(update_fields=["acceptance_status", "accepted", "updated_at"])
        self._login_to_tenant(self.recipient, self.tenant_a)

        response = self.client.post(
            self._sign_url(DUMMY_TOKEN_A),
            {"action": "accept", "signature_canvas": DUMMY_SIGNATURE},
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "valid signature")
        self.receipt_a.refresh_from_db()
        self.assertEqual(self.receipt_a.acceptance_status, CustodyReceipt.STATUS_ACCEPTED)

    def test_completed_declined_receipt_cannot_be_accepted_again(self):
        # AC §6: Prepare- und Consent-Semantik — already declined remains declined.
        self.receipt_a.acceptance_status = CustodyReceipt.STATUS_DECLINED
        self.receipt_a.save(update_fields=["acceptance_status", "updated_at"])
        self._login_to_tenant(self.recipient, self.tenant_a)

        response = self.client.post(
            self._sign_url(DUMMY_TOKEN_A),
            {"action": "accept", "signature_canvas": DUMMY_SIGNATURE},
        )

        self.assertEqual(response.status_code, 200)
        self.receipt_a.refresh_from_db()
        self.assertEqual(self.receipt_a.acceptance_status, CustodyReceipt.STATUS_DECLINED)
        self.assertFalse(self.receipt_a.accepted)

    def test_authenticated_cross_tenant_recipient_cannot_sign_foreign_receipt(self):
        # AC §6: Rollen und Tenant-Grenze — cross-tenant recipient cannot consent through UI.
        self._login_to_tenant(self.cross_tenant_user, self.tenant_b)

        response = self.client.post(
            self._sign_url(DUMMY_TOKEN_A),
            {"action": "accept", "signature_canvas": DUMMY_SIGNATURE},
        )

        self.assertEqual(response.status_code, 403)
        self.assertContains(response, WRONG_RECIPIENT_MESSAGE)
        self._assert_no_receipt_payload(response, self.receipt_a)
        self.receipt_a.refresh_from_db()
        self.assertEqual(self.receipt_a.acceptance_status, CustodyReceipt.STATUS_PENDING)


@override_settings(REQUIRE_CUSTODY_SIGNIN=False)
class CustodyBearerPolicyTests(CustodyRBACFixtureMixin, TestCase):
    """Explicit non-login mode remains covered without creating an operator path."""

    def test_dummy_bearer_token_can_use_explicit_non_login_recipient_flow(self):
        # AC §6: Token, Ablauf und Fehler — REQUIRE_CUSTODY_SIGNIN=False is explicit and tested.
        self.client.logout()

        response = self.client.post(
            self._sign_url(DUMMY_TOKEN_A),
            {"action": "accept", "signature_canvas": DUMMY_SIGNATURE},
        )

        self.assertEqual(response.status_code, 200)
        self.receipt_a.refresh_from_db()
        self.assertEqual(self.receipt_a.acceptance_status, CustodyReceipt.STATUS_ACCEPTED)


class CustodyConcurrentConsentTests(CustodyRBACFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def _post_from_independent_client(self, signature):
        client = self.client_class()
        client.force_login(self.recipient)
        session = client.session
        session["active_tenant_id"] = self.tenant_a.pk
        session.save()
        return client.post(
            self._sign_url(DUMMY_TOKEN_A),
            {"action": "accept", "signature_canvas": signature},
        )

    def test_concurrent_accept_posts_have_one_terminal_transition(self):
        # AC §6: Prepare- und Consent-Semantik — concurrent POSTs are serialized by select_for_update().
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self._post_from_independent_client, "dummy-signature-one"),
                executor.submit(self._post_from_independent_client, "dummy-signature-two"),
            ]
            responses = [future.result() for future in futures]

        self.receipt_a.refresh_from_db()
        self.assertEqual(self.receipt_a.acceptance_status, CustodyReceipt.STATUS_ACCEPTED)
        self.assertEqual(sum(response.status_code == 200 for response in responses), 2)
        self.assertEqual(CustodyReceipt.objects.filter(pk=self.receipt_a.pk, accepted=True).count(), 1)

    def test_sign_post_contains_row_lock(self):
        # AC §6: Prepare- und Consent-Semantik — receipt POST must use a row lock.
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self._login_to_tenant(self.recipient, self.tenant_a)
        with CaptureQueriesContext(connection) as queries:
            self.client.post(
                self._sign_url(DUMMY_TOKEN_A),
                {"action": "accept", "signature_canvas": DUMMY_SIGNATURE},
            )

        self.assertTrue(
            any(
                "custodyreceipt" in query["sql"].lower() and "for update" in query["sql"].lower()
                for query in queries.captured_queries
            ),
            "recipient consent must lock the receipt row",
        )


def reverse_if_available(*names, kwargs=None):
    """Resolve the first compatible route spelling in the target implementation."""
    for name in names:
        try:
            return reverse(name, kwargs=kwargs or {})
        except NoReverseMatch:
            continue
    return None


class CustodyInternalRouteTests(CustodyRBACFixtureMixin, TestCase):
    """Authenticated internal receipt views are separate from recipient consent."""

    def setUp(self):
        super().setUp()
        self.internal_list_url = reverse_if_available(
            "compliance:custodyreceipt_list",
            "compliance:custodyreceipt-list",
        )
        self.internal_detail_url = reverse_if_available(
            "compliance:custodyreceipt_detail",
            "compliance:custodyreceipt-detail",
            kwargs={"pk": self.receipt_a.pk},
        )
        self.prepare_url = reverse_if_available(
            "compliance:custodyreceipt_prepare",
            "compliance:custodyreceipt-prepare",
            kwargs={"pk": self.receipt_a.pk},
        )
        self.export_url = reverse_if_available(
            "compliance:custodyreceipt_export",
            "compliance:custodyreceipt-export",
            kwargs={"pk": self.receipt_a.pk},
        )

    def _require_url(self, url, surface):
        self.assertIsNotNone(url, f"SOL must expose the {surface} route")
        return url

    def _assert_internal_denial(self, response):
        body = response.content.decode("utf-8", errors="replace").lower()
        self.assertNotIn("wrong-recipient", body)
        self.assertNotIn("not the intended recipient", body)
        self.assertTrue("internal" in body or "custody" in body)

    def test_superadmin_sees_global_internal_receipt_list(self):
        # AC §6: Rollen und Tenant-Grenze — superadmin sees receipts globally on the internal path.
        url = self._require_url(self.internal_list_url, "internal receipt list")
        self.client.force_login(self.superadmin)

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.asset_a.asset_tag)
        self.assertContains(response, self.asset_b.asset_tag)
        self.assertNotContains(response, DUMMY_TOKEN_A)
        self.assertNotContains(response, DUMMY_TOKEN_B)

    def test_tenant_admin_sees_own_tenant_and_foreign_detail_is_404(self):
        # AC §6: Rollen und Tenant-Grenze — Tenant Admin is own-tenant only; foreign detail → 404.
        list_url = self._require_url(self.internal_list_url, "internal receipt list")
        foreign_detail_url = self._require_url(
            reverse_if_available(
                "compliance:custodyreceipt_detail",
                "compliance:custodyreceipt-detail",
                kwargs={"pk": self.receipt_b.pk},
            ),
            "internal receipt detail",
        )
        self._login_to_tenant(self.tenant_admin, self.tenant_a)

        list_response = self.client.get(list_url)
        detail_response = self.client.get(foreign_detail_url)

        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, self.asset_a.asset_tag)
        self.assertNotContains(list_response, self.asset_b.asset_tag)
        self.assertEqual(detail_response.status_code, 404)
        self._assert_no_receipt_payload(detail_response, self.receipt_b)

    def test_technician_can_list_and_detail_but_raw_token_is_not_rendered(self):
        # AC §6: Rollen und Tenant-Grenze — Technician gets internal view/detail only.
        list_url = self._require_url(self.internal_list_url, "internal receipt list")
        detail_url = self._require_url(self.internal_detail_url, "internal receipt detail")
        self._login_to_tenant(self.technician, self.tenant_a)

        list_response = self.client.get(list_url)
        detail_response = self.client.get(detail_url)

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, self.asset_a.asset_tag)
        self.assertNotContains(list_response, DUMMY_TOKEN_A)
        self.assertNotContains(detail_response, DUMMY_TOKEN_A)

    def test_internal_route_without_permission_is_403_not_recipient_error(self):
        # AC §6: Token, Ablauf und Fehler — missing internal permission → internal 403.
        url = self._require_url(self.internal_list_url, "internal receipt list")
        self._login_to_tenant(self.unrelated, self.tenant_a)

        response = self.client.get(url)

        self.assertEqual(response.status_code, 403)
        self._assert_internal_denial(response)
        self._assert_no_receipt_payload(response, self.receipt_a)

    def test_cross_tenant_internal_detail_is_404_without_payload(self):
        # AC §6: Rollen und Tenant-Grenze — cross-tenant UI detail → 404.
        url = self._require_url(self.internal_detail_url, "internal receipt detail")
        self._login_to_tenant(self.cross_tenant_user, self.tenant_b)

        response = self.client.get(url)

        self.assertEqual(response.status_code, 404)
        self._assert_no_receipt_payload(response, self.receipt_a)

    def test_technician_cannot_change_template_policy(self):
        # AC §6: Rollen und Tenant-Grenze — Technician template policy is not mutable.
        url = reverse("compliance:custodytemplate_update", kwargs={"pk": self.template_a.pk})
        self._login_to_tenant(self.technician, self.tenant_a)

        response = self.client.get(url)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(CustodyTemplate.objects.get(pk=self.template_a.pk).eula_text, DUMMY_EULA)

    def test_technician_export_is_denied_by_default(self):
        # AC §6: API und interne Darstellung — Technician has no export capability by default.
        if self.export_url is None:
            self.skipTest("Export route is not present on the current SOL slices; target export denial remains open")
        self._login_to_tenant(self.technician, self.tenant_a)

        response = self.client.get(self.export_url)

        self.assertEqual(response.status_code, 403)
        self._assert_no_receipt_payload(response, self.receipt_a)

    def test_prepare_route_is_explicitly_skipped_when_slice_d_is_absent(self):
        # AC §6: Prepare- und Consent-Semantik — Slice D is optional until SOL publishes it.
        if self.prepare_url is None:
            self.skipTest("Slice D prepare-session route is not present on SOL yet")
        self._login_to_tenant(self.technician, self.tenant_a)
        response = self.client.post(self.prepare_url, {"holder_id": self.unrelated_holder.pk})
        self.assertIn(response.status_code, (200, 201, 202, 204))
        self.receipt_a.refresh_from_db()
        self.assertEqual(self.receipt_a.holder_id, self.recipient_holder.pk)


class CustodyAPIContractTests(CustodyRBACFixtureMixin, APITestCase):
    """REST API tenant scope and read-only consent state."""

    def _api_login_to_tenant(self, user, tenant):
        self.client.force_login(user)
        session = self.client.session
        session["active_tenant_id"] = tenant.pk
        session.save()

    def _api_url(self, name, pk=None):
        kwargs = {"pk": pk} if pk is not None else {}
        return reverse(f"api:compliance_api:custodyreceipt-{name}", kwargs=kwargs)

    def test_technician_api_list_is_tenant_scoped(self):
        # AC §6: API und interne Darstellung — receipt API list is tenant-scoped.
        self._api_login_to_tenant(self.technician, self.tenant_a)

        response = self.client.get(self._api_url("list"))

        self.assertEqual(response.status_code, 200)
        payload = response.data.get("results", response.data)
        rendered = str(payload)
        self.assertIn(self.asset_a.asset_tag, rendered)
        self.assertNotIn(self.asset_b.asset_tag, rendered)
        self.assertNotIn(DUMMY_TOKEN_A, rendered)

    def test_cross_tenant_api_detail_is_404(self):
        # AC §6: Rollen und Tenant-Grenze — cross-tenant API detail → 404.
        self._api_login_to_tenant(self.cross_tenant_user, self.tenant_b)

        response = self.client.get(self._api_url("detail", self.receipt_a.pk))

        self.assertEqual(response.status_code, 404)
        self.assertNotIn(self.asset_a.asset_tag, str(response.data))
        self.assertNotIn(DUMMY_EULA, str(response.data))

    def test_api_patch_cannot_forge_acceptance(self):
        # AC §6: Prepare- und Consent-Semantik — API PATCH cannot forge acceptance.
        self._api_login_to_tenant(self.tenant_admin, self.tenant_a)

        response = self.client.patch(
            self._api_url("detail", self.receipt_a.pk),
            {
                "accepted": True,
                "acceptance_status": CustodyReceipt.STATUS_ACCEPTED,
                "signature_data": DUMMY_SIGNATURE,
                "signature_hash": "dummy-signature-hash",
            },
            format="json",
        )

        self.assertIn(response.status_code, (200, 400, 403))
        self.receipt_a.refresh_from_db()
        self.assertFalse(self.receipt_a.accepted)
        self.assertEqual(self.receipt_a.acceptance_status, CustodyReceipt.STATUS_PENDING)
