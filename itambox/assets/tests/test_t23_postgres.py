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
from extras.services.specifications.codecs import SAFE_INTEGER_MAX, SAFE_INTEGER_MIN
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
                "overflow_number": "1234567890123456789012345678901234567890123456789",
                "bad_date": "2024-02-31",
                "integer_above_32bit": "2147483648",
                "integer_below_32bit": "-2147483649",
                "integer_safe_max": str(SAFE_INTEGER_MAX),
                "integer_safe_min": str(SAFE_INTEGER_MIN),
                "integer_over_safe": str(SAFE_INTEGER_MAX + 1),
                "integer_under_safe": str(SAFE_INTEGER_MIN - 1),
                "integer_malformed": "not-an-integer",
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

        overflow_reference = FieldReference(source="asset", key="overflow_number")
        overflow_definition = SimpleNamespace(
            key="overflow_number",
            field_type="decimal",
            targets=frozenset({"asset"}),
            activation="global",
            lifecycle="active",
        )
        overflow_query = self._scoped(
            FieldFilter(overflow_reference, "gte", "1"),
            reference=overflow_reference,
            definition=overflow_definition,
        )
        self.assertEqual(list(overflow_query.values_list("asset_tag", flat=True)), [])

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

    def test_integer_consumer_preserves_the_shared_safe_integer_domain(self):
        cases = (
            ("integer_above_32bit", "eq", "2147483648", ["T23-A"]),
            ("integer_below_32bit", "eq", "-2147483649", ["T23-A"]),
            ("integer_safe_max", "eq", str(SAFE_INTEGER_MAX), ["T23-A"]),
            ("integer_safe_min", "eq", str(SAFE_INTEGER_MIN), ["T23-A"]),
            ("integer_over_safe", "gte", "0", []),
            ("integer_under_safe", "lte", "0", []),
            ("integer_malformed", "gte", "0", []),
        )

        for key, operator, value, expected_tags in cases:
            reference = FieldReference(source="asset", key=key)
            definition = SimpleNamespace(
                key=key,
                field_type="integer",
                targets=frozenset({"asset"}),
                activation="global",
                lifecycle="active",
            )
            query = self._scoped(
                FieldFilter(reference, operator, value),
                reference=reference,
                definition=definition,
            )
            self.assertEqual(list(query.values_list("asset_tag", flat=True)), expected_tags, msg=key)

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


def _presence_definition(key, field_type, *, targets=("asset",), lifecycle="active", activation="global"):
    return SimpleNamespace(
        key=key,
        field_type=field_type,
        targets=frozenset(targets),
        activation=activation,
        lifecycle=lifecycle,
    )


