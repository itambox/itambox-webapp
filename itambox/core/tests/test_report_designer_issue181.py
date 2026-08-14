from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import pytest
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings

from core.reports.rendering import render_report_html
from core.tasks.reports import _render_report_output, _resolve_scope_authorization
from extras.forms import ReportTemplateForm
from extras.models import ReportTemplate, ScheduledReport


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
