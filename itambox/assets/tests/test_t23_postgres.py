"""T23 PostgreSQL regressions through the public search/report surfaces."""

import json
from types import SimpleNamespace

from django.test import TestCase, override_settings
from django.urls import reverse

from assets.models import Asset, AssetType, Manufacturer
from assets.services.specification_consumers.contracts import FieldFilter, FieldReference
from assets.services.specification_consumers.query import apply_specification_filters
from core.reports import build_report_context
from core.tests.mixins import TenantTestMixin
from extras.models import ReportTemplate
from organization.models import Tenant


class T23PostgreSQLConsumerTests(TenantTestMixin, TestCase):
    """Exercise source-qualified JSON consumers against the real PostgreSQL ORM."""

    def setUp(self):
        self.setup_tenant_context(name="T23 Tenant A", slug="t23-tenant-a")
        self.other_tenant = Tenant.objects.create(name="T23 Tenant B", slug="t23-tenant-b")
        manufacturer = Manufacturer.objects.create(name="T23 Manufacturer")
        self.asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="T23 Model",
            slug="t23-model",
            custom_field_data={"memory_capacity": "16.000"},
        )
        self.asset = Asset.objects.create(
            name="T23 Authorized Asset",
            asset_tag="T23-A",
            tenant=self.tenant,
            asset_type=self.asset_type,
            custom_field_data={
                "memory_capacity": "24.000",
                "bad_number": "not-a-number",
                "bad_date": "2024-02-31",
            },
        )
        self.other_asset = Asset.objects.create(
            name="T23 Other Tenant Asset",
            asset_tag="T23-B",
            tenant=self.other_tenant,
            asset_type=self.asset_type,
            custom_field_data={"memory_capacity": "48.000"},
        )
        self.asset_reference = FieldReference(source="asset", key="memory_capacity")
        self.asset_type_reference = FieldReference(source="asset_type", key="memory_capacity")
        self.decimal_definition = SimpleNamespace(
            key="memory_capacity",
            field_type="decimal",
            targets=frozenset({"asset", "asset_type"}),
            activation="global",
            lifecycle="active",
        )
        self.asset_definition = SimpleNamespace(
            key="memory_capacity",
            field_type="decimal",
            targets=frozenset({"asset"}),
            activation="global",
            lifecycle="active",
        )
        self.template = ReportTemplate.objects.create(
            name="T23 Public Report",
            tenant=self.tenant,
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=["asset_tag"],
            include_summary_cards=False,
        )

    def _scoped(self, filter_spec, *, reference=None, definition=None):
        reference = reference or filter_spec.reference
        definitions = {reference: definition} if definition is not None else None
        return apply_specification_filters(
            Asset.all_objects.all(),
            (filter_spec,),
            tenant_ids=(self.tenant.pk,),
            definitions=definitions,
        )

    def test_source_qualified_search_distinguishes_asset_and_asset_type_values(self):
        asset_query = self._scoped(
            FieldFilter(self.asset_reference, "eq", "24.000"),
            definition=self.asset_definition,
        )
        asset_type_query = self._scoped(
            FieldFilter(self.asset_type_reference, "eq", "16.000"),
            reference=self.asset_type_reference,
            definition=self.decimal_definition,
        )

        self.assertEqual(list(asset_query.values_list("asset_tag", flat=True)), ["T23-A"])
        self.assertEqual(list(asset_type_query.values_list("asset_tag", flat=True)), ["T23-A"])

    def test_malformed_numeric_and_date_json_is_safe_and_excluded(self):
        numeric_filter = FieldFilter(self.asset_reference, "gte", "20.000")
        numeric_query = self._scoped(numeric_filter, definition=self.asset_definition)
        self.assertEqual(list(numeric_query.values_list("asset_tag", flat=True)), ["T23-A"])

        date_reference = FieldReference(source="asset", key="bad_date")
        date_definition = SimpleNamespace(
            key="bad_date",
            field_type="date",
            targets=frozenset({"asset"}),
            activation="global",
            lifecycle="active",
        )
        date_query = self._scoped(
            FieldFilter(date_reference, "eq", "2024-03-02"),
            reference=date_reference,
            definition=date_definition,
        )
        self.assertEqual(list(date_query.values_list("asset_tag", flat=True)), [])
        explain = date_query.explain()
        print(f"T23_EXPLAIN malformed-date query:\n{explain}")
        self.assertTrue(explain.strip())

    def test_core_search_registry_reaches_asset_specification_entrypoint(self):
        import assets.search  # noqa: F401  # registers the real AssetIndex
        from core.search import search

        result = search(
            Asset,
            specification_filters=(FieldFilter(self.asset_reference, "eq", "24.000"),),
            queryset=Asset.all_objects.all(),
            tenant_ids=(self.tenant.pk,),
            definitions={self.asset_reference: self.asset_definition},
        )
        self.assertEqual(list(result.values_list("asset_tag", flat=True)), ["T23-A"])

    def test_report_registry_scopes_before_filter_and_export(self):
        specification_filter = FieldFilter(self.asset_reference, "eq", "24.000")
        headers, rows, _cards, _grouped, _chart, context = build_report_context(
            self.template,
            active_tenant=self.tenant,
            specification_filters=(specification_filter,),
            specification_definitions={self.asset_reference: self.asset_definition},
            specification_export_references=(self.asset_reference,),
        )

        self.assertEqual(headers, ["Asset Tag"])
        self.assertEqual([row["Asset Tag"] for row in rows], ["T23-A"])
        machine_export = context["specification_export"]
        self.assertEqual(
            machine_export.columns,
            (
                "asset.spec.memory_capacity.value",
                "asset.spec.memory_capacity.present",
                "asset.spec.memory_capacity.status",
            ),
        )
        self.assertEqual(len(machine_export.rows), 1)
        self.assertEqual(machine_export.rows[0]["asset.spec.memory_capacity.value"], "24.000")

    @override_settings(REPORT_DESIGNER_ENABLED=True)
    def test_public_preview_and_download_urls_transport_filters(self):
        self.client_login_to_tenant(self.tenant_admin, self.tenant)
        filter_document = json.dumps(
            {
                "filters": [
                    {
                        "source": "asset",
                        "field_key": "memory_capacity",
                        "operator": "eq",
                        "value": "24.000",
                    }
                ]
            }
        )
        preview = self.client.post(
            reverse("extras:reporttemplate_preview"),
            {
                "name": "T23 Preview",
                "report_type": ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
                "included_columns": ["asset_tag"],
                "specification_filters": filter_document,
            },
        )
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "T23-A")
        self.assertNotContains(preview, "T23-B")

        download = self.client.get(
            reverse("extras:reporttemplate_download", kwargs={"pk": self.template.pk}),
            {"format": "csv", "specification_filters": filter_document},
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "text/csv")
        self.assertIn("T23-A", download.content.decode())
        self.assertNotIn("T23-B", download.content.decode())
