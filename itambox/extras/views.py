import datetime
import json

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ObjectDoesNotExist, PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.html import escape
from django.utils.http import urlencode
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView, View
from django_tables2 import RequestConfig

from assets.tables import AssetTable  # Import AssetTable
from core.managers import get_current_tenant
from core.reports.rendering import render_report_csv, render_report_html
from itambox.capabilities import registry
from itambox.panels import Panel
from itambox.utils import get_model_viewname, get_paginate_count  # Import the utility function
from itambox.views.generic import (
    ObjectBulkDeleteView,
    ObjectBulkEditView,
    ObjectDeleteView,
    ObjectDetailView,
    ObjectEditView,
    ObjectListView,
)
from itambox.views.generic.mixins import CapabilityRequiredMixin
from itambox.views.generic.utils import safe_return_url
from users.models import UserPreference  # Import UserPreference

from .filters import CustomFieldFilterSet, CustomFieldsetFilterSet, SavedFilterFilterSet, TagFilter
from .forms import (
    CustomFieldFilterForm,
    CustomFieldForm,
    CustomFieldsetFilterForm,
    CustomFieldsetForm,
    SavedFilterFilterForm,
    SavedFilterForm,
    TagFilterForm,
    TagForm,
)
from .models import CustomField, CustomFieldset, SavedFilter, Tag
from .tables import CustomFieldsetTable, CustomFieldTable, SavedFilterTable, TagTable


class TagDetailView(ObjectDetailView):
    queryset = Tag.objects.all()
    template_name = "extras/tags/tag_detail.html"

    layout = (((Panel("info", _("Tag Details")),),),)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tag = self.object

        # Fetch related assets using the related_name from Asset.tags
        related_assets = tag.assets.all()

        # Create and configure the assets table
        assets_table = AssetTable(related_assets, request=self.request)
        # Disable pagination for related table
        assets_table.configure(self.request, paginate=False)

        context["assets_table"] = assets_table
        return context


class TagCreateView(ObjectEditView):
    model_form = TagForm
    template_name = "generic/object_edit.html"
    default_return_url = "extras:tag_list"


class TagUpdateView(ObjectEditView):
    queryset = Tag.objects.all()
    model_form = TagForm
    template_name = "generic/object_edit.html"
    default_return_url = "extras:tag_list"


class TagDeleteView(ObjectDeleteView):
    queryset = Tag.objects.all()
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("extras:tag_list")


# Refactor tag_list to CBV
class TagListView(ObjectListView):
    queryset = Tag.objects.all()
    filterset = TagFilter
    filterset_form = TagFilterForm  # Assuming TagFilterForm exists
    table = TagTable
    action_buttons = ("add",)  # Add create button
    template_name = "generic/object_list.html"  # Use base template


class TagBulkEditView(ObjectBulkEditView):
    queryset = Tag.objects.all()


class TagBulkDeleteView(ObjectBulkDeleteView):
    queryset = Tag.objects.all()


# Custom Fields
class CustomFieldListView(ObjectListView):
    queryset = CustomField.objects.all()
    filterset = CustomFieldFilterSet
    filterset_form = CustomFieldFilterForm
    table = CustomFieldTable
    action_buttons = ("add",)


class CustomFieldDetailView(ObjectDetailView):
    queryset = CustomField.objects.all()

    layout = (((Panel("info", _("Custom Field Details")),),),)


class CustomFieldEditView(ObjectEditView):
    queryset = CustomField.objects.all()
    model = CustomField
    model_form = CustomFieldForm
    template_name = "generic/object_edit.html"
    default_return_url = "extras:customfield_list"


class CustomFieldDeleteView(ObjectDeleteView):
    queryset = CustomField.objects.all()
    model = CustomField
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("extras:customfield_list")


class CustomFieldBulkEditView(ObjectBulkEditView):
    queryset = CustomField.objects.all()


class CustomFieldBulkDeleteView(ObjectBulkDeleteView):
    queryset = CustomField.objects.all()


# Custom Fieldsets
class CustomFieldsetListView(ObjectListView):
    queryset = CustomFieldset.objects.annotate(fields_count=Count("fields"))
    filterset = CustomFieldsetFilterSet
    filterset_form = CustomFieldsetFilterForm
    table = CustomFieldsetTable
    action_buttons = ("add",)


class CustomFieldsetDetailView(ObjectDetailView):
    queryset = CustomFieldset.objects.all().prefetch_related("fields", "asset_types")

    layout = (((Panel("info", _("Custom Field Set Details")),),),)


class CustomFieldsetEditView(ObjectEditView):
    queryset = CustomFieldset.objects.all()
    model = CustomFieldset
    model_form = CustomFieldsetForm
    template_name = "generic/object_edit.html"
    default_return_url = "extras:customfieldset_list"


class CustomFieldsetDeleteView(ObjectDeleteView):
    queryset = CustomFieldset.objects.all()
    model = CustomFieldset
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("extras:customfieldset_list")


class CustomFieldsetBulkEditView(ObjectBulkEditView):
    queryset = CustomFieldset.objects.all()


class CustomFieldsetBulkDeleteView(ObjectBulkDeleteView):
    queryset = CustomFieldset.objects.all()


# =============================================================================
# Saved Filters
# =============================================================================


class SavedFilterListView(ObjectListView):
    queryset = SavedFilter.objects.select_related("content_type", "tenant", "created_by")
    filterset = SavedFilterFilterSet
    filterset_form = SavedFilterFilterForm
    table = SavedFilterTable
    action_buttons = ("add",)
    template_name = "generic/object_list.html"


