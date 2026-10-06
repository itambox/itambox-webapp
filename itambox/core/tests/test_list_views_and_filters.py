"""Regression tests for issue #173 list-filter wiring and isolation.

Issue #173 originally bounded invalid filtersets to the tenant-scoped base
queryset without endorsing that generic fallback. Issue #199 now makes the
app-wide contract explicit: invalid filters retain their validation errors and
fail closed to an empty queryset.
"""

import datetime

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from assets.models import Supplier
from core.tests.mixins import TenantTestMixin, grant
from extras.filters import AlertLogFilterSet
from extras.models import AlertLog, AlertRule
from extras.tables import AlertLogTable
from extras.views import AlertLogListView
from organization.models import Location, Role, Site, Tenant
from procurement.filters import ContractFilterSet, PurchaseOrderFilterSet
from procurement.models import Contract, PurchaseOrder
from procurement.tables import ContractTable, PurchaseOrderTable
from procurement.views import ContractListView, PurchaseOrderListView

User = get_user_model()


class Issue173ListWiringTests(SimpleTestCase):
    def test_target_lists_use_the_generic_list_contract(self):
        cases = (
            (PurchaseOrderListView, PurchaseOrderFilterSet, "PurchaseOrderFilterForm", PurchaseOrderTable),
            (ContractListView, ContractFilterSet, "ContractFilterForm", ContractTable),
            (AlertLogListView, AlertLogFilterSet, "AlertLogFilterForm", AlertLogTable),
        )

        for view_class, expected_filterset, expected_form_name, expected_table in cases:
            with self.subTest(view=view_class.__name__):
                self.assertIs(view_class.filterset, expected_filterset)
                self.assertEqual(view_class.filterset_form.__name__, expected_form_name)
                self.assertIn("status", view_class.filterset_form().fields)
                self.assertIs(view_class.table, expected_table)
                self.assertNotIn("filterset_class", view_class.__dict__)

    def test_procurement_lists_do_not_declare_django_tables_aliases(self):
        for view_class in (PurchaseOrderListView, ContractListView):
            with self.subTest(view=view_class.__name__):
                self.assertNotIn("table_class", view_class.__dict__)

    def test_alert_list_preserves_its_existing_table(self):
        self.assertIs(AlertLogListView.table, AlertLogTable)


