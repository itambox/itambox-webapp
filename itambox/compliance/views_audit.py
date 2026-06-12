import django_tables2 as tables
from django_tables2.utils import A
import json
from django.shortcuts import get_object_or_404, render
from django.http import HttpResponse
from django.urls import reverse_lazy
from django.views.generic import View
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django import forms

from core.tables import BaseTable, ToggleColumn, ActionsColumn
from itambox.views.generic import ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView
from itambox.views.generic.service_views import GenericTransactionView, SimplePostView

from compliance.models import AuditSession, AssetAudit
from compliance.forms_audit import AuditSessionForm, AssetAuditForm, AuditBarcodeScanForm
from compliance.filters import AuditSessionFilterSet
from compliance.forms_filter import AuditSessionFilterForm
from compliance.reconciliation import audit_asset, close_audit_session, rehome_audit_session_mismatches, flag_missing_assets

User = get_user_model()


class AuditSessionTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('compliance:auditsession_detail', args=[A('pk')], verbose_name='Name')
    location = tables.LinkColumn('organization:location_detail', args=[A('location.pk')], accessor='location.name', verbose_name='Location')
    status = tables.Column(verbose_name='Status')
    started_at = tables.DateTimeColumn(format="Y-m-d H:i")
    completed_at = tables.DateTimeColumn(format="Y-m-d H:i")
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = AuditSession
        fields = ('pk', 'name', 'location', 'status', 'started_at', 'completed_at', 'actions')
        default_columns = ('pk', 'name', 'location', 'status', 'started_at', 'completed_at', 'actions')

    def render_status(self, value):
        badges = {
            'planned': 'bg-secondary text-secondary-fg',
            'active': 'bg-primary text-primary-fg',
            'completed': 'bg-success text-success-fg',
        }
        badge_class = badges.get(value, 'bg-secondary text-secondary-fg')
        display = value.title() if value else 'Planned'
        from django.utils.html import format_html
        return format_html('<span class="badge {}">{}</span>', badge_class, display)

    def render_location(self, value):
        return value or "Global (All Locations)"

    def render_completed_at(self, value):
        return value.strftime("%Y-%m-%d %H:%M") if value else "—"


class AuditSessionListView(ObjectListView):
    queryset = AuditSession.objects.select_related('location', 'created_by')
    table = AuditSessionTable
    template_name = 'generic/object_list.html'
    action_buttons = ('add',)
    filterset = AuditSessionFilterSet
    filterset_form = AuditSessionFilterForm


class AuditSessionCreateView(ObjectEditView):
    queryset = AuditSession.objects.all()
    model_form = AuditSessionForm
    template_name = 'generic/object_edit.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        start_immediately = form.cleaned_data.get('start_immediately', True)
        form.instance.status = 'active' if start_immediately else 'planned'
        return super().form_valid(form)


class AuditSessionDetailView(ObjectDetailView):
    queryset = AuditSession.objects.select_related('location', 'created_by').prefetch_related('audits__asset', 'audits__auditor')
    template_name = 'compliance/audits/audit_session_detail.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        session = self.get_object()

        if session.status == 'completed' and session.reconciliation_report:
            # Render from the frozen stored report; do not recompute.
            report = session.reconciliation_report
            rows = report.get('rows', [])
            ctx['total_expected'] = report.get('total_expected', 0)
            ctx['total_scanned'] = report.get('total_scanned', 0)
            ctx['matching'] = [r for r in rows if r['category'] == 'matching']
            ctx['mismatches'] = [r for r in rows if r['category'] == 'mismatched']
            ctx['surprise_finds'] = [r for r in rows if r['category'] == 'surprise']
            ctx['missing_assets'] = [r for r in rows if r['category'] == 'missing']
            ctx['report_is_stored'] = True
        else:
            from compliance.reconciliation import classify_session_audits, _audit_to_dict, _missing_asset_to_dict
            classified = classify_session_audits(session)
            expected_loc_name = session.location.name if session.location else 'Global'
            ctx['total_expected'] = (
                len(classified['matching']) + len(classified['mismatched']) + classified['missing'].count()
            )
            ctx['total_scanned'] = (
                len(classified['matching']) + len(classified['mismatched']) + len(classified['surprise'])
            )
            ctx['matching'] = [_audit_to_dict(a, 'matching') for a in classified['matching']]
            ctx['mismatches'] = [_audit_to_dict(a, 'mismatched', expected_loc_name) for a in classified['mismatched']]
            ctx['surprise_finds'] = [_audit_to_dict(a, 'surprise') for a in classified['surprise']]
            ctx['missing_assets'] = [_missing_asset_to_dict(a, session.location) for a in classified['missing']]
            ctx['report_is_stored'] = False

        ctx['scan_form'] = AuditBarcodeScanForm()
        return ctx