class SavedFilterDetailView(ObjectDetailView):
    queryset = SavedFilter.objects.all()

    layout = (((Panel("info", _("Saved Filter Details")),),),)


class SavedFilterEditView(ObjectEditView):
    queryset = SavedFilter.objects.all()
    model = SavedFilter
    model_form = SavedFilterForm
    template_name = "generic/object_edit.html"
    default_return_url = "extras:savedfilter_list"


class SavedFilterDeleteView(ObjectDeleteView):
    queryset = SavedFilter.objects.all()
    model = SavedFilter
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("extras:savedfilter_list")


class SavedFilterSaveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Quick-save the current list-view filter as a named SavedFilter.

    POST-only. The list view's filter offcanvas hx-includes the filter form
    (``.filter-form-sidebar``), so the POST carries the filter fields' CURRENT
    values (whether or not "Apply" was clicked) alongside the save controls.
    We persist those filter params and redirect back to the list with
    ``?filter=<new pk>`` so the freshly saved filter applies immediately.

    Save-control fields use an ``sf_`` prefix so they never collide with a
    filterset field of the same name (e.g. a model whose filter has ``name``).
    """

    permission_required = ("extras.add_savedfilter",)

    # POST keys that are save-form controls or list chrome, NOT filter params.
    NON_FILTER_PARAMS = frozenset(
        {
            "sf_name",
            "sf_shared",
            "sf_is_global",
            "model",
            "return_url",
            "csrfmiddlewaretoken",
            "page",
            "per_page",
            "sort",
            "deleted",
            "filter",
        }
    )

    def post(self, request, *args, **kwargs):
        name = (request.POST.get("sf_name") or "").strip()
        model_str = (request.POST.get("model") or "").strip()
        is_global = request.POST.get("sf_is_global") in ("1", "true", "on", "yes")
        shared = request.POST.get("sf_shared") in ("1", "true", "on", "yes")

        content_type = self._resolve_content_type(model_str)
        if not name or content_type is None:
            return self._respond(request, model_str, None, "Provide a name and a valid model to save the filter.")

        parameters = self._parse_parameters(request.POST)

        tenant = get_current_tenant()
        if is_global and request.user.is_superuser:
            tenant = None

        saved = SavedFilter.objects.create(
            name=name,
            content_type=content_type,
            parameters=parameters,
            shared=shared,
            created_by=request.user,
            tenant=tenant,
        )

        return self._respond(request, model_str, saved.pk, None)

    def _resolve_content_type(self, model_str):
        if "." not in model_str:
            return None
        app_label, model_name = model_str.split(".", 1)
        try:
            return ContentType.objects.get_by_natural_key(app_label, model_name)
        except ContentType.DoesNotExist:
            return None

    def _parse_parameters(self, post):
        """Filter params = POST minus control/chrome keys and empty values."""
        params = {}
        for key in post.keys():
            if key in self.NON_FILTER_PARAMS:
                continue
            values = [v for v in post.getlist(key) if v not in (None, "")]
            if not values:
                continue
            params[key] = values if len(values) > 1 else values[0]
        return params

    def _list_url(self, request, model_str):
        return_url = request.POST.get("return_url")
        if return_url:
            # Same-host only — guard against an attacker-supplied external return_url.
            return safe_return_url(request, return_url.split("?", 1)[0], reverse("extras:savedfilter_list"))
        content_type = self._resolve_content_type(model_str)
        if content_type is not None:
            model = content_type.model_class()
            if model is not None:
                try:
                    return reverse(get_model_viewname(model, "list"))
                except Exception:
                    pass
        return reverse("extras:savedfilter_list")

    def _respond(self, request, model_str, pk, error):
        """Redirect to the list (with ?filter=<pk> on success). HTMX submissions
        get a 204 + HX-Redirect so the browser performs a full navigation and the
        list's ?filter load hook re-applies the saved filter."""
        list_url = self._list_url(request, model_str)
        target = f"{list_url}?{urlencode({'filter': pk})}" if pk else list_url
        if error:
            messages.error(request, error)
        if request.headers.get("HX-Request") == "true":
            response = HttpResponse(status=204)
            response["HX-Redirect"] = target
            return response
        return redirect(target)


# =============================================================================
# Alerting Views
# =============================================================================
import logging

from django.utils import timezone
from django.utils.decorators import method_decorator

from itambox.views.generic.service_views import SimplePostView

from .filters import (
    AlertLogFilterSet,
    AlertRuleFilterSet,
    NotificationChannelFilterSet,
    ReportTemplateFilterSet,
    ScheduledReportFilterSet,
)
from .forms import (
    AlertLogFilterForm,
    AlertRuleFilterForm,
    AlertRuleForm,
    NotificationChannelFilterForm,
    NotificationChannelForm,
    ReportTemplateFilterForm,
    ReportTemplateForm,
    ScheduledReportFilterForm,
    ScheduledReportForm,
)
from .models import AlertLog, AlertRule, NotificationChannel, ReportTemplate, ScheduledReport
from .tables import (
    AlertLogTable,
    AlertRuleTable,
    NotificationChannelTable,
    ReportTemplateTable,
    ScheduledReportTable,
)

logger = logging.getLogger(__name__)


