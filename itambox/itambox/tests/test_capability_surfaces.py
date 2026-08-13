"""U2/U7: the surfaces the registry drives — markers, diagnostics, and OpenAPI.

The registry is only worth having if the maturity a domain declares is the
maturity a user, an operator, and an API client all see. These tests pin those
three readers to the same source.
"""

from collections import namedtuple
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.http import Http404
from django.template.loader import render_to_string
from django.test import RequestFactory, override_settings

from itambox.api.openapi import CapabilityAwareAutoSchema
from itambox.capabilities import BETA, EXPERIMENTAL, STABLE, registry
from itambox.tests.capability_harness import deactivated, probe_failing
from itambox.views.generic.capability_notices import capability_notice

#: Stand-ins for a DRF viewset and its queryset: the schema class only ever
#: reads ``view.queryset.model``, so there is nothing else to imitate.
_StubQuerySet = namedtuple("_StubQuerySet", "model")
_StubView = namedtuple("_StubView", "queryset")
_APP_ROOT = Path(__file__).resolve().parents[2]


class TestSurfaceMarker:
    """U2: a non-Stable model carries its owning capability's marker."""

    def test_a_beta_owned_model_yields_a_notice(self):
        from extras.models import WebhookEndpoint

        notice = capability_notice(WebhookEndpoint)
        assert notice["key"] == "automation.webhooks"
        assert notice["maturity"] == BETA
        assert notice["title"]
        assert notice["docs_url"].endswith(".md")
        assert notice["limitations"]

    def test_a_stable_owned_model_yields_no_notice(self):
        from procurement.models import PurchaseOrder

        assert capability_notice(PurchaseOrder) is None

    def test_an_unowned_model_yields_no_notice(self):
        from assets.models import Asset

        assert capability_notice(Asset) is None

    def test_an_experimental_marker_names_its_own_grade(self):
        notice = _notice_for_key("platform.plugins")
        assert notice["maturity"] == EXPERIMENTAL

    def test_a_notice_never_carries_the_probe_or_a_value(self):
        from extras.models import WebhookEndpoint

        notice = capability_notice(WebhookEndpoint)
        assert "activation_probe" not in notice
        assert set(notice) == {"key", "title", "maturity", "activation", "docs_url", "limitations"}

    def test_a_deactivated_capability_still_marks_its_surface(self):
        """Inactive is not invisible: the grade is a property of the contract."""
        from extras.models import WebhookEndpoint

        with deactivated("automation.webhooks"):
            assert capability_notice(WebhookEndpoint)["maturity"] == BETA

    def test_the_notice_survives_a_failing_probe(self):
        from extras.models import WebhookEndpoint

        with probe_failing("automation.webhooks"):
            assert capability_notice(WebhookEndpoint)["key"] == "automation.webhooks"


