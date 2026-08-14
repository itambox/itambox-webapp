"""REST and bulk-boundary regression coverage for issue #183."""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.tests.mixins import TenantTestMixin, grant
from extras.models import AlertLog, AlertRule
from organization.models import Role, Tenant

User = get_user_model()


class AlertBulkTenantBoundaryTests(TenantTestMixin, TestCase):
    permissions = ["extras.view_alertlog", "extras.change_alertlog"]

    def setUp(self):
        self.setup_tenant_context(
            name="Issue 183 Bulk A",
            slug="issue-183-bulk-a",
            permissions=self.permissions,
        )
        self.tenant_a = self.tenant
        self.tenant_b = Tenant.objects.create(name="Issue 183 Bulk B", slug="issue-183-bulk-b")
        self.rule_a = AlertRule.objects.create(
            tenant=self.tenant_a,
            name="Issue 183 Bulk Rule A",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=1,
        )
        self.rule_b = AlertRule._base_manager.create(
            tenant=self.tenant_b,
            name="Issue 183 Bulk Rule B",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=1,
        )
        ct = ContentType.objects.get_for_model(AlertRule)
        self.local = AlertLog._base_manager.create(
            tenant=self.tenant_a,
            rule=self.rule_a,
            subject="local",
            message="local",
            content_type=ct,
            object_id=self.rule_a.pk,
        )
        self.foreign = AlertLog._base_manager.create(
            tenant=self.tenant_b,
            rule=self.rule_b,
            subject="foreign-secret-target",
            message="foreign",
            content_type=ct,
            object_id=self.rule_b.pk,
        )
        self.client_login_to_tenant(self.tenant_user, self.tenant_a)

    def test_bulk_selection_with_unresolved_null_tenant_mutates_nothing(self):
        unresolved = AlertLog._base_manager.create(
            tenant=None,
            rule=self.rule_a,
            subject="unresolved",
            message="unresolved",
            content_type=ContentType.objects.get_for_model(AlertRule),
            object_id=self.rule_a.pk + 100000,
            tenant_resolution_status="unresolved",
        )
        response = self.client.post(
            reverse("extras:alertlog_bulk_acknowledge"),
            {"pk": [unresolved.pk]},
        )
        self.assertEqual(response.status_code, 302)
        unresolved.refresh_from_db()
        self.assertEqual(unresolved.status, AlertLog.STATUS_ACTIVE)

    def test_bulk_selection_with_foreign_pk_mutates_nothing(self):
        response = self.client.post(
            reverse("extras:alertlog_bulk_acknowledge"),
            {"pk": [self.local.pk, self.foreign.pk]},
        )
        self.assertEqual(response.status_code, 302)
        self.local.refresh_from_db()
        self.foreign.refresh_from_db()
        self.assertEqual(self.local.status, AlertLog.STATUS_ACTIVE)
        self.assertEqual(self.foreign.status, AlertLog.STATUS_ACTIVE)

    def test_superuser_cannot_bulk_mutate_unresolved_null_tenant_alert(self):
        unresolved = AlertLog._base_manager.create(
            tenant=None,
            rule=self.rule_a,
            subject="unresolved-superuser",
            message="unresolved-superuser",
            content_type=ContentType.objects.get_for_model(AlertRule),
            object_id=self.rule_a.pk + 100001,
            tenant_resolution_status="unresolved",
        )
        self.client.logout()
        self.client.force_login(self.tenant_admin)
        self.clear_tenant_context()
        response = self.client.post(
            reverse("extras:alertlog_bulk_acknowledge"),
            {"pk": [unresolved.pk]},
        )
        self.assertEqual(response.status_code, 302)
        unresolved.refresh_from_db()
        self.assertEqual(unresolved.status, AlertLog.STATUS_ACTIVE)