@method_decorator(login_required, name="dispatch")
class AlertRuleListView(ObjectListView):
    queryset = AlertRule.objects.all()
    filterset = AlertRuleFilterSet
    filterset_form = AlertRuleFilterForm
    table = AlertRuleTable
    template_name = "core/alerts/alert_rule_list.html"
    action_buttons = ("add",)

    def get_breadcrumbs(self):
        return [(reverse("dashboard"), _("Dashboard")), (None, _("Alert Rules"))]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Alert Rules")
        return context


@method_decorator(login_required, name="dispatch")
class AlertRuleDetailView(ObjectDetailView):
    queryset = AlertRule.objects.all()
    template_name = "core/alerts/alert_rule_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = self.get_object()
        context["title"] = _("Alert Rule: %(name)s") % {"name": obj.name}
        context["logs_count"] = obj.logs.count()
        context["active_logs_count"] = obj.logs.filter(status="active").count()
        return context


@method_decorator(login_required, name="dispatch")
class AlertRuleCreateView(ObjectEditView):
    queryset = AlertRule.objects.all()
    model_form = AlertRuleForm
    template_name = "core/alerts/alert_rule_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Create Alert Rule")
        return context


@method_decorator(login_required, name="dispatch")
class AlertRuleUpdateView(ObjectEditView):
    queryset = AlertRule.objects.all()
    model_form = AlertRuleForm
    template_name = "core/alerts/alert_rule_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit Alert Rule: %(name)s") % {"name": self.object.name}
        return context


@method_decorator(login_required, name="dispatch")
class AlertRuleDeleteView(ObjectDeleteView):
    queryset = AlertRule.objects.all()
    template_name = "core/alerts/alert_rule_confirm_delete.html"


class AlertRuleBulkDeleteView(ObjectBulkDeleteView):
    queryset = AlertRule.objects.all()


class AlertRuleRunNowView(SimplePostView):
    """Evaluate a single alert rule immediately, on demand.

    The evaluation is enqueued as a background task rather than run inline:
    run_alert_rule_now() deliberately clears the tenant, membership and current-
    user contextvars without restoring them (it is designed to run standalone in
    a worker), so running it inside the request would contaminate the request's
    context for the remainder of the response.
    """

    queryset = AlertRule.objects.all()
    permission_required = ("extras.change_alertrule",)

    def perform_action(self, rule, request):
        from django_q.tasks import async_task

        rule_id = rule.pk
        async_task("core.tasks.run_alert_rule_now", rule_id)
        return {"message": f"Evaluation queued for '{rule.name}'. New alerts will appear shortly."}

    def get_success_redirect(self, obj, result):
        return redirect(
            safe_return_url(
                self.request,
                self.request.POST.get("return_url"),
                reverse("extras:alertrule_detail", kwargs={"pk": obj.pk}),
            )
        )


@method_decorator(login_required, name="dispatch")
class AlertLogListView(ObjectListView):
    queryset = (
        AlertLog.objects.filter(tenant__isnull=False).select_related("rule", "content_type").order_by("-created_at")
    )
    table = AlertLogTable
    template_name = "core/alerts/alert_list.html"
    action_buttons = ()
    filterset = AlertLogFilterSet
    filterset_form = AlertLogFilterForm

    def get_breadcrumbs(self):
        return [(reverse("dashboard"), _("Dashboard")), (None, _("Alerts Center"))]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Alerts Center")

        from core.managers import get_current_tenant

        current_tenant = get_current_tenant()

        active_qs = AlertLog.objects.filter(tenant__isnull=False, status=AlertLog.STATUS_ACTIVE)
        acknowledged_qs = AlertLog.objects.filter(tenant__isnull=False, status=AlertLog.STATUS_ACKNOWLEDGED)

        if current_tenant:
            active_qs = active_qs.filter(tenant=current_tenant)
            acknowledged_qs = acknowledged_qs.filter(tenant=current_tenant)

        context["active_alerts_count"] = active_qs.count()
        context["acknowledged_alerts_count"] = acknowledged_qs.count()
        return context


class _TenantBoundAlertActionMixin:
    # Keep superuser single-alert mutations fail-closed without an active tenant.

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.user.is_superuser and get_current_tenant() is None:
            return queryset.none()
        return queryset


class AlertAcknowledgeView(_TenantBoundAlertActionMixin, SimplePostView):
    queryset = AlertLog.objects.filter(tenant__isnull=False)
    permission_required = ("extras.change_alertlog",)

    def perform_action(self, alert, request):
        if alert.status == AlertLog.STATUS_ACTIVE:
            alert.status = AlertLog.STATUS_ACKNOWLEDGED
            alert.acknowledged_by = request.user
            alert.save(update_fields=["status", "acknowledged_by"])
        return {"message": f"Alert '{alert.subject}' acknowledged."}

    def get_success_redirect(self, obj, result):
        return redirect(
            safe_return_url(
                self.request,
                self.request.POST.get("return_url"),
                reverse("extras:alertlog_list"),
            )
        )


class AlertResolveView(_TenantBoundAlertActionMixin, SimplePostView):
    queryset = AlertLog.objects.filter(tenant__isnull=False)
    permission_required = ("extras.change_alertlog",)

    def perform_action(self, alert, request):
        if alert.status in [AlertLog.STATUS_ACTIVE, AlertLog.STATUS_ACKNOWLEDGED]:
            alert.status = AlertLog.STATUS_RESOLVED
            alert.resolved_by = request.user
            alert.resolved_at = timezone.now()
            alert.save(update_fields=["status", "resolved_by", "resolved_at"])
        return {"message": f"Alert '{alert.subject}' marked as resolved."}

    def get_success_redirect(self, obj, result):
        return redirect(
            safe_return_url(
                self.request,
                self.request.POST.get("return_url"),
                reverse("extras:alertlog_list"),
            )
        )