class TestBannerTemplate:
    def test_the_banner_names_the_capability_and_links_its_document(self):
        html = render_to_string(
            "generic/includes/beta_banner.html",
            {"capability_notice": _notice_for_key("automation.webhooks")},
        )
        assert "Beta" in html
        assert "Webhook" in html
        assert "capability-registry" in html or "development/" in html

    def test_the_contract_link_is_excluded_from_boost(self):
        """The docs link points outside the app shell: it must never be a
        boosted HTMX request (global hx-boost on <body> would otherwise swap
        the standalone docs page into the app layout and break the UI).
        Defense in depth next to the central boost-guard (static/src/boost-guard.ts).
        """
        html = render_to_string(
            "generic/includes/beta_banner.html",
            {"capability_notice": _notice_for_key("automation.webhooks")},
        )
        assert 'hx-boost="false"' in html

    def test_the_banner_renders_the_declared_limitations(self):
        notice = _notice_for_key("automation.webhooks")
        html = render_to_string("generic/includes/beta_banner.html", {"capability_notice": notice})
        assert notice["limitations"][0] in html

    def test_the_beta_banner_is_a_polite_live_region_with_a_label(self):
        html = render_to_string(
            "generic/includes/beta_banner.html",
            {"capability_notice": _notice_for_key("automation.webhooks")},
        )
        assert 'role="status"' in html
        assert 'aria-live="polite"' in html
        assert 'aria-atomic="true"' in html
        assert 'aria-labelledby="beta-module-banner-title"' in html
        assert 'id="beta-module-banner-title"' in html

    def test_the_experimental_banner_is_announced_without_a_dismiss_control(self):
        html = render_to_string(
            "generic/includes/beta_banner.html",
            {"capability_notice": _notice_for_key("platform.plugins")},
        )
        assert 'role="status"' in html
        assert 'aria-live="polite"' in html
        assert 'data-maturity="experimental"' in html
        assert "btn-close" not in html

    def test_the_maturity_badge_exposes_text_and_an_accessible_name(self):
        html = render_to_string(
            "generic/includes/capability_badge.html",
            {"capability_notice": _notice_for_key("platform.plugins")},
        )
        assert "aria-label=" in html
        assert 'aria-hidden="true"' not in html
        assert "Experimental" in html

    def test_the_banner_is_silent_without_a_notice(self):
        html = render_to_string("generic/includes/beta_banner.html", {})
        assert html.strip() == ""

    def test_the_legacy_flag_still_renders_a_banner(self):
        html = render_to_string("generic/includes/beta_banner.html", {"is_beta_module": True})
        assert "Beta" in html

    def test_the_badge_names_the_grade(self):
        html = render_to_string(
            "generic/includes/capability_badge.html",
            {"capability_notice": _notice_for_key("platform.plugins")},
        )
        assert "Experimental" in html

    def test_the_badge_is_silent_without_a_notice(self):
        html = render_to_string("generic/includes/capability_badge.html", {})
        assert html.strip() == ""


class TestAccessibilityTemplateContracts:
    def test_mobile_theme_controls_are_named_buttons_with_hidden_icons(self):
        html = "\n".join(
            (
                (_APP_ROOT / "templates" / "layout.html").read_text(encoding="utf-8"),
                (_APP_ROOT / "templates" / "global_includes" / "_topbar.html").read_text(encoding="utf-8"),
            )
        )
        assert html.count("aria-label=\"{% translate 'Enable dark mode' %}\"") >= 2
        assert html.count("aria-label=\"{% translate 'Enable light mode' %}\"") >= 2
        assert 'mdi-weather-night" aria-hidden="true"' in html
        assert 'mdi-weather-sunny" aria-hidden="true"' in html
        assert html.count('type="button"') >= 2

    def test_shared_toast_and_modal_errors_are_announced(self):
        toast = (_APP_ROOT / "templates" / "global_includes" / "_toast.html").read_text(encoding="utf-8")
        modal = (_APP_ROOT / "templates" / "generic" / "includes" / "add_stock_modal.html").read_text(encoding="utf-8")
        for html in (toast, modal):
            assert 'role="alert"' in html
            assert 'aria-live="assertive"' in html
            assert 'aria-atomic="true"' in html


@pytest.mark.django_db
class TestOperatorDiagnostics:
    """U7: class, mode, current state, source, and value presence — nothing else."""

    def test_the_command_reports_every_capability(self):
        output = _run_command()
        for capability in registry.all():
            assert capability.key in output

    def test_the_command_reports_mode_source_and_state(self):
        output = _run_command()
        assert "opt-in" in output
        assert "object-enabled" in output
        assert "operator-flag" in output
        assert "configured" in output

    def test_the_command_reports_value_presence_not_the_value(self):
        with override_settings(PLUGINS=["demo_plugin_secret"]):
            output = _run_command()
        assert "demo_plugin_secret" not in output

    def test_a_failing_probe_is_reported_by_type_only(self):
        with probe_failing("automation.webhooks"):
            output = _run_command()
        assert "RuntimeError" in output
        assert "hunter2" not in output

    def test_the_command_can_emit_json_rows(self):
        rows = _run_command("--format", "json")
        assert '"value_present"' in rows
        assert '"activation_probe"' not in rows