class T23PostgreSQLPresenceStatusAndScopeTests(TenantTestMixin, TestCase):
    """Database-level presence, status, typed comparison and tenant boundary."""

    def setUp(self):
        self.setup_tenant_context(name="T23 Presence A", slug="t23-presence-a")
        self.other_tenant = Tenant.objects.create(name="T23 Presence B", slug="t23-presence-b")
        manufacturer = Manufacturer.objects.create(name="T23 Presence Manufacturer")
        self.asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="T23 Presence Model",
            slug="t23-presence-model",
            custom_field_data={"memory_capacity": "16.000"},
        )
        self.typed_asset_type = AssetType.objects.create(
            manufacturer=manufacturer,
            model="T23 Presence Typed Model",
            slug="t23-presence-typed-model",
            custom_field_data={"memory_capacity": "777.000"},
        )

        def create(name, tag, data, *, tenant=None, asset_type=None):
            return Asset.objects.create(
                name=name,
                asset_tag=tag,
                tenant=tenant or self.tenant,
                asset_type=asset_type or self.asset_type,
                custom_field_data=data,
            )

        self.present = create(
            "T23 Present",
            "T23-P",
            {
                "ports": ["usb_c", "hdmi"],
                "flag": True,
                "count": "3",
                "label": "Rack Switch",
                "seen_on": "2024-02-29",
            },
        )
        self.null_row = create("T23 Null", "T23-N", {"ports": None, "flag": False, "count": "0"})
        self.empty_row = create("T23 Empty", "T23-E", {"ports": [], "count": ""})
        self.missing_row = create("T23 Missing", "T23-M", {})
        self.malformed_row = create("T23 Malformed", "T23-X", {"count": "not-an-integer", "flag": "1"})
        self.overflow_date_row = create("T23 Overflow Date", "T23-D", {"seen_on": "2024-02-31"})
        self.typed_type_row = create("T23 Typed Type", "T23-T", {}, asset_type=self.typed_asset_type)
        self.other_tenant_row = create(
            "T23 Other Tenant",
            "T23-B",
            {"ports": ["usb_c"], "count": "3"},
            tenant=self.other_tenant,
        )

    def _rows(self, filter_spec, reference=None, definition=None, *, tenant_ids=None):
        reference = reference or filter_spec.reference
        definitions = {reference: definition} if definition is not None else None
        ids = (self.tenant.pk,) if tenant_ids is None else tenant_ids
        queryset = apply_specification_filters(
            Asset.all_objects.all(),
            (filter_spec,),
            tenant_ids=ids,
            definitions=definitions,
        )
        return sorted(queryset.values_list("asset_tag", flat=True))

    def test_tenant_boundary_is_applied_before_any_value_expression(self):
        ports = FieldReference(source="asset", key="ports")
        multi_select = _presence_definition("ports", "multi_select")
        filter_spec = FieldFilter(ports, "contains_any", ["usb_c"])

        self.assertEqual(self._rows(filter_spec, definition=multi_select), ["T23-P"])
        self.assertEqual(self._rows(filter_spec, definition=multi_select, tenant_ids=()), [])
        self.assertEqual(
            self._rows(filter_spec, definition=multi_select, tenant_ids=(self.other_tenant.pk,)), ["T23-B"]
        )

    def test_presence_predicates_distinguish_missing_null_and_empty_values(self):
        ports = FieldReference(source="asset", key="ports")
        count = FieldReference(source="asset", key="count")

        self.assertEqual(self._rows(FieldFilter(ports, "is_missing")), ["T23-D", "T23-M", "T23-T", "T23-X"])
        self.assertEqual(self._rows(FieldFilter(ports, "is_null")), ["T23-N"])
        self.assertEqual(self._rows(FieldFilter(ports, "is_empty")), ["T23-E"])
        self.assertEqual(self._rows(FieldFilter(count, "is_empty")), ["T23-E"])

    def test_unknown_status_returns_every_row_carrying_the_key(self):
        ports = FieldReference(source="asset", key="ports")

        rows = self._rows(FieldFilter(ports, "eq", "unused", status="unknown"))

        self.assertEqual(rows, ["T23-E", "T23-N", "T23-P"])

    def test_history_status_is_empty_without_a_knowable_definition(self):
        ports = FieldReference(source="asset", key="ports")
        filter_spec = FieldFilter(ports, "contains_any", ["usb_c"], status="history")

        self.assertEqual(self._rows(filter_spec), [])

    def test_history_status_complements_a_retired_definition_applicability(self):
        """A retired definition is applicable to no row, so history adds no restriction.

        The negated empty applicability is satisfied by every row, so only the value
        condition decides the result, while the current status stays empty.
        """

        ports = FieldReference(source="asset", key="ports")
        filter_spec = FieldFilter(ports, "contains_any", ["usb_c"], status="history")
        retired = _presence_definition("ports", "multi_select", lifecycle="retired")
        current = FieldFilter(ports, "contains_any", ["usb_c"], status="current")

        self.assertEqual(self._rows(filter_spec, definition=retired), ["T23-P"])
        self.assertEqual(self._rows(current, definition=retired), [])

    def test_current_status_excludes_rows_that_fail_the_type_guard(self):
        count = FieldReference(source="asset", key="count")
        integer = _presence_definition("count", "integer")
        filter_spec = FieldFilter(count, "gte", "0")

        self.assertEqual(self._rows(filter_spec, definition=integer), ["T23-N", "T23-P"])

    def test_invalid_status_returns_only_rows_that_fail_the_type_guard(self):
        count = FieldReference(source="asset", key="count")
        integer = _presence_definition("count", "integer")
        filter_spec = FieldFilter(count, "eq", "0", status="invalid")

        self.assertEqual(self._rows(filter_spec, definition=integer), ["T23-E", "T23-X"])

    def test_multi_select_containment_ignores_the_empty_array(self):
        ports = FieldReference(source="asset", key="ports")
        multi_select = _presence_definition("ports", "multi_select")

        self.assertEqual(
            self._rows(FieldFilter(ports, "contains_all", ["usb_c", "hdmi"]), definition=multi_select), ["T23-P"]
        )
        self.assertEqual(
            self._rows(FieldFilter(ports, "contains_all", ["usb_c", "displayport"]), definition=multi_select), []
        )

    def test_boolean_equality_is_typed_and_ignores_other_spellings(self):
        """Only JSON booleans match; the string "1" never satisfies a boolean filter."""

        flag = FieldReference(source="asset", key="flag")
        boolean = _presence_definition("flag", "boolean")

        self.assertEqual(self._rows(FieldFilter(flag, "eq", True), definition=boolean), ["T23-P"])
        self.assertEqual(self._rows(FieldFilter(flag, "eq", False), definition=boolean), ["T23-N"])
        self.assertEqual(self._rows(FieldFilter(flag, "neq", True), definition=boolean), ["T23-N"])
        self.assertEqual(self._rows(FieldFilter(flag, "eq", True), definition=boolean), ["T23-P"])

    def test_text_contains_and_date_bounds_use_the_stored_json_values(self):
        label = FieldReference(source="asset", key="label")
        seen_on = FieldReference(source="asset", key="seen_on")
        text = _presence_definition("label", "text")
        date_definition = _presence_definition("seen_on", "date")

        self.assertEqual(self._rows(FieldFilter(label, "contains", "rack"), definition=text), ["T23-P"])
        self.assertEqual(self._rows(FieldFilter(seen_on, "gte", "2024-01-01"), definition=date_definition), ["T23-P"])
        self.assertEqual(self._rows(FieldFilter(seen_on, "lte", "2024-12-31"), definition=date_definition), ["T23-P"])

    def test_asset_type_source_uses_the_related_asset_type_json_column(self):
        memory = FieldReference(source="asset_type", key="memory_capacity")
        definition = _presence_definition("memory_capacity", "decimal", targets=("asset_type",))

        self.assertEqual(
            self._rows(FieldFilter(memory, "eq", "777.000"), reference=memory, definition=definition), ["T23-T"]
        )
        self.assertEqual(
            self._rows(FieldFilter(memory, "eq", "16.000"), reference=memory, definition=definition),
            ["T23-D", "T23-E", "T23-M", "T23-N", "T23-P", "T23-X"],
        )

    def test_asset_only_definition_target_excludes_the_asset_type_source(self):
        memory = FieldReference(source="asset_type", key="memory_capacity")
        definition = _presence_definition("memory_capacity", "decimal", targets=("asset",))

        self.assertEqual(self._rows(FieldFilter(memory, "eq", "777.000"), reference=memory, definition=definition), [])