class _BulkAlertActionView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Apply a status transition to many AlertLogs selected in the Alert Center.

    Reads the checked ``pk`` list (gathered by batch-actions.ts) and transitions
    eligible logs. Tenant-scoped: AlertLog.objects only exposes the current
    tenant's logs, so a user can never act on another tenant's alerts.
    """

    permission_required = ("extras.change_alertlog",)
    hx_trigger = "tableRefreshRequired"
    eligible_statuses = ()

    def apply(self, queryset, user):
        raise NotImplementedError

    def success_message(self, count):
        raise NotImplementedError

    def post(self, request, *args, **kwargs):
        pks = request.POST.getlist("pk")
        return_url = safe_return_url(request, request.POST.get("return_url"), reverse("extras:alertlog_list"))

        if not pks:
            return self._respond(request, gettext("No alerts selected."), "warning", return_url)

        try:
            unique_pks = {int(value) for value in pks}
        except (TypeError, ValueError):
            unique_pks = set()
        with transaction.atomic():
            locked_qs = AlertLog.objects.select_for_update().filter(pk__in=unique_pks).order_by("pk")
            # A null-tenant row marked unresolved is never safe to mutate,
            # including for superusers: reconciliation has not established an
            # owner, so bulk actions must fail closed rather than guess.
            locked_qs = locked_qs.exclude(tenant__isnull=True, tenant_resolution_status="unresolved")
            current_tenant = get_current_tenant()
            if request.user.is_superuser and current_tenant is None:
                # TenantScopingManager intentionally gives superusers a global
                # queryset without a scope. A bulk mutation must never use that
                # global path: without an active tenant, fail closed.
                locked_qs = locked_qs.none()
            elif not request.user.is_superuser:
                locked_qs = locked_qs.filter(tenant__isnull=False)
            locked_alerts = list(locked_qs)
            # Materialize the locked rows before comparing the selection. Django
            # strips FOR UPDATE from aggregate COUNT queries, so COUNT() cannot
            # prove all-or-safe semantics under READ COMMITTED.
            if not unique_pks or len(locked_alerts) != len(unique_pks):
                return self._respond(
                    request,
                    gettext("The selection contains an alert that is not accessible in the active tenant."),
                    "danger",
                    return_url,
                )
            eligible = locked_alerts
            if self.eligible_statuses:
                eligible = [alert for alert in locked_alerts if alert.status in self.eligible_statuses]
            count = self.apply(eligible, request.user)
        return self._respond(request, self.success_message(count), "success", return_url)

    def _respond(self, request, message, level, return_url):
        if getattr(request, "htmx", False):
            resp = HttpResponse(status=204)
            resp["HX-Trigger"] = json.dumps(
                {
                    self.hx_trigger: None,
                    "showMessage": {"message": message, "level": level},
                }
            )
            return resp
        # Django messages has no Bootstrap-style ``danger`` helper.
        if level == "danger":
            level = "error"
        getattr(messages, level)(request, message)
        return redirect(return_url)


class AlertBulkAcknowledgeView(_BulkAlertActionView):
    eligible_statuses = (AlertLog.STATUS_ACTIVE,)

    def apply(self, queryset, user):
        count = 0
        for alert in queryset:
            alert.status = AlertLog.STATUS_ACKNOWLEDGED
            alert.acknowledged_by = user
            alert.save(update_fields=["status", "acknowledged_by"])
            count += 1
        return count

    def success_message(self, count):
        return gettext("%(count)s alert(s) acknowledged.") % {"count": count}


class AlertBulkResolveView(_BulkAlertActionView):
    eligible_statuses = (AlertLog.STATUS_ACTIVE, AlertLog.STATUS_ACKNOWLEDGED)

    def apply(self, queryset, user):
        count = 0
        for alert in queryset:
            alert.status = AlertLog.STATUS_RESOLVED
            alert.resolved_by = user
            alert.resolved_at = timezone.now()
            alert.save(update_fields=["status", "resolved_by", "resolved_at"])
            count += 1
        return count

    def success_message(self, count):
        return gettext("%(count)s alert(s) resolved.") % {"count": count}


@method_decorator(login_required, name="dispatch")
class NotificationChannelListView(ObjectListView):
    queryset = NotificationChannel.objects.all()
    filterset = NotificationChannelFilterSet
    filterset_form = NotificationChannelFilterForm
    table = NotificationChannelTable
    template_name = "core/alerts/notificationchannel_list.html"
    action_buttons = ("add",)

    def get_breadcrumbs(self):
        return [(reverse("dashboard"), _("Dashboard")), (None, _("Notification Channels"))]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Notification Channels")
        return context


@method_decorator(login_required, name="dispatch")
class NotificationChannelCreateView(ObjectEditView):
    queryset = NotificationChannel.objects.all()
    model_form = NotificationChannelForm
    template_name = "core/alerts/notificationchannel_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Create Notification Channel")
        return context


@method_decorator(login_required, name="dispatch")
class NotificationChannelUpdateView(ObjectEditView):
    queryset = NotificationChannel.objects.all()
    model_form = NotificationChannelForm
    template_name = "core/alerts/notificationchannel_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit Notification Channel: %(name)s") % {"name": self.object.name}
        return context


@method_decorator(login_required, name="dispatch")
class NotificationChannelDeleteView(ObjectDeleteView):
    queryset = NotificationChannel.objects.all()
    template_name = "core/alerts/notificationchannel_confirm_delete.html"


class NotificationChannelBulkDeleteView(ObjectBulkDeleteView):
    queryset = NotificationChannel.objects.all()


class NotificationChannelTestView(SimplePostView):
    """Send a test notification through a channel and report success/failure inline."""

    queryset = NotificationChannel.objects.all()
    permission_required = ("extras.change_notificationchannel",)

    def perform_action(self, channel, request):
        from core.events import send_notification_to_channel

        ok = send_notification_to_channel(
            channel,
            subject=str(_("ITAMbox Test Notification")),
            body=_("This is a test message sent to channel '%(name)s' (%(type)s).")
            % {
                "name": channel.name,
                "type": channel.get_channel_type_display(),
            },
        )
        if ok:
            return {"message": f"Test notification sent successfully via '{channel.name}'."}
        raise Exception(f"Channel '{channel.name}' returned a delivery failure.")

    def get_success_redirect(self, obj, result):
        return redirect(reverse("extras:notificationchannel_list"))


# =============================================================================
# Reporting Views
# =============================================================================

#: The report designer is opt-in (ITAMBOX_FEATURE_REPORT_DESIGNER). Every route
#: below that edits, previews, renders, or schedules a ReportTemplate is closed
#: while the capability is inactive, so the flag an operator sets and the routes
#: or background delivery a deployment serves cannot drift apart. The Stable
#: curated report catalogue (`reporting.curated`) remains independent.
REPORT_DESIGNER_CAPABILITY = "reporting.designer"


@method_decorator(login_required, name="dispatch")
class ReportTemplateListView(CapabilityRequiredMixin, ObjectListView):
    capability_key = REPORT_DESIGNER_CAPABILITY
    queryset = ReportTemplate.objects.all()
    filterset = ReportTemplateFilterSet
    filterset_form = ReportTemplateFilterForm
    table = ReportTemplateTable
    template_name = "core/reports/report_template_list.html"
    action_buttons = ("add",)

    def get_breadcrumbs(self):
        return [(reverse("dashboard"), _("Dashboard")), (None, _("Report Templates"))]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Report Templates")
        context["is_beta_module"] = True
        return context


@method_decorator(login_required, name="dispatch")
class ReportTemplateDetailView(CapabilityRequiredMixin, ObjectDetailView):
    capability_key = REPORT_DESIGNER_CAPABILITY
    queryset = ReportTemplate.objects.all()
    template_name = "core/reports/report_template_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = self.get_object()
        context["title"] = _("Report Template: %(name)s") % {"name": obj.name}
        context["schedules"] = obj.schedules.all()
        context["legacy_designer_notice"] = bool(obj.legacy_designer_grandfathered)
        return context


@method_decorator(login_required, name="dispatch")
class ReportTemplateCreateView(CapabilityRequiredMixin, ObjectEditView):
    capability_key = REPORT_DESIGNER_CAPABILITY
    queryset = ReportTemplate.objects.all()
    model_form = ReportTemplateForm
    template_name = "core/reports/report_template_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Create Report Template")
        return context


@method_decorator(login_required, name="dispatch")
class ReportTemplateUpdateView(CapabilityRequiredMixin, ObjectEditView):
    capability_key = REPORT_DESIGNER_CAPABILITY
    queryset = ReportTemplate.objects.all()
    model_form = ReportTemplateForm
    template_name = "core/reports/report_template_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit Report Template: %(name)s") % {"name": self.object.name}
        return context


@method_decorator(login_required, name="dispatch")
class ReportTemplateDeleteView(CapabilityRequiredMixin, ObjectDeleteView):
    capability_key = REPORT_DESIGNER_CAPABILITY
    queryset = ReportTemplate.objects.all()
    template_name = "core/reports/report_template_confirm_delete.html"


class ReportTemplateBulkDeleteView(CapabilityRequiredMixin, ObjectBulkDeleteView):
    capability_key = REPORT_DESIGNER_CAPABILITY
    queryset = ReportTemplate.objects.all()


@method_decorator(login_required, name="dispatch")
class ScheduledReportListView(CapabilityRequiredMixin, ObjectListView):
    capability_key = REPORT_DESIGNER_CAPABILITY
    queryset = ScheduledReport.objects.select_related("report").prefetch_related(
        "scope_authorization__authorized_by", "scope_authorization__revoked_by"
    )
    filterset = ScheduledReportFilterSet
    filterset_form = ScheduledReportFilterForm
    table = ScheduledReportTable
    template_name = "core/reports/report_list.html"
    action_buttons = ("add",)

    def get_breadcrumbs(self):
        return [(reverse("dashboard"), _("Dashboard")), (None, _("Scheduled Reports"))]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Scheduled Reports")
        context["templates"] = ReportTemplate.objects.all()
        context["is_beta_module"] = True
        return context


def handle_report_scheduling(sched_report):
    from django.utils import timezone
    from django_q.models import Schedule

    if sched_report.is_active:
        if not registry.is_active(REPORT_DESIGNER_CAPABILITY):
            return
        # Map frequency choice to django-q Schedule type
        freq_mapping = {
            "once": Schedule.ONCE,
            "hourly": Schedule.HOURLY,
            "daily": Schedule.DAILY,
            "weekly": Schedule.WEEKLY,
            "biweekly": "BW",
            "monthly": Schedule.MONTHLY,
            "quarterly": "Q",
            "yearly": "Y",
            "cron": Schedule.CRON,
        }
        q_freq = freq_mapping.get(sched_report.frequency, Schedule.WEEKLY)

        defaults = {
            "func": "core.tasks.generate_scheduled_report_task",
            "args": str(sched_report.pk),
            "schedule_type": q_freq,
            "repeats": -1,
        }
        if q_freq == Schedule.CRON:
            defaults["cron"] = sched_report.cron_expression
        else:
            defaults["cron"] = ""

        # Configure next_run if start_time is set
        if sched_report.start_time:
            now = timezone.now()
            # Compute next run date with this start time
            next_date = now.date()
            next_run = timezone.make_aware(
                datetime.datetime.combine(next_date, sched_report.start_time), timezone.get_current_timezone()
            )
            if next_run < now:
                # If the time has already passed today, set to tomorrow
                next_run += datetime.timedelta(days=1)
            defaults["next_run"] = next_run

        q_schedule, created = Schedule.objects.update_or_create(
            name=f"scheduled_report_{sched_report.pk}", defaults=defaults
        )
        if sched_report.schedule != q_schedule:
            sched_report.schedule = q_schedule
            sched_report.save(update_fields=["schedule"])
    else:
        if sched_report.schedule:
            q_sched = sched_report.schedule
            sched_report.schedule = None
            sched_report.save(update_fields=["schedule"])
            q_sched.delete()


@method_decorator(login_required, name="dispatch")
class ScheduledReportCreateView(CapabilityRequiredMixin, ObjectEditView):
    capability_key = REPORT_DESIGNER_CAPABILITY
    queryset = ScheduledReport.objects.all()
    model_form = ScheduledReportForm
    template_name = "core/reports/report_schedule_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Schedule a Report")
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        handle_report_scheduling(self.object)
        return response


@method_decorator(login_required, name="dispatch")
class ScheduledReportUpdateView(CapabilityRequiredMixin, ObjectEditView):
    capability_key = REPORT_DESIGNER_CAPABILITY
    queryset = ScheduledReport.objects.all()
    model_form = ScheduledReportForm
    template_name = "core/reports/report_schedule_form.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = _("Edit Schedule: %(name)s") % {"name": self.object.name}
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        handle_report_scheduling(self.object)
        return response


@method_decorator(login_required, name="dispatch")
class ScheduledReportDeleteView(CapabilityRequiredMixin, ObjectDeleteView):
    capability_key = REPORT_DESIGNER_CAPABILITY
    queryset = ScheduledReport.objects.all()
    template_name = "core/reports/report_schedule_confirm_delete.html"


class ScheduledReportBulkDeleteView(CapabilityRequiredMixin, ObjectBulkDeleteView):
    capability_key = REPORT_DESIGNER_CAPABILITY
    queryset = ScheduledReport.objects.all()


@method_decorator(login_required, name="dispatch")
class ScheduledReportScopeApprovalView(CapabilityRequiredMixin, PermissionRequiredMixin, LoginRequiredMixin, View):
    """Approve or revoke the durable cross-tenant scope approval of a schedule.

    Approval requires the same cross-tenant report permission the model gate
    enforces, and is refused when the acting principal's reach does not cover
    every tenant in scope — an ineffective approval would only delay the
    fail-closed ``report.scope_unauthorized`` terminal state to delivery time.
    """

    capability_key = REPORT_DESIGNER_CAPABILITY
    permission_required = ("reports.view_cross_tenant_reports",)
    template_name = "core/reports/report_schedule_scope_approval.html"

    def get_queryset(self):
        return ScheduledReport.objects.select_related("report").prefetch_related(
            "filter_tenants",
            "scope_authorization__authorized_by",
            "scope_authorization__revoked_by",
        )

    def get_object(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs.get("pk"))

    def _stored_authorization(self, sched):
        try:
            return sched.scope_authorization
        except ObjectDoesNotExist:
            return None

    def _scope_tenants(self, sched):
        Tenant = apps.get_model("organization", "Tenant")
        scope_tenant_ids = sched.effective_scope_tenant_ids()
        if not scope_tenant_ids:
            return []
        return list(Tenant._base_manager.filter(pk__in=scope_tenant_ids))

    def _approval_would_be_effective(self, sched, scope_tenants):
        # An approval only takes effect when the acting principal holds the
        # cross-tenant permission on EVERY tenant in scope; delivery re-checks
        # this per tenant, so refuse to store an approval that cannot work.
        return all(
            self.request.user.has_perm("reports.view_cross_tenant_reports", obj=tenant) for tenant in scope_tenants
        )

    def get_context_data(self, **kwargs):
        sched = self.object = self.get_object()
        authorization = self._stored_authorization(sched)
        scope_tenants = self._scope_tenants(sched)
        scope_tenant_ids = sorted({tenant.pk for tenant in scope_tenants})
        authorization_is_current = bool(
            authorization
            and not authorization.is_revoked()
            and sorted(authorization.scope_tenant_ids) == scope_tenant_ids
        )
        return {
            "object": sched,
            "title": _("Scope Approval: %(name)s") % {"name": sched.name},
            "requires_authorization": sched.scope_requires_authorization(),
            "scope_tenants": scope_tenants,
            "authorization": authorization,
            "authorization_is_current": authorization_is_current,
            "approval_would_be_effective": self._approval_would_be_effective(sched, scope_tenants),
            "return_url": safe_return_url(
                self.request, self.request.GET.get("return_url"), reverse("extras:scheduledreport_list")
            ),
        }

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name, self.get_context_data())

    def post(self, request, *args, **kwargs):
        scope_authorization_model = apps.get_model("extras", "ScheduledReportScopeAuthorization")
        sched = self.object = self.get_object()
        action = request.POST.get("action")
        return_url = safe_return_url(request, request.POST.get("return_url"), reverse("extras:scheduledreport_list"))
        try:
            if action == "approve":
                scope_tenants = self._scope_tenants(sched)
                if not scope_tenants:
                    raise ValidationError(
                        _("This schedule has no resolvable scope tenants, so a cross-tenant approval cannot be stored.")
                    )
                if not self._approval_would_be_effective(sched, scope_tenants):
                    missing = [
                        tenant.name
                        for tenant in scope_tenants
                        if not request.user.has_perm("reports.view_cross_tenant_reports", obj=tenant)
                    ]
                    raise ValidationError(
                        _("Your cross-tenant reach does not cover: %(tenants)s. The approval would not take effect.")
                        % {"tenants": ", ".join(missing)}
                    )
                scope_authorization_model.approve(sched, request.user)
                messages.success(request, _("Cross-tenant scope of '%(name)s' approved.") % {"name": sched.name})
            elif action == "revoke":
                scope_authorization_model.revoke(sched, request.user)
                messages.success(
                    request, _("Cross-tenant scope approval of '%(name)s' revoked.") % {"name": sched.name}
                )
            else:
                messages.error(request, _("Unknown scope approval action."))
            return redirect(return_url)
        except PermissionDenied as error:
            messages.error(request, str(error))
            return redirect(request.path)
        except ValidationError as error:
            for message in getattr(error, "messages", [str(error)]):
                messages.error(request, message)
            return redirect(request.path)


@method_decorator(login_required, name="dispatch")
class ReportTriggerImmediateView(CapabilityRequiredMixin, PermissionRequiredMixin, LoginRequiredMixin, View):
    capability_key = REPORT_DESIGNER_CAPABILITY
    permission_required = ("extras.view_scheduledreport",)

    def has_permission(self):
        perms = self.get_permission_required()
        try:
            obj = get_object_or_404(ScheduledReport, pk=self.kwargs.get("pk"))
        except Http404:
            return False
        return self.request.user.has_perms(perms, obj=obj)

    def post(self, request, pk):
        sched = get_object_or_404(ScheduledReport, pk=pk)

        # Trigger report generation synchronously for immediate visual feedback in the UI
        from core.tasks import generate_scheduled_report_task

        success = generate_scheduled_report_task(sched.pk)
        sched.refresh_from_db()
        archive = sched.archives.first()
        status = sched.last_status or ""
        _status_kind, _separator, status_detail = status.partition(":")
        status_detail = status_detail.strip()
        if status.startswith("delivery_") and status_detail:
            delivery_detail = status_detail
        elif status.startswith("failed:"):
            delivery_detail = status
        else:
            delivery_detail = (archive.error_message if archive else "") or status or _("Check logs.")
        if status == "partial" or status.startswith("delivery_partial:"):
            messages.warning(
                request,
                _("Scheduled report '%(name)s' was generated but delivered only partially: %(error)s")
                % {"name": sched.name, "error": delivery_detail},
            )
        elif status == "failed" or status.startswith("delivery_failed:"):
            messages.error(
                request,
                _("Scheduled report '%(name)s' was generated but all deliveries failed: %(error)s")
                % {"name": sched.name, "error": delivery_detail},
            )
        elif success:
            messages.success(
                request, _("Scheduled report '%(name)s' generated and sent successfully.") % {"name": sched.name}
            )
        else:
            messages.error(
                request,
                _("Failed to generate scheduled report '%(name)s': %(error)s")
                % {"name": sched.name, "error": delivery_detail},
            )

        return redirect(
            safe_return_url(request, request.POST.get("return_url"), reverse("extras:scheduledreport_list"))
        )


@method_decorator(login_required, name="dispatch")
class ReportTemplatePreviewView(CapabilityRequiredMixin, PermissionRequiredMixin, View):
    capability_key = REPORT_DESIGNER_CAPABILITY
    permission_required = ()

    def has_permission(self):
        return self.request.user.has_perm("extras.add_reporttemplate") or self.request.user.has_perm(
            "extras.change_reporttemplate"
        )

    def post(self, request, *args, **kwargs):
        report_type = request.POST.get("report_type")
        style_preset = request.POST.get("style_preset", "default")
        included_columns = request.POST.getlist("included_columns")
        include_summary_cards = (
            request.POST.get("include_summary_cards") == "on" or request.POST.get("include_summary_cards") == "true"
        )
        include_distribution_chart = (
            request.POST.get("include_distribution_chart") == "on"
            or request.POST.get("include_distribution_chart") == "true"
        )
        group_by_field = request.POST.get("group_by_field", "")
        advanced_mode = request.POST.get("advanced_mode") in ("on", "true", "1")
        template_content = request.POST.get("template_content", "")
        description = request.POST.get("description", "")

        # Resolve active tenant for preview scoping
        selected_tenant_id = request.POST.get("tenant")
        active_tenant = None
        if selected_tenant_id and request.user.is_superuser:
            from organization.models import Tenant

            active_tenant = Tenant.objects.filter(pk=selected_tenant_id).first()
        else:
            from core.managers import get_current_tenant

            active_tenant = get_current_tenant()

        # Resolve multi-tenant filter scoping constellation for preview
        selected_filter_tenant_ids = request.POST.getlist("filter_tenants")
        filter_tenants = []
        if selected_filter_tenant_ids and request.user.is_superuser:
            from organization.models import Tenant

            filter_tenants = list(Tenant.objects.filter(pk__in=selected_filter_tenant_ids))

        # Create dynamic in-memory ReportTemplate object
        template_instance = ReportTemplate(
            name=request.POST.get("name", "Preview Report"),
            description=description,
            report_type=report_type,
            included_columns=included_columns,
            include_summary_cards=include_summary_cards,
            include_distribution_chart=include_distribution_chart,
            group_by_field=group_by_field,
            style_preset=style_preset,
            advanced_mode=advanced_mode,
            template_content=template_content,
        )

        # inline imports: heavy-import: report provider discovery is only needed for this preview request
        from core.reports import build_report_context

        try:
            template_instance.full_clean(validate_constraints=False)
            _headers, _rows, _summary_cards, _grouped_data, _chart_svg, context_data = build_report_context(
                template_instance, active_tenant=active_tenant, filter_tenants=filter_tenants
            )

            context_data["request"] = request
            rendered_html = render_report_html(context_data, template_instance)

            return HttpResponse(rendered_html)
        except PermissionError:
            return HttpResponse(gettext("You may not view this report's data."), status=403)
        except ValidationError as exc:
            details = escape("; ".join(str(message) for message in exc.messages))
            return HttpResponse(
                f"<h3>{gettext('Invalid report template configuration.')}</h3><p>{details}</p>", status=400
            )
        except Exception:
            # Full detail (with traceback) goes to the server log; the client gets a
            # generic message so exception text is never reflected in the response.
            logger.exception("Template Render Error in preview")
            return HttpResponse(
                f"<h3>{gettext('Template render failed. See the server log for details.')}</h3>", status=400
            )


@method_decorator(login_required, name="dispatch")
class ReportTemplateDownloadView(CapabilityRequiredMixin, PermissionRequiredMixin, LoginRequiredMixin, View):
    capability_key = REPORT_DESIGNER_CAPABILITY
    permission_required = ("extras.view_reporttemplate",)

    def has_permission(self):
        perms = self.get_permission_required()
        try:
            obj = get_object_or_404(ReportTemplate, pk=self.kwargs.get("pk"))
        except Http404:
            return False
        return self.request.user.has_perms(perms, obj=obj)

    def get(self, request, pk, *args, **kwargs):
        # objects automatically handles tenant scoping!
        template = get_object_or_404(ReportTemplate.objects.all(), pk=pk)

        # Enforce multi-tenant thread-local active tenant binding
        from core.managers import get_current_tenant

        active_tenant = get_current_tenant()

        # Enforce sandboxed constellation
        filter_tenants = list(template.filter_tenants.all())

        # inline imports: heavy-import: report provider discovery is only needed for this export request
        from core.reports import build_report_context

        try:
            headers, rows, _summary_cards, _grouped_data, _chart_svg, context_data = build_report_context(
                template, active_tenant=active_tenant, filter_tenants=filter_tenants
            )

            format_type = request.GET.get("format", "html").lower()
            from core.csv_utils import safe_csv_filename

            safe_name = safe_csv_filename(template.name).lower().replace(" ", "_")
            stamp = f"{timezone.now():%Y%m%d}"

            if format_type == "csv":
                response = HttpResponse(
                    render_report_csv(template, headers, rows, _summary_cards, _grouped_data), content_type="text/csv"
                )
                response["Content-Disposition"] = f'attachment; filename="{safe_name}_{stamp}.csv"'
                return response

            if format_type == "xlsx":
                from core.reports.exporters import XLSX_MIME, report_xlsx_bytes

                response = HttpResponse(
                    report_xlsx_bytes(headers, rows, sheet_title=template.name), content_type=XLSX_MIME
                )
                response["Content-Disposition"] = f'attachment; filename="{safe_name}_{stamp}.xlsx"'
                return response

            # HTML render — shared by the html and pdf formats.
            context_data["request"] = request
            rendered_html = render_report_html(context_data, template)

            if format_type == "pdf":
                from core.reports.exporters import PDF_MIME, report_pdf_bytes

                response = HttpResponse(report_pdf_bytes(rendered_html), content_type=PDF_MIME)
                disposition = "inline" if request.GET.get("print") == "true" else "attachment"
                response["Content-Disposition"] = f'{disposition}; filename="{safe_name}_{stamp}.pdf"'
                return response

            response = HttpResponse(rendered_html, content_type="text/html")
            disposition = "inline" if request.GET.get("print") == "true" else "attachment"
            response["Content-Disposition"] = f'{disposition}; filename="{safe_name}_{stamp}.html"'
            return response
        except PermissionError:
            return HttpResponse(gettext("You may not view this report's data."), status=403)
        except Exception:
            # Full detail (with traceback) goes to the server log; the client gets a
            # generic message so exception text is never reflected in the response.
            logger.exception("Template Render Error in download")
            return HttpResponse(
                f"<h3>{gettext('Template render failed. See the server log for details.')}</h3>", status=400
            )