class TestOpenAPIMaturity:
    """The schema publishes the same grade the UI shows."""

    def test_an_owned_operation_is_annotated(self):
        from extras.models import WebhookEndpoint

        operation = _operation_for(WebhookEndpoint)
        assert operation["x-itambox-maturity"] == BETA

    def test_a_stable_owned_operation_is_annotated_stable(self):
        from procurement.models import PurchaseOrder

        assert _operation_for(PurchaseOrder)["x-itambox-maturity"] == STABLE

    def test_an_unowned_operation_is_not_annotated(self):
        from assets.models import Asset

        assert "x-itambox-maturity" not in _operation_for(Asset)

    def test_a_view_without_a_queryset_is_not_annotated(self):
        schema = CapabilityAwareAutoSchema()
        schema.view = object()
        assert schema.capability_maturity() is None

    def test_the_schema_class_is_wired_into_settings(self):
        from django.conf import settings

        assert settings.REST_FRAMEWORK["DEFAULT_SCHEMA_CLASS"].endswith("CapabilityAwareAutoSchema")

    def test_every_generated_scim_operation_is_annotated_beta(self):
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        operations = [
            operation
            for path, path_item in schema["paths"].items()
            if "/scim/v2/" in path
            for method, operation in path_item.items()
            if method.lower() in {"get", "post", "put", "patch", "delete"}
        ]

        assert len(operations) == 26
        assert {operation.get("x-itambox-maturity") for operation in operations} == {BETA}


@pytest.mark.serial_only
class TestNavigationMaturity:
    def test_procurement_navigation_is_not_marked_beta(self):
        from core.navigation.menu import OPERATIONS_MENU

        procurement = next(group for group in OPERATIONS_MENU.groups if str(group.label) == "Procurement")
        assert procurement.beta is False

    @override_settings(REPORT_DESIGNER_ENABLED=False)
    def test_report_designer_navigation_is_hidden_when_inactive(self):
        from core.navigation.menu import MONITORING_MENU

        reporting = next(group for group in MONITORING_MENU.groups if str(group.label) == "Reporting")
        designer = next(item for item in reporting.items if str(item.link_text) == "Report Templates")
        scheduled = next(item for item in reporting.items if str(item.link_text) == "Scheduled Reports")
        assert designer.condition is not None
        assert designer.condition(None) is False
        assert scheduled.condition is not None
        assert scheduled.condition(None) is False

    @override_settings(REPORT_DESIGNER_ENABLED=True)
    def test_report_designer_navigation_is_visible_when_active(self):
        from core.navigation.menu import MONITORING_MENU

        reporting = next(group for group in MONITORING_MENU.groups if str(group.label) == "Reporting")
        designer = next(item for item in reporting.items if str(item.link_text) == "Report Templates")
        scheduled = next(item for item in reporting.items if str(item.link_text) == "Scheduled Reports")
        assert designer.condition(None) is True
        assert scheduled.condition(None) is True


DESIGNER_VIEWS = (
    "ReportTemplateListView",
    "ReportTemplateDetailView",
    "ReportTemplateCreateView",
    "ReportTemplateUpdateView",
    "ReportTemplateDeleteView",
    "ReportTemplateBulkDeleteView",
    "ReportTemplatePreviewView",
    "ReportTemplateDownloadView",
)

#: The pk-less designer routes. The detail, edit, delete, and download routes
#: would 404 on a missing row as well, so an open gate is only unambiguous on
#: these two; the closed direction is proven for all eight.
UNAMBIGUOUS_DESIGNER_VIEWS = ("ReportTemplateListView", "ReportTemplateBulkDeleteView")

SCHEDULED_REPORT_VIEWS = (
    "ScheduledReportListView",
    "ScheduledReportCreateView",
    "ScheduledReportUpdateView",
    "ScheduledReportDeleteView",
    "ScheduledReportBulkDeleteView",
    "ReportTriggerImmediateView",
)