class AlertLogReadOnlyAPITests(APITestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Issue 183 API A", slug="issue-183-api-a")
        self.tenant_b = Tenant.objects.create(name="Issue 183 API B", slug="issue-183-api-b")
        permissions = ["extras.view_alertlog"]
        role = Role.objects.create(tenant=self.tenant_a, name="Issue 183 API Viewer", permissions=permissions)
        self.user = User.objects.create_user(username="issue183-api-user", password="pw")
        grant(self.user, self.tenant_a, role)
        self.rule_a = AlertRule._base_manager.create(
            tenant=self.tenant_a,
            name="Issue 183 API Rule A",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=1,
        )
        self.rule_b = AlertRule._base_manager.create(
            tenant=self.tenant_b,
            name="Issue 183 API Rule B Secret",
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=1,
        )
        ct = ContentType.objects.get_for_model(AlertRule)
        self.alert_a = AlertLog._base_manager.create(
            tenant=self.tenant_a,
            rule=self.rule_a,
            subject="Tenant A alert",
            message="A message",
            content_type=ct,
            object_id=self.rule_a.pk,
            status=AlertLog.STATUS_ACTIVE,
        )
        self.alert_b = AlertLog._base_manager.create(
            tenant=self.tenant_b,
            rule=self.rule_b,
            subject="Tenant B secret alert",
            message="B secret message",
            content_type=ct,
            object_id=self.rule_b.pk,
            status=AlertLog.STATUS_RESOLVED,
        )

        self.alert_global = AlertLog._base_manager.create(
            tenant=None,
            rule=self.rule_a,
            subject="Unresolved global legacy alert",
            message="must not be tenant-visible",
            content_type=ct,
            object_id=self.rule_a.pk + 100000,
            status=AlertLog.STATUS_ACTIVE,
            tenant_resolution_status="unresolved",
        )
        self.client.force_login(self.user)
        session = self.client.session
        session["active_tenant_id"] = self.tenant_a.pk
        session.save()

    def _list(self):
        return reverse("api:extras_api:alertlog-list")

    def _detail(self, pk):
        return reverse("api:extras_api:alertlog-detail", kwargs={"pk": pk})

    @staticmethod
    def _rows(response):
        data = response.data
        return data["results"] if isinstance(data, dict) and "results" in data else data

    def test_list_is_tenant_scoped_and_serializes_lifecycle_fields(self):
        response = self.client.get(self._list())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        rows = self._rows(response)
        self.assertEqual({row["id"] for row in rows}, {self.alert_a.pk})
        self.assertEqual(rows[0]["status"], AlertLog.STATUS_ACTIVE)
        self.assertIn("delivery_status", rows[0])
        self.assertIn("tenant_resolution_status", rows[0])
        self.assertNotIn("Tenant B secret", str(response.data))

    def test_unresolved_null_tenant_alert_is_not_visible_to_active_tenant(self):
        response = self.client.get(self._list())
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertNotIn(self.alert_global.pk, {row["id"] for row in self._rows(response)})
        self.assertNotIn("Unresolved global legacy alert", str(response.data))

    def test_status_filter_is_applied_without_cross_tenant_leak(self):
        response = self.client.get(self._list(), {"status": AlertLog.STATUS_RESOLVED})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(self._rows(response), [])

    def test_foreign_detail_is_not_found(self):
        response = self.client.get(self._detail(self.alert_b.pk))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotIn("Tenant B secret", str(response.data))

    def test_mutation_methods_are_not_supported(self):
        for method in ("post", "patch", "delete"):
            with self.subTest(method=method):
                request = getattr(self.client, method)
                response = request(self._detail(self.alert_a.pk), {}, format="json")
                self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_405_METHOD_NOT_ALLOWED))
        self.assertEqual(AlertLog._base_manager.get(pk=self.alert_a.pk).status, AlertLog.STATUS_ACTIVE)

    def test_unauthenticated_read_is_rejected(self):
        self.client.logout()
        response = self.client.get(self._list())
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_post_to_list_is_not_supported(self):
        response = self.client.post(self._list(), {}, format="json")
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_405_METHOD_NOT_ALLOWED))