class Issue173ListRequestTests(TenantTestMixin, TestCase):
    permissions = [
        "procurement.view_purchaseorder",
        "procurement.view_contract",
        "extras.view_alertlog",
    ]

    def setUp(self):
        self.setup_tenant_context(
            name="WP3 Tenant A",
            slug="wp3-tenant-a",
            permissions=self.permissions,
        )
        self.tenant_a = self.tenant
        self.tenant_b = Tenant.objects.create(name="WP3 Tenant B", slug="wp3-tenant-b")
        self.site = Site.objects.create(name="WP3 Site", slug="wp3-site")
        self.supplier = Supplier.objects.create(name="WP3 Supplier", slug="wp3-supplier")
        self.content_type = ContentType.objects.get_for_model(AlertRule)

        with self.tenant_context(self.tenant_a, self.tenant_membership):
            self.location_a = Location.objects.create(
                name="WP3 Location A",
                slug="wp3-location-a",
                site=self.site,
                tenant=self.tenant_a,
            )
            self.po_a_match = self._purchase_order(
                tenant=self.tenant_a,
                location=self.location_a,
                order_number="WP3-PO-A-DRAFT",
                status=PurchaseOrder.STATUS_DRAFT,
            )
            self.po_a_other = self._purchase_order(
                tenant=self.tenant_a,
                location=self.location_a,
                order_number="WP3-PO-A-APPROVED",
                status=PurchaseOrder.STATUS_APPROVED,
            )
            self.contract_a_match = self._contract(
                tenant=self.tenant_a,
                name="WP3 Contract A Active",
                number="WP3-CTR-A-ACTIVE",
                status="active",
            )
            self.contract_a_other = self._contract(
                tenant=self.tenant_a,
                name="WP3 Contract A Draft",
                number="WP3-CTR-A-DRAFT",
                status="draft",
            )
            self.rule_a = self._alert_rule(self.tenant_a, "WP3 Rule A")
            self.alert_a_match = self._alert(
                tenant=self.tenant_a,
                rule=self.rule_a,
                subject="WP3 Alert A Active",
                object_id=101,
                status=AlertLog.STATUS_ACTIVE,
            )
            self.alert_a_other = self._alert(
                tenant=self.tenant_a,
                rule=self.rule_a,
                subject="WP3 Alert A Acknowledged",
                object_id=102,
                status=AlertLog.STATUS_ACKNOWLEDGED,
            )

        with self.tenant_context(self.tenant_b):
            self.location_b = Location.objects.create(
                name="WP3 Location B",
                slug="wp3-location-b",
                site=self.site,
                tenant=self.tenant_b,
            )
            self.po_b_shared = self._purchase_order(
                tenant=self.tenant_b,
                location=self.location_b,
                order_number="WP3-PO-B-DRAFT",
                status=PurchaseOrder.STATUS_DRAFT,
            )
            self.po_b_only = self._purchase_order(
                tenant=self.tenant_b,
                location=self.location_b,
                order_number="WP3-PO-B-RECEIVED",
                status=PurchaseOrder.STATUS_RECEIVED,
            )
            self.contract_b_shared = self._contract(
                tenant=self.tenant_b,
                name="WP3 Contract B Active",
                number="WP3-CTR-B-ACTIVE",
                status="active",
            )
            self.contract_b_only = self._contract(
                tenant=self.tenant_b,
                name="WP3 Contract B Expired",
                number="WP3-CTR-B-EXPIRED",
                status="expired",
            )
            self.rule_b = self._alert_rule(self.tenant_b, "WP3 Rule B")
            self.alert_b_shared = self._alert(
                tenant=self.tenant_b,
                rule=self.rule_b,
                subject="WP3 Alert B Active",
                object_id=201,
                status=AlertLog.STATUS_ACTIVE,
            )
            self.alert_b_only = self._alert(
                tenant=self.tenant_b,
                rule=self.rule_b,
                subject="WP3 Alert B Resolved",
                object_id=202,
                status=AlertLog.STATUS_RESOLVED,
            )

        self.client_login_to_tenant(self.tenant_user, self.tenant_a)

    def tearDown(self):
        self.clear_tenant_context()
        super().tearDown()

    def _purchase_order(self, *, tenant, location, order_number, status):
        return PurchaseOrder.objects.create(
            tenant=tenant,
            order_number=order_number,
            supplier=self.supplier,
            destination_location=location,
            created_by=self.tenant_admin,
            status=status,
        )

    def _contract(self, *, tenant, name, number, status):
        return Contract.objects.create(
            tenant=tenant,
            name=name,
            contract_number=number,
            contract_type="support",
            status=status,
            supplier=self.supplier,
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2027, 1, 1),
        )

    def _alert_rule(self, tenant, name):
        return AlertRule.objects.create(
            tenant=tenant,
            name=name,
            alert_type=AlertRule.ALERT_TYPE_LOW_STOCK,
            threshold_value=5,
        )

    def _alert(self, *, tenant, rule, subject, object_id, status):
        return AlertLog.objects.create(
            tenant=tenant,
            rule=rule,
            subject=subject,
            message=f"Message for {subject}",
            content_type=self.content_type,
            object_id=object_id,
            status=status,
        )

    @staticmethod
    def _table_objects(response):
        return list(response.context["table"].data)

    def test_unfiltered_lists_render_controls_and_exact_tenant_rows(self):
        cases = (
            (
                "procurement:purchaseorder_list",
                "PurchaseOrderFilterForm",
                [self.po_a_match, self.po_a_other],
                [self.po_b_shared, self.po_b_only],
            ),
            (
                "procurement:contract_list",
                "ContractFilterForm",
                [self.contract_a_match, self.contract_a_other],
                [self.contract_b_shared, self.contract_b_only],
            ),
            (
                "extras:alertlog_list",
                "AlertLogFilterForm",
                [self.alert_a_match, self.alert_a_other],
                [self.alert_b_shared, self.alert_b_only],
            ),
        )

        for url_name, form_class_name, own_rows, foreign_rows in cases:
            with self.subTest(url=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(type(response.context["filter_form"]).__name__, form_class_name)
                self.assertIn("status", response.context["filter_form"].fields)
                self.assertContains(response, 'name="status"')
                self.assertEqual(set(self._table_objects(response)), set(own_rows))
                for row in foreign_rows:
                    self.assertNotContains(response, str(row))

        po_response = self.client.get(reverse("procurement:purchaseorder_list"))
        self.assertContains(po_response, self.po_a_match.get_absolute_url())
        contract_response = self.client.get(reverse("procurement:contract_list"))
        self.assertContains(contract_response, self.contract_a_match.get_absolute_url())

    def test_status_filters_narrow_after_tenant_scoping(self):
        cases = (
            (
                "procurement:purchaseorder_list",
                PurchaseOrder.STATUS_DRAFT,
                self.po_a_match,
                [self.po_a_other, self.po_b_shared, self.po_b_only],
            ),
            (
                "procurement:contract_list",
                "active",
                self.contract_a_match,
                [self.contract_a_other, self.contract_b_shared, self.contract_b_only],
            ),
            (
                "extras:alertlog_list",
                AlertLog.STATUS_ACTIVE,
                self.alert_a_match,
                [self.alert_a_other, self.alert_b_shared, self.alert_b_only],
            ),
        )

        for url_name, status, expected_row, excluded_rows in cases:
            with self.subTest(url=url_name, status=status):
                response = self.client.get(reverse(url_name), {"status": status})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self._table_objects(response), [expected_row])
                for row in excluded_rows:
                    self.assertNotContains(response, str(row))

    def test_valid_foreign_only_status_returns_explicit_empty_result(self):
        cases = (
            ("procurement:purchaseorder_list", PurchaseOrder.STATUS_RECEIVED),
            ("procurement:contract_list", "expired"),
            ("extras:alertlog_list", AlertLog.STATUS_RESOLVED),
        )

        for url_name, status in cases:
            with self.subTest(url=url_name, status=status):
                response = self.client.get(reverse(url_name), {"status": status})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(self._table_objects(response), [])
                table = response.context["table"]
                self.assertTrue(table.empty_text)
                self.assertContains(response, str(table.empty_text))

    def test_tenant_owned_related_filter_choices_exclude_foreign_objects(self):
        purchase_order_response = self.client.get(reverse("procurement:purchaseorder_list"))
        location_choices = purchase_order_response.context["filter_form"].fields["destination_location"].queryset
        self.assertIn(self.location_a, location_choices)
        self.assertNotIn(self.location_b, location_choices)

        alert_response = self.client.get(reverse("extras:alertlog_list"))
        rule_choices = alert_response.context["filter_form"].fields["rule"].queryset
        self.assertIn(self.rule_a, rule_choices)
        self.assertNotIn(self.rule_b, rule_choices)

    def test_foreign_related_values_show_validation_and_fail_closed(self):
        cases = (
            (
                "procurement:purchaseorder_list",
                "destination_location",
                self.location_b.pk,
                str(self.location_b),
            ),
            (
                "procurement:contract_list",
                "supplier",
                # Supplier is global reference data, not tenant-owned, so there
                # is no tenant-B supplier identity.  An unknown related id is
                # the applicable non-disclosure case for this list.
                self.supplier.pk + 999_999,
                None,
            ),
            (
                "extras:alertlog_list",
                "rule",
                self.rule_b.pk,
                str(self.rule_b),
            ),
        )

        for url_name, field_name, foreign_value, foreign_label in cases:
            with self.subTest(url=url_name, field=field_name):
                response = self.client.get(reverse(url_name), {field_name: foreign_value})
                self.assertEqual(response.status_code, 200)
                self.assertIn(field_name, response.context["filter_form"].errors)
                self.assertEqual(self._table_objects(response), [])
                if foreign_label:
                    self.assertNotContains(response, foreign_label)

    def test_invalid_status_shows_validation_and_fails_closed(self):
        cases = ("procurement:purchaseorder_list", "procurement:contract_list", "extras:alertlog_list")

        for url_name in cases:
            with self.subTest(url=url_name):
                response = self.client.get(reverse(url_name), {"status": "not-a-valid-status"})
                self.assertEqual(response.status_code, 200)
                self.assertIn("status", response.context["filter_form"].errors)
                self.assertEqual(self._table_objects(response), [])

    def test_missing_view_permissions_deny_all_three_lists(self):
        user = User.objects.create_user(username="wp3-no-perms", password="password")
        role = Role.objects.create(tenant=self.tenant_a, name="WP3 No Permissions", permissions=[])
        grant(user, self.tenant_a, role)
        self.client_login_to_tenant(user, self.tenant_a)

        for url_name in (
            "procurement:purchaseorder_list",
            "procurement:contract_list",
            "extras:alertlog_list",
        ):
            with self.subTest(url=url_name):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 403)