class AssetAuditScanView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """HTMX AJAX endpoint called on barcode scans within an active campaign."""
    permission_required = 'compliance.add_assetaudit'

    def handle_no_permission(self):
        if self.request.user.is_authenticated and getattr(self.request, 'htmx', False):
            response = HttpResponse(status=204)
            response['HX-Trigger'] = json.dumps({
                "showMessage": {"message": "You do not have permission to record audit scans.", "level": "danger"}
            })
            return response
        return super().handle_no_permission()

    def post(self, request, pk, *args, **kwargs):
        session = get_object_or_404(AuditSession, pk=pk, status='active')
        form = AuditBarcodeScanForm(request.POST)

        if form.is_valid():
            barcode = form.cleaned_data['barcode'].strip()

            from assets.scanning import resolve_scanned_code
            asset = resolve_scanned_code(barcode)

            if not asset:
                response = HttpResponse(
                    f"<div class='alert alert-danger mb-0'>Scanned asset tag or serial '{barcode}' not found in database.</div>",
                    status=400
                )
                response['HX-Trigger'] = 'playAuditFailSound'
                return response

            try:
                audit_asset(
                    asset=asset,
                    user=request.user,
                    session=session,
                    location=session.location or asset.location,
                    status=asset.status,
                    verification_method='barcode'
                )
            except ValidationError as err:
                msg = err.message if hasattr(err, 'message') else str(err)
                response = HttpResponse(f"<div class='alert alert-danger mb-0'>{msg}</div>", status=400)
                response['HX-Trigger'] = 'playAuditFailSound'
                return response

            response = render(request, 'compliance/audits/includes/audit_scan_success.html', {'asset': asset})
            response['HX-Trigger'] = json.dumps({
                'updateReconciliation': None,
                'playAuditSound': None
            })
            return response

        response = HttpResponse("<div class='alert alert-danger mb-0'>Invalid form submission.</div>", status=400)
        response['HX-Trigger'] = 'playAuditFailSound'
        return response


class AuditSessionCloseForm(forms.Form):
    def __init__(self, *args, **kwargs):
        kwargs.pop('instance', None)
        super().__init__(*args, **kwargs)


class AuditSessionCloseView(GenericTransactionView):
    queryset = AuditSession.objects.filter(status='active')
    model_form = AuditSessionCloseForm
    template_name = 'compliance/audits/audit_session_close.html'
    service_callable = close_audit_session
    permission_required = 'compliance.change_auditsession'
    success_message = "Audit session closed. Reconciliation report generated."

    def get_service_kwargs(self, form):
        return {}


class AuditSessionRehomeView(SimplePostView):
    queryset = AuditSession.objects.filter(status='completed')
    permission_required = 'assets.change_asset'

    def perform_action(self, session, request) -> dict:
        rehome_audit_session_mismatches(session, request.user)
        return {'message': f"All mismatched assets in campaign '{session.name}' have been bulk re-homed to '{session.location.name if session.location else 'Global'}'."}


class AuditSessionReportCsvView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Stream the stored reconciliation report as a CSV download."""
    permission_required = 'compliance.view_auditsession'

    def get(self, request, pk, *args, **kwargs):
        import csv
        session = get_object_or_404(AuditSession, pk=pk, status='completed')
        report = session.reconciliation_report or {}
        rows = report.get('rows', [])

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = (
            f'attachment; filename="audit-report-{session.pk}.csv"'
        )
        writer = csv.writer(response)
        writer.writerow([
            'Category', 'Asset Tag', 'Asset Name',
            'Observed Location', 'Expected Location',
            'Auditor', 'Timestamp',
        ])
        for row in rows:
            writer.writerow([
                row.get('category', ''),
                row.get('asset_tag', ''),
                row.get('name', ''),
                row.get('observed_location', ''),
                row.get('expected_location', ''),
                row.get('auditor', ''),
                row.get('timestamp_display', '') or (row.get('timestamp', '') or '')[:16],
            ])
        return response


class AuditSessionFlagMissingForm(forms.Form):
    """Empty confirmation form — submit confirms the bulk flag action."""
    pass


class AuditSessionFlagMissingView(GenericTransactionView):
    """Confirm + execute bulk 'Flag missing as Missing' action on completed sessions."""
    queryset = AuditSession.objects.filter(status='completed')
    model_form = AuditSessionFlagMissingForm
    template_name = 'compliance/audits/audit_session_flag_missing.html'
    error_partial = 'compliance/audits/audit_session_flag_missing.html#flag-missing-form'
    service_callable = flag_missing_assets
    permission_required = 'assets.change_asset'
    context_object_name = 'session'
    hx_trigger = 'tableRefreshRequired'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.pop('instance', None)
        return kwargs

    def get_service_kwargs(self, form):
        return {}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        session = self.get_object()
        if session.reconciliation_report:
            missing_rows = [
                r for r in session.reconciliation_report.get('rows', [])
                if r.get('category') == 'missing'
            ]
            context['missing_count'] = len(missing_rows)
        else:
            context['missing_count'] = 0
        return context

    def get_success_message(self, result=None):
        if result:
            return (
                f"{result['flagged']} asset(s) flagged as Missing. "
                f"{result['skipped']} skipped (status changed since close)."
            )
        return "Missing assets flagged."


class AuditSessionDeleteView(ObjectDeleteView):
    queryset = AuditSession.objects.all()
    model = AuditSession
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('compliance:auditsession_list')


class AuditSessionStartView(SimplePostView):
    """Transition a planned session to active."""
    queryset = AuditSession.objects.filter(status='planned')
    permission_required = 'compliance.change_auditsession'

    def perform_action(self, session, request) -> dict:
        from django.core.exceptions import ValidationError
        if session.status != 'planned':
            raise ValidationError("Only planned sessions can be started.")
        session.status = 'active'
        session.save(update_fields=['status'])
        return {'message': f"Campaign '{session.name}' is now active."}
