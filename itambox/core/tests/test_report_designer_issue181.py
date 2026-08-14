from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from core.models import ChangeLoggingMixin
from core.reports.columns import headers_for, label_for
from core.reports.rendering import (
    _custom_context,
    _safe_custom_value,
    custom_html_execution_allowed,
    render_report_csv,
    render_report_html,
)
from core.tasks.reports import (
    _parse_authorized_scope,
    _principal_can_authorize_scope,
    _render_report_output,
    _resolve_authorized_principal,
    _resolve_report_scope,
    _resolve_scope_authorization,
    _scope_requires_authorization,
    _tenant_scope_permission_is_valid,
    generate_scheduled_report_task,
)
from extras.apps import _scheduled_reports_probe
from extras.forms import ReportTemplateForm
from extras.models import ReportTemplate, ScheduledReport, ScheduledReportScopeAuthorization
from extras.views import ReportTemplateDetailView, ReportTemplateDownloadView, ReportTemplatePreviewView
from itambox.capabilities import ActivationState
from organization.models import Tenant


class ReportDesignerIssue181ContractTests(SimpleTestCase):
    def test_model_and_form_keep_legacy_fields_but_marker_is_not_editable(self):
        fields = {field.name: field for field in ReportTemplate._meta.get_fields()}
        assert {"advanced_mode", "template_content", "legacy_designer_grandfathered"} <= set(fields)
        assert fields["legacy_designer_grandfathered"].editable is False
        form = ReportTemplateForm()
        assert {"advanced_mode", "template_content"} <= set(form.fields)
        assert "legacy_designer_grandfathered" not in form.fields

    @override_settings(FEATURE_REPORT_DESIGNER=False, REPORT_DESIGNER_ENABLED=False)
    def test_flag_off_rejects_new_advanced_mode_and_custom_html(self):
        with pytest.raises(ValidationError, match="ITAMBOX_FEATURE_REPORT_DESIGNER"):
            ReportTemplate(
                name="blocked advanced",
                report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
                advanced_mode=True,
            ).clean()
        with pytest.raises(ValidationError, match="ITAMBOX_FEATURE_REPORT_DESIGNER"):
            ReportTemplate(
                name="blocked html",
                report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
                template_content="<p>custom</p>",
            ).clean()

    def test_unknown_columns_are_rejected_with_machine_key(self):
        with pytest.raises(ValidationError, match="not_published"):
            ReportTemplate(
                name="bad columns",
                report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
                included_columns=["asset_tag", "not_published"],
            ).clean()

    def test_custom_jinja_context_does_not_expose_request_or_runtime_objects(self):
        template = SimpleNamespace(
            name="safe",
            template_content="{{ request.COOKIES|default('missing') }}|{{ report_name }}",
            legacy_designer_grandfathered=True,
        )
        rendered = render_report_html(
            {"report_name": "safe", "request": SimpleNamespace(COOKIES={"sessionid": "secret"})}, template
        )
        assert "secret" not in rendered
        assert "missing" in rendered

    @override_settings(FEATURE_REPORT_DESIGNER=True, REPORT_DESIGNER_ENABLED=False)
    def test_html_execution_is_independent_from_legacy_csv_shape(self):
        template = SimpleNamespace(
            name="custom",
            advanced_mode=False,
            template_content="<h1>{{ report_name }}</h1>",
        )
        output = _render_report_output(
            SimpleNamespace(format=ScheduledReport.FORMAT_HTML),
            template,
            ["Asset Tag"],
            [{"Asset Tag": "A-1"}],
            {"report_name": "custom", "summary_cards": [], "grouped_data": {}},
        )
        assert output.email_body == "<h1>custom</h1>"

    @override_settings(FEATURE_REPORT_DESIGNER=False, REPORT_DESIGNER_ENABLED=False)
    def test_grandfathered_html_rendering_helper_works_but_worker_delivery_is_paused(self):
        template = SimpleNamespace(
            name="grandfathered",
            advanced_mode=False,
            template_content="<h1>{{ report_name }}</h1>",
            legacy_designer_grandfathered=True,
        )
        output = _render_report_output(
            SimpleNamespace(format=ScheduledReport.FORMAT_HTML),
            template,
            [],
            [],
            {"report_name": "grandfathered", "summary_cards": [], "grouped_data": {}},
        )
        assert output.email_body == "<h1>grandfathered</h1>"

    @override_settings(FEATURE_REPORT_DESIGNER=False, REPORT_DESIGNER_ENABLED=False)
    def test_flag_off_does_not_execute_non_grandfathered_custom_html(self):
        template = SimpleNamespace(
            name="inactive",
            advanced_mode=True,
            template_content="<h1>{{ report_name }}</h1>",
            legacy_designer_grandfathered=False,
        )
        output = _render_report_output(
            SimpleNamespace(format=ScheduledReport.FORMAT_HTML),
            template,
            [],
            [],
            {"report_name": "inactive", "summary_cards": [], "grouped_data": {}},
        )
        assert "<h1>inactive</h1>" not in output.email_body

    def test_worker_scope_authorization_checks_each_persisted_tenant(self):
        tenant_a = SimpleNamespace(pk=1)
        tenant_b = SimpleNamespace(pk=2)
        principal = Mock(is_active=True, pk=99)
        principal.has_perm.side_effect = lambda permission, obj=None: obj is tenant_a
        authorization = SimpleNamespace(scope_tenant_ids=[tenant_a.pk, tenant_b.pk], authorized_by=principal)
        authorization_manager = Mock()
        authorization_manager.filter.return_value.select_related.return_value.first.return_value = authorization
        schedule = SimpleNamespace(pk=123)

        with (
            patch("core.tasks.reports.ScheduledReportScopeAuthorization.objects", authorization_manager),
            patch("core.tasks.reports.TaskContext") as task_context,
            patch("core.tasks.reports.get_current_user", return_value=principal),
        ):
            result = _resolve_scope_authorization(schedule, tenant_a, [tenant_a, tenant_b])

        assert result is None
        assert task_context.call_args_list == [
            call(tenant_id=tenant_a.pk, user_id=principal.pk, operation="reports.scope_authorization"),
            call(tenant_id=tenant_b.pk, user_id=principal.pk, operation="reports.scope_authorization"),
        ]
        assert principal.has_perm.call_args_list == [
            call("reports.view_cross_tenant_reports", obj=tenant_a),
            call("reports.view_cross_tenant_reports", obj=tenant_b),
        ]

    def test_legacy_csv_shape_does_not_require_custom_html(self):
        template = SimpleNamespace(
            name="legacy csv",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            advanced_mode=True,
            template_content="",
        )
        output = _render_report_output(
            SimpleNamespace(format=ScheduledReport.FORMAT_CSV),
            template,
            ["Asset Tag"],
            [{"Asset Tag": "A-1"}],
            {
                "report_name": "legacy csv",
                "summary_cards": [
                    {"label": "Total Hardware Assets", "value": "1"},
                    {"label": "Total Acquisition Sum", "value": "$0.00"},
                ],
                "grouped_data": {"Berlin": [{"Asset Tag": "A-1"}]},
            },
        )
        assert output.attachment_content.startswith("Metric,Value")
        assert "Total Hardware Assets,1" in output.attachment_content