@pytest.mark.django_db
class TestReportDesignerOptIn:
    """The report designer is opt-in, and one mechanism decides it.

    ``ITAMBOX_REPORT_DESIGNER_ENABLED`` is the operator flag; the registry reads
    it and the routes read the registry. A flag the probe consults but no route
    honours would be a label, not an activation mechanism.
    """

    def test_the_flag_exists_and_ships_off(self):
        assert settings.REPORT_DESIGNER_ENABLED is False

    def test_a_fresh_deployment_reports_the_designer_inactive(self):
        assert registry.is_active("reporting.designer") is False

    @override_settings(REPORT_DESIGNER_ENABLED=True)
    def test_an_operator_who_sets_the_flag_activates_the_designer(self):
        state = registry.state("reporting.designer")
        assert (state.active, state.value_present) == (True, True)

    @pytest.mark.parametrize("view_name", DESIGNER_VIEWS)
    def test_every_designer_route_names_the_capability_that_gates_it(self, view_name):
        assert _designer_view(view_name).capability_key == "reporting.designer"

    @pytest.mark.parametrize("view_name", DESIGNER_VIEWS)
    def test_every_designer_route_is_closed_on_a_fresh_deployment(self, view_name):
        assert _gate_outcome(view_name) == "Http404"

    @override_settings(REPORT_DESIGNER_ENABLED=True)
    @pytest.mark.parametrize("view_name", UNAMBIGUOUS_DESIGNER_VIEWS)
    def test_an_enabled_deployment_keeps_its_designer_routes(self, view_name):
        assert _gate_outcome(view_name) != "Http404"

    @override_settings(REPORT_DESIGNER_ENABLED=True)
    def test_the_route_follows_the_registry_rather_than_re_reading_the_flag(self):
        """One mechanism: with the flag on but the capability off, the route is closed."""
        with deactivated("reporting.designer"):
            assert _gate_outcome("ReportTemplateListView") == "Http404"

    def test_a_switched_off_designer_is_still_graded_beta(self):
        from extras.models import ReportTemplate

        assert capability_notice(ReportTemplate)["maturity"] == BETA

    def test_the_diagnostics_row_reports_the_flag_without_printing_it(self):
        rows = {row["key"]: row for row in registry.diagnostics()}
        row = rows["reporting.designer"]
        assert (row["active"], row["value_present"], row["activation"]) == (False, False, "opt-in")
        assert row["activation_source"] == "operator-flag"

    def test_the_curated_report_catalogue_is_untouched_by_the_designer_flag(self):
        """The Stable report path is a different capability and stays on."""
        assert registry.is_active("reporting.curated") is True


@pytest.mark.django_db
class TestScheduledReportsFollowDesignerOptIn:
    """Scheduled reports share the designer's operator activation boundary."""

    @pytest.mark.parametrize("view_name", SCHEDULED_REPORT_VIEWS)
    def test_every_scheduled_report_route_names_the_designer_capability(self, view_name):
        assert _designer_view(view_name).capability_key == "reporting.designer"

    @pytest.mark.parametrize("view_name", SCHEDULED_REPORT_VIEWS)
    def test_every_scheduled_report_route_is_closed_when_the_designer_is_off(self, view_name):
        assert _gate_outcome(view_name) == "Http404"

    @override_settings(REPORT_DESIGNER_ENABLED=True)
    @pytest.mark.parametrize("view_name", ("ScheduledReportListView", "ScheduledReportBulkDeleteView"))
    def test_enabled_designer_keeps_unambiguous_scheduled_routes_open(self, view_name):
        assert _gate_outcome(view_name) != "Http404"


def _designer_view(view_name):
    from extras import views

    return getattr(views, view_name)


def _gate_outcome(view_name):
    """What the capability gate did, named rather than rendered.

    Returns ``"Http404"`` when the gate closed the route, and the name of
    whatever happened next otherwise -- a permission error, a wrong method, a
    response. The test asserts only on the gate, so downstream behaviour is
    deliberately not modelled. No URL kwargs are supplied: the gate runs before
    anything reads them, so needing one would itself mean the gate ran late.
    """
    from django.contrib.auth import get_user_model

    request = RequestFactory().get("/")
    request.user = get_user_model()(username="capability-probe", is_active=True)
    try:
        return type(_designer_view(view_name).as_view()(request)).__name__
    except Http404:
        return "Http404"
    except Exception as exc:
        return type(exc).__name__


def _notice_for_key(key):
    capability = registry.get(key)
    return {
        "key": capability.key,
        "title": capability.title,
        "maturity": capability.maturity,
        "activation": capability.activation,
        "docs_url": capability.docs_url,
        "limitations": capability.limitations,
    }


def _operation_for(model):
    schema = CapabilityAwareAutoSchema()
    schema.view = _StubView(_StubQuerySet(model))
    operation = {"operationId": "stub"}
    schema.annotate_capability_maturity(operation)
    return operation


def _run_command(*args):
    stdout = StringIO()
    call_command("capabilities", *args, stdout=stdout)
    return stdout.getvalue()
