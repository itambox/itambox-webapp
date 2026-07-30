import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.tests.mixins import grant
from organization.models import Role, Tenant
from procurement.models import Contract

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
        return f'W/"{contract.updated_at.isoformat()}"'

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