class ReportDesignerIssue181CoverageTests(SimpleTestCase):
    def test_columns_resolve_only_canonical_keys(self):
        assert label_for("asset_tag") == "Asset Tag"
        assert headers_for(["asset_tag", "not_published", "name"]) == ["Asset Tag", "Asset Name"]

    def test_custom_context_sanitizes_supported_values_and_drops_runtime_objects(self):
        runtime_object = object()
        assert _safe_custom_value(None) is None
        assert _safe_custom_value("text") == "text"
        assert _safe_custom_value(4) == 4
        assert _safe_custom_value(1.5) == 1.5
        assert _safe_custom_value(True) is True
        assert _safe_custom_value(date(2026, 8, 14)) == "2026-08-14"
        assert _safe_custom_value(datetime(2026, 8, 14, 5, 6, 7)) == "2026-08-14 05:06:07"
        assert _safe_custom_value(Decimal("12.50")) == "12.50"
        assert _safe_custom_value({1: [Decimal("2.00"), runtime_object]}) == {"1": ["2.00", None]}
        assert _safe_custom_value(("a", runtime_object)) == ["a", None]
        context = _custom_context({"report_name": "safe", "request": runtime_object, "ignored": "hidden"})
        assert context == {"report_name": "safe", "request": None}

    def test_custom_html_gate_and_fallback_cover_feature_and_grandfather_paths(self):
        template = SimpleNamespace(template_content="<p>{{ report_name }}</p>", legacy_designer_grandfathered=False)
        with patch("core.reports.rendering.report_designer_probe", return_value=SimpleNamespace(active=False)):
            assert custom_html_execution_allowed(template) is False
            fallback = render_report_html({"report_name": "curated"}, template)
        assert "curated" in fallback

        with patch("core.reports.rendering.report_designer_probe", return_value=SimpleNamespace(active=True)):
            assert custom_html_execution_allowed(template) is True
            rendered = render_report_html({"report_name": "<safe>"}, template)
        assert "&lt;safe&gt;" in rendered

        template.legacy_designer_grandfathered = True
        with patch("core.reports.rendering.report_designer_probe", return_value=SimpleNamespace(active=False)):
            assert custom_html_execution_allowed(template) is True

    def test_csv_renderer_covers_stable_and_each_legacy_report_shape(self):
        stable = SimpleNamespace(advanced_mode=False)
        stable_csv = render_report_csv(stable, ["Asset Tag"], [{"Asset Tag": "=SUM(A1)"}])
        assert "'" in stable_csv

        asset = SimpleNamespace(advanced_mode=True, report_type="asset_summary")
        asset_csv = render_report_csv(
            asset,
            [],
            [{"Asset Tag": "A-1"}],
            summary_cards=[{"label": "Total Acquisition Sum", "value": "$10"}],
            grouped_data={"Berlin": [{"Asset Tag": "A-1"}]},
        )
        assert "Total Hardware Assets,1" in asset_csv
        assert "Berlin,1" in asset_csv

        license_template = SimpleNamespace(advanced_mode=True, report_type="license_utilization")
        license_csv = render_report_csv(
            license_template,
            [],
            [
                {
                    "License Name": "Office",
                    "Software": "Suite",
                    "Total Seats": 10,
                    "Assigned Seats": 6,
                    "Available Seats": 4,
                    "Utilization Rate": "60%",
                }
            ],
        )
        assert "License,Software,Total Seats" in license_csv
        assert "Office,Suite,10,6,4,60%" in license_csv

        subscription_template = SimpleNamespace(advanced_mode=True, report_type="subscription_renewals")
        subscription_csv = render_report_csv(
            subscription_template,
            [],
            [
                {
                    "Subscription Name": "Cloud",
                    "Provider": "Acme",
                    "Billing Cycle": "Monthly",
                    "Cost": 12,
                    "End Date": "2027-01-01",
                }
            ],
            summary_cards=[{"label": "Est. Monthly Spend", "value": "$12"}],
        )
        assert "Active Subscriptions,1" in subscription_csv
        assert "Cloud,Acme,Monthly,12,2027-01-01" in subscription_csv

        unknown_template = SimpleNamespace(advanced_mode=True, report_type="new_provider")
        unknown_csv = render_report_csv(unknown_template, ["Asset Tag"], [{"Asset Tag": "A-2"}])
        assert unknown_csv.startswith("Asset Tag")

    def test_scope_helpers_fail_closed_for_invalid_approvals(self):
        tenant_a = SimpleNamespace(pk=1)
        tenant_b = SimpleNamespace(pk=2)
        assert _scope_requires_authorization(tenant_a, []) is False
        assert _scope_requires_authorization(tenant_a, [tenant_a]) is False
        assert _scope_requires_authorization(tenant_a, [tenant_b]) is True
        assert _scope_requires_authorization(None, []) is True
        assert _parse_authorized_scope(SimpleNamespace(scope_tenant_ids=["2", 1, 2])) == [1, 2]
        assert _parse_authorized_scope(SimpleNamespace(scope_tenant_ids=["not-an-id"])) is None
        assert _resolve_authorized_principal(SimpleNamespace(authorized_by=None)) is None
        assert _resolve_authorized_principal(SimpleNamespace(authorized_by=SimpleNamespace(is_active=False))) is None
        assert _resolve_authorized_principal(SimpleNamespace(authorized_by=SimpleNamespace(is_active=True))) is not None

        class MissingPrincipal:
            @property
            def authorized_by(self):
                raise ObjectDoesNotExist

        assert _resolve_authorized_principal(MissingPrincipal()) is None

    def test_scope_permission_and_principal_checks_cover_denials_and_exceptions(self):
        tenant = SimpleNamespace(pk=7)
        principal = SimpleNamespace(pk=42)
        assert _tenant_scope_permission_is_valid(principal, SimpleNamespace(pk=None)) is False
        worker = Mock()
        worker.has_perm.return_value = False
        with (
            patch("core.tasks.reports.TaskContext"),
            patch("core.tasks.reports.get_current_user", return_value=worker),
        ):
            assert _tenant_scope_permission_is_valid(principal, tenant) is False
            worker.has_perm.return_value = True
            assert _tenant_scope_permission_is_valid(principal, tenant) is True

        with patch("core.tasks.reports.TaskContext", side_effect=PermissionDenied):
            assert _tenant_scope_permission_is_valid(principal, tenant) is False
        with (
            patch("core.tasks.reports.TaskContext"),
            patch("core.tasks.reports.get_current_user", side_effect=ObjectDoesNotExist),
        ):
            assert _tenant_scope_permission_is_valid(principal, tenant) is False

        with patch("core.tasks.reports._tenant_scope_permission_is_valid", side_effect=[True, False]) as check:
            assert _principal_can_authorize_scope(principal, [tenant, SimpleNamespace(pk=8)]) is False
            assert check.call_count == 2
        assert _principal_can_authorize_scope(principal, []) is True

    def test_scope_authorization_rejects_missing_mismatched_and_inactive_approvals(self):
        tenant_a = SimpleNamespace(pk=1)
        tenant_b = SimpleNamespace(pk=2)
        schedule = SimpleNamespace(pk=123)
        manager = Mock()
        manager.filter.return_value.select_related.return_value.first.return_value = None
        with patch("core.tasks.reports.ScheduledReportScopeAuthorization.objects", manager):
            assert _resolve_scope_authorization(schedule, tenant_a, [tenant_a, tenant_b]) is None

        authorization = SimpleNamespace(scope_tenant_ids=[1], authorized_by=SimpleNamespace(is_active=True, pk=8))
        manager.filter.return_value.select_related.return_value.first.return_value = authorization
        with patch("core.tasks.reports.ScheduledReportScopeAuthorization.objects", manager):
            assert _resolve_scope_authorization(schedule, tenant_a, [tenant_a, tenant_b]) is None

        authorization.scope_tenant_ids = [1, 2]
        authorization.authorized_by = SimpleNamespace(is_active=False, pk=8)
        with patch("core.tasks.reports.ScheduledReportScopeAuthorization.objects", manager):
            assert _resolve_scope_authorization(schedule, tenant_a, [tenant_a, tenant_b]) is None

        with patch("core.tasks.reports._principal_can_authorize_scope", return_value=False):
            with patch("core.tasks.reports.ScheduledReportScopeAuthorization.objects", manager):
                assert _resolve_scope_authorization(schedule, tenant_a, [tenant_a, tenant_b]) is None

        with patch("core.tasks.reports.ScheduledReportScopeAuthorization.objects") as unused:
            assert _resolve_scope_authorization(schedule, tenant_a, [tenant_a]) is None
            unused.filter.assert_not_called()

    def test_generate_task_stops_for_inactive_capability_and_unauthorized_scope(self):
        inactive_schedule = SimpleNamespace(
            pk=1,
            report=SimpleNamespace(legacy_designer_grandfathered=False),
            is_active=True,
        )
        manager = Mock()
        manager.get.return_value = inactive_schedule
        with (
            patch("core.tasks.reports.ScheduledReport.objects", manager),
            patch("core.tasks.reports.report_designer_probe", return_value=SimpleNamespace(active=False)),
        ):
            result = generate_scheduled_report_task(1)
        assert result.status.value == "skipped"
        assert result.code == "report.capability_inactive"

        broad_schedule = SimpleNamespace(
            pk=2,
            report=SimpleNamespace(legacy_designer_grandfathered=False),
            is_active=True,
        )
        manager.get.return_value = broad_schedule
        tenant_a = SimpleNamespace(pk=1, id=1)
        tenant_b = SimpleNamespace(pk=2, id=2)
        with (
            patch("core.tasks.reports.ScheduledReport.objects", manager),
            patch("core.tasks.reports.report_designer_probe", return_value=SimpleNamespace(active=True)),
            patch("core.tasks.reports._resolve_report_scope", return_value=(tenant_a, [tenant_a, tenant_b])),
            patch("core.tasks.reports._resolve_scope_authorization", return_value=None),
            patch("core.tasks.reports._scope_requires_authorization", return_value=True),
        ):
            result = generate_scheduled_report_task(2)
        assert result.status.value == "terminal"
        assert result.code == "report.scope_unauthorized"

    def test_generate_task_uses_authorized_principal_without_ambient_tenant_for_broad_scope(self):
        schedule = SimpleNamespace(
            pk=3,
            report=SimpleNamespace(legacy_designer_grandfathered=False),
            is_active=True,
            save=Mock(),
        )
        tenant_a = SimpleNamespace(pk=1, id=1)
        tenant_b = SimpleNamespace(pk=2, id=2)
        authorized_principal_id = 99
        expected_result = SimpleNamespace(status="success")
        manager = Mock()
        manager.get.return_value = schedule

        with (
            patch("core.tasks.reports.ScheduledReport.objects", manager),
            patch("core.tasks.reports.report_designer_probe", return_value=SimpleNamespace(active=True)),
            patch("core.tasks.reports._resolve_report_scope", return_value=(tenant_a, [tenant_a, tenant_b])),
            patch("core.tasks.reports._resolve_scope_authorization", return_value=authorized_principal_id),
            patch("core.tasks.reports._scope_requires_authorization", return_value=True),
            patch("core.tasks.reports.TaskContext") as task_context,
            patch("core.tasks.reports._process_scheduled_report", return_value=expected_result) as process_report,
        ):
            result = generate_scheduled_report_task(schedule.pk)

        assert result is expected_result
        task_context.assert_called_once_with(
            tenant_id=None,
            user_id=authorized_principal_id,
            all_accessible=True,
            operation="reports.generate",
        )
        process_report.assert_called_once_with(schedule, tenant_a, [tenant_a, tenant_b])

    @staticmethod
    def _persisted_template_state(**overrides):
        state = {
            "name": "template",
            "description": "",
            "tenant_id": None,
            "report_type": ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            "included_columns": [],
            "include_summary_cards": True,
            "include_distribution_chart": False,
            "group_by_field": "",
            "style_preset": "default",
            "advanced_mode": False,
            "template_content": "",
            "legacy_designer_grandfathered": False,
        }
        state.update(overrides)
        return state

    def test_report_template_clean_and_save_enforce_disabled_designer_without_database(self):
        active_template = ReportTemplate(name="active", included_columns=[])
        with patch("extras.models.report_designer_probe", return_value=SimpleNamespace(active=True)):
            assert active_template.clean() is None

        existing_query = Mock()
        existing_query.values.return_value.first.return_value = self._persisted_template_state(
            name="unchanged",
            advanced_mode=True,
            template_content="<p>old</p>",
            legacy_designer_grandfathered=True,
        )
        unchanged = ReportTemplate(
            pk=5,
            name="unchanged",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=[],
            advanced_mode=True,
            template_content="<p>old</p>",
            legacy_designer_grandfathered=True,
        )
        with (
            patch.object(ReportTemplate._base_manager, "filter", return_value=existing_query),
            patch("extras.models.report_designer_probe", return_value=SimpleNamespace(active=False)),
        ):
            assert unchanged.clean() is None
            unchanged.template_content = "<p>new</p>"
            with pytest.raises(ValidationError, match="editing a grandfathered"):
                unchanged.clean()

        forged = ReportTemplate(name="forged", included_columns=[], legacy_designer_grandfathered=True)
        with pytest.raises(ValidationError, match="cannot be forged"):
            forged.save()

        changed_marker = ReportTemplate(
            pk=5,
            name="marker",
            included_columns=[],
            legacy_designer_grandfathered=False,
        )
        marker_query = Mock()
        marker_query.values.return_value.first.return_value = {
            "advanced_mode": False,
            "template_content": "",
            "legacy_designer_grandfathered": True,
        }
        with patch.object(ReportTemplate._base_manager, "filter", return_value=marker_query):
            with pytest.raises(ValidationError, match="cannot be changed"):
                changed_marker.save()

    def test_flag_off_rejects_changes_to_existing_non_grandfathered_html_on_clean_and_save(self):
        existing_query = Mock()
        existing_query.values.return_value.first.return_value = self._persisted_template_state(
            name="custom html",
            template_content="<p>old</p>",
        )
        template = ReportTemplate(
            pk=5,
            name="custom html",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=[],
            template_content="<p>new</p>",
        )
        with (
            patch.object(ReportTemplate._base_manager, "filter", return_value=existing_query),
            patch("extras.models.report_designer_probe", return_value=SimpleNamespace(active=False)),
        ):
            with pytest.raises(ValidationError, match="saving custom HTML"):
                template.clean()
            with patch.object(ChangeLoggingMixin, "save") as parent_save:
                with pytest.raises(ValidationError, match="saving custom HTML"):
                    template.save()
            parent_save.assert_not_called()

    def test_flag_off_rejects_metadata_only_edits_to_grandfathered_template(self):
        existing_query = Mock()
        existing_query.values.return_value.first.return_value = self._persisted_template_state(
            name="grandfathered",
            description="original",
            template_content="<p>legacy</p>",
            legacy_designer_grandfathered=True,
        )
        template = ReportTemplate(
            pk=5,
            name="grandfathered",
            description="edited",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=[],
            template_content="<p>legacy</p>",
            legacy_designer_grandfathered=True,
        )
        with (
            patch.object(ReportTemplate._base_manager, "filter", return_value=existing_query),
            patch("extras.models.report_designer_probe", return_value=SimpleNamespace(active=False)),
            patch.object(ChangeLoggingMixin, "save") as parent_save,
        ):
            with pytest.raises(ValidationError, match="editing a grandfathered"):
                template.save()
        parent_save.assert_not_called()

    def test_flag_off_allows_noop_save_of_grandfathered_template(self):
        existing_query = Mock()
        existing_query.values.return_value.first.return_value = self._persisted_template_state(
            name="grandfathered",
            template_content="<p>legacy</p>",
            legacy_designer_grandfathered=True,
        )
        template = ReportTemplate(
            pk=5,
            name="grandfathered",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=[],
            template_content="<p>legacy</p>",
            legacy_designer_grandfathered=True,
        )
        with (
            patch.object(ReportTemplate._base_manager, "filter", return_value=existing_query),
            patch("extras.models.report_designer_probe", return_value=SimpleNamespace(active=False)),
            patch.object(ChangeLoggingMixin, "save") as parent_save,
        ):
            template.save()
        parent_save.assert_called_once()

    def test_flag_on_allows_editing_existing_non_grandfathered_html(self):
        existing_query = Mock()
        existing_query.values.return_value.first.return_value = self._persisted_template_state(
            name="custom html",
            template_content="<p>old</p>",
        )
        template = ReportTemplate(
            pk=5,
            name="custom html",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            included_columns=[],
            template_content="<p>new</p>",
        )
        with (
            patch.object(ReportTemplate._base_manager, "filter", return_value=existing_query),
            patch("extras.models.report_designer_probe", return_value=SimpleNamespace(active=True)),
            patch.object(ChangeLoggingMixin, "save") as parent_save,
        ):
            template.save()
        parent_save.assert_called_once()

    def test_scheduled_report_scope_helpers_and_approval_are_database_independent(self):
        tenant_a = SimpleNamespace(pk=1)
        tenant_b = SimpleNamespace(pk=2)
        report_filter_manager = SimpleNamespace(all=lambda: [tenant_b])
        schedule = SimpleNamespace(
            filter_tenants=SimpleNamespace(all=lambda: []),
            report_id=99,
            report=SimpleNamespace(filter_tenants=report_filter_manager, tenant=tenant_a),
            tenant=tenant_a,
        )
        schedule.effective_scope_tenant_ids = lambda: ScheduledReport.effective_scope_tenant_ids(schedule)
        assert ScheduledReport.persisted_scope_tenant_ids(schedule) == [2]
        assert ScheduledReport.effective_scope_tenant_ids(schedule) == [2]
        assert ScheduledReport.scope_requires_authorization(schedule) is True

        schedule.filter_tenants = SimpleNamespace(all=lambda: [tenant_a, tenant_b])
        assert ScheduledReport.persisted_scope_tenant_ids(schedule) == [1, 2]
        assert ScheduledReport.effective_scope_tenant_ids(schedule) == [1, 2]
        assert ScheduledReport.scope_requires_authorization(schedule) is True

        schedule.filter_tenants = SimpleNamespace(all=lambda: [])
        schedule.report = SimpleNamespace(filter_tenants=SimpleNamespace(all=lambda: []), tenant=None)
        schedule.tenant = tenant_a
        assert ScheduledReport.persisted_scope_tenant_ids(schedule) == []
        assert ScheduledReport.effective_scope_tenant_ids(schedule) == [1]
        assert ScheduledReport.scope_requires_authorization(schedule) is False

        actor = Mock(is_active=False)
        with pytest.raises(PermissionDenied):
            ScheduledReportScopeAuthorization.approve(schedule, actor)
        actor.is_active = True
        actor.has_perm.return_value = True
        schedule.scope_requires_authorization = lambda: False
        with pytest.raises(ValidationError, match="does not need"):
            ScheduledReportScopeAuthorization.approve(schedule, actor)

        schedule.scope_requires_authorization = lambda: True
        schedule.effective_scope_tenant_ids = lambda: [1, 2]
        authorization = SimpleNamespace(scope_tenant_ids=[1, 2])
        authorization_manager = Mock()
        authorization_manager.update_or_create.return_value = (authorization, True)
        with patch.object(ScheduledReportScopeAuthorization, "objects", authorization_manager):
            assert ScheduledReportScopeAuthorization.approve(schedule, actor) is authorization
        authorization_manager.update_or_create.assert_called_once_with(
            scheduled_report=schedule,
            defaults={"authorized_by": actor, "scope_tenant_ids": [1, 2]},
        )

    def test_report_scope_uses_persisted_scope_without_owner_fallback(self):
        tenant_a = SimpleNamespace(pk=1)
        tenant_b = SimpleNamespace(pk=2)
        schedule = SimpleNamespace(
            pk=17,
            tenant=tenant_a,
            report=SimpleNamespace(tenant=tenant_a),
            persisted_scope_tenant_ids=lambda: [2],
            effective_scope_tenant_ids=lambda: [1, 2],
        )
        query = Mock()
        query.order_by.return_value = [tenant_b]
        with patch.object(Tenant.all_objects, "filter", return_value=query) as filter_scope:
            assert _resolve_report_scope(schedule) == (tenant_a, [tenant_b])
        filter_scope.assert_called_once_with(pk__in=[2])

        owner_only = SimpleNamespace(
            pk=18,
            tenant=tenant_a,
            report=SimpleNamespace(tenant=tenant_a),
            persisted_scope_tenant_ids=lambda: [],
            effective_scope_tenant_ids=lambda: [1],
        )
        assert _resolve_report_scope(owner_only) == (tenant_a, [])

    def test_scheduled_reports_probe_requires_both_operator_and_row_gates(self):
        with (
            patch("extras.apps.report_designer_probe", return_value=ActivationState(True, True)),
            patch("extras.apps.object_enabled_probe", return_value=lambda: ActivationState(True, True)),
        ):
            assert _scheduled_reports_probe() == ActivationState(True, True)
        with (
            patch("extras.apps.report_designer_probe", return_value=ActivationState(False, True)),
            patch("extras.apps.object_enabled_probe", return_value=lambda: ActivationState(True, True)),
        ):
            assert _scheduled_reports_probe() == ActivationState(False, True)
        with (
            patch("extras.apps.report_designer_probe", return_value=ActivationState(True, False)),
            patch("extras.apps.object_enabled_probe", return_value=lambda: ActivationState(False, True)),
        ):
            assert _scheduled_reports_probe() == ActivationState(False, True)

    @override_settings(FEATURE_REPORT_DESIGNER=True, REPORT_DESIGNER_ENABLED=False)
    def test_report_views_cover_preview_permissions_and_rendering_seams(self):
        preview = ReportTemplatePreviewView()
        preview.request = SimpleNamespace(user=Mock(has_perm=Mock(side_effect=[False, True])))
        assert preview.has_permission() is True
        preview.request.user.has_perm.reset_mock(side_effect=True)
        preview.request.user.has_perm.return_value = True
        assert preview.has_permission() is True

        request = RequestFactory().post(
            "/preview/",
            {
                "report_type": "asset_summary",
                "included_columns": ["asset_tag"],
                "include_summary_cards": "true",
                "advanced_mode": "1",
                "template_content": "<h1>{{ report_name }}</h1>",
                "description": "preview",
            },
        )
        request.user = SimpleNamespace(is_superuser=False)
        with (
            patch("extras.views.get_current_tenant", return_value=None),
            patch(
                "core.reports.build_report_context",
                return_value=([], [], [], {}, "", {"report_name": "preview"}),
            ),
            patch("extras.views.render_report_html", return_value="<h1>preview</h1>") as render,
        ):
            response = preview.post(request)
        assert response.status_code == 200
        assert response.content == b"<h1>preview</h1>"
        render.assert_called_once()

        with patch("core.reports.build_report_context", side_effect=PermissionError):
            response = preview.post(request)
        assert response.status_code == 403

    def test_preview_rejects_unknown_columns_before_building_report_context(self):
        request = RequestFactory().post(
            "/preview/",
            {
                "report_type": "asset_summary",
                "included_columns": ["asset_tag", "not_published"],
            },
        )
        request.user = SimpleNamespace(is_superuser=False)
        with (
            patch("extras.views.get_current_tenant", return_value=None),
            patch(
                "core.reports.build_report_context",
                return_value=([], [], [], {}, "", {"report_name": "preview"}),
            ) as build_context,
            patch("extras.views.render_report_html", return_value="<h1>preview</h1>"),
        ):
            response = ReportTemplatePreviewView().post(request)

        assert response.status_code == 400
        assert b"Invalid report template configuration." in response.content
        assert b"Unknown report column key" in response.content
        assert b"not_published" in response.content
        build_context.assert_not_called()

    def test_report_template_detail_and_download_cover_csv_html_and_pdf(self):
        detail = ReportTemplateDetailView()
        detail_template = SimpleNamespace(name="Legacy", legacy_designer_grandfathered=True, schedules=Mock())
        detail.get_object = Mock(return_value=detail_template)
        with patch("extras.views.ObjectDetailView.get_context_data", return_value={}):
            context = detail.get_context_data()
        assert context["legacy_designer_notice"] is True

        template = SimpleNamespace(
            name="My Report",
            filter_tenants=SimpleNamespace(all=lambda: []),
            report_type="asset_summary",
            advanced_mode=False,
            template_content="",
        )
        build_context = (["Asset Tag"], [{"Asset Tag": "A-1"}], [], {}, "", {"report_name": "My Report"})
        with (
            patch("extras.views.get_object_or_404", return_value=template),
            patch("extras.views.get_current_tenant", return_value=None),
            patch("core.reports.build_report_context", return_value=build_context),
            patch("extras.views.render_report_csv", return_value="Asset Tag\nA-1\n"),
        ):
            csv_response = ReportTemplateDownloadView().get(SimpleNamespace(GET={"format": "csv"}), 1)
        assert csv_response.status_code == 200
        assert 'filename="my_report_' in csv_response["Content-Disposition"]

        with (
            patch("extras.views.get_object_or_404", return_value=template),
            patch("extras.views.get_current_tenant", return_value=None),
            patch("core.reports.build_report_context", return_value=build_context),
            patch("extras.views.render_report_html", return_value="<p>html</p>"),
        ):
            html_response = ReportTemplateDownloadView().get(SimpleNamespace(GET={"format": "html"}), 1)
        assert html_response["Content-Type"].startswith("text/html")

        with (
            patch("extras.views.get_object_or_404", return_value=template),
            patch("extras.views.get_current_tenant", return_value=None),
            patch("core.reports.build_report_context", return_value=build_context),
            patch("extras.views.render_report_html", return_value="<p>html</p>"),
            patch("core.reports.exporters.report_pdf_bytes", return_value=b"pdf"),
        ):
            pdf_response = ReportTemplateDownloadView().get(SimpleNamespace(GET={"format": "pdf", "print": "true"}), 1)
        assert pdf_response["Content-Type"].startswith("application/pdf")
        assert pdf_response["Content-Disposition"].startswith("inline;")


class ReportDesignerFilterTenantWriteTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Filter Tenant A", slug="filter-tenant-a")
        self.tenant_b = Tenant.objects.create(name="Filter Tenant B", slug="filter-tenant-b")
        self.template = ReportTemplate.objects.create(
            name="Grandfathered Filter Template",
            report_type=ReportTemplate.REPORT_TYPE_ASSET_SUMMARY,
            tenant=self.tenant_a,
        )
        ReportTemplate._base_manager.filter(pk=self.template.pk).update(legacy_designer_grandfathered=True)
        self.template.refresh_from_db()
        self.template.filter_tenants.add(self.tenant_a)
        self.admin = SimpleNamespace(is_superuser=True, is_staff=True)
        self.current_user = patch("extras.forms.get_current_user", return_value=self.admin)
        self.current_user.start()
        self.addCleanup(self.current_user.stop)

    def _form(self, filter_tenant):
        return ReportTemplateForm(
            data={
                "name": self.template.name,
                "description": self.template.description,
                "report_type": self.template.report_type,
                "included_columns": [],
                "include_summary_cards": "on",
                "include_distribution_chart": "",
                "group_by_field": "",
                "style_preset": self.template.style_preset,
                "advanced_mode": "",
                "template_content": "",
                "tenant": str(self.tenant_a.pk),
                "filter_tenants": [str(filter_tenant.pk)],
            },
            instance=ReportTemplate.objects.get(pk=self.template.pk),
        )

    @override_settings(FEATURE_REPORT_DESIGNER=False, REPORT_DESIGNER_ENABLED=False)
    def test_flag_off_blocks_grandfathered_filter_tenant_change_through_form_save(self):
        form = self._form(self.tenant_b)
        assert form.is_valid()
        with pytest.raises(ValidationError, match="grandfathered"):
            form.save()
        assert set(self.template.filter_tenants.values_list("pk", flat=True)) == {self.tenant_a.pk}

    @override_settings(FEATURE_REPORT_DESIGNER=False, REPORT_DESIGNER_ENABLED=False)
    def test_flag_off_blocks_grandfathered_filter_tenant_change_through_deferred_save_m2m(self):
        form = self._form(self.tenant_b)
        assert form.is_valid()
        form.save(commit=False)
        with pytest.raises(ValidationError, match="grandfathered"):
            form.save_m2m()
        assert set(self.template.filter_tenants.values_list("pk", flat=True)) == {self.tenant_a.pk}

    @override_settings(FEATURE_REPORT_DESIGNER=False, REPORT_DESIGNER_ENABLED=False)
    def test_flag_off_allows_noop_grandfathered_filter_tenant_save_m2m(self):
        form = self._form(self.tenant_a)
        assert form.is_valid()
        form.save(commit=False)
        form.save_m2m()
        assert set(self.template.filter_tenants.values_list("pk", flat=True)) == {self.tenant_a.pk}

    @override_settings(FEATURE_REPORT_DESIGNER=True, REPORT_DESIGNER_ENABLED=False)
    def test_flag_on_allows_grandfathered_filter_tenant_change_through_form_save(self):
        form = self._form(self.tenant_b)
        assert form.is_valid()
        form.save()
        assert set(self.template.filter_tenants.values_list("pk", flat=True)) == {self.tenant_b.pk}
