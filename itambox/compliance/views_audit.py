import django_tables2 as tables
from django_tables2.utils import A
from django.shortcuts import get_object_or_404, render
from django.http import HttpResponse
from django.urls import reverse_lazy
from django.views.generic import View
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django import forms

from core.tables import BaseTable, ToggleColumn, ActionsColumn
from itambox.views.generic import ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView
from itambox.views.generic.service_views import GenericTransactionView, SimplePostView

from compliance.models import AuditSession, AssetAudit
from compliance.forms_audit import AuditSessionForm, AssetAuditForm, AuditBarcodeScanForm
from compliance.filters import AuditSessionFilterSet
from compliance.forms_filter import AuditSessionFilterForm
from compliance.reconciliation import audit_asset, close_audit_session, rehome_audit_session_mismatches

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

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.status = 'active'
        return super().form_valid(form)


class AuditSessionDetailView(ObjectDetailView):
    queryset = AuditSession.objects.select_related('location', 'created_by').prefetch_related('audits__asset', 'audits__auditor')
    template_name = 'assets/audits/audit_session_detail.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        session = self.get_object()

        expected_ids = set(session.expected_assets_queryset.values_list('id', flat=True))
        audited_relations = session.audits.select_related('asset', 'location', 'status')
        scanned_ids = set(audited_relations.values_list('asset_id', flat=True))

        ctx['scan_form'] = AuditBarcodeScanForm()
        ctx['total_expected'] = len(expected_ids)
        ctx['total_scanned'] = len(scanned_ids)

        mismatches = []
        matching = []
        for a in audited_relations:
            if a.asset.location != session.location:
                mismatches.append(a)
            else:
                matching.append(a)

        ctx['mismatches'] = mismatches
        ctx['matching'] = matching
        from assets.models import Asset
        ctx['missing_assets'] = Asset.objects.filter(id__in=(expected_ids - scanned_ids)).select_related('location', 'status')
        return ctx


class AssetAuditScanView(View):
    """HTMX AJAX endpoint called on barcode scans within an active campaign."""
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

            import json
            response = render(request, 'assets/audits/includes/audit_scan_success.html', {'asset': asset})
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
    template_name = 'assets/audits/audit_session_close.html'
    service_callable = close_audit_session
    success_message = "Audit session closed. Reconciliation report generated."

    def get_service_kwargs(self, form):
        return {}


class AuditSessionRehomeView(SimplePostView):
    queryset = AuditSession.objects.filter(status='completed')

    def perform_action(self, session, request) -> dict:
        rehome_audit_session_mismatches(session, request.user)
        return {'message': f"All mismatched assets in campaign '{session.name}' have been bulk re-homed to '{session.location.name if session.location else 'Global'}'."}


class AuditSessionDeleteView(ObjectDeleteView):
    queryset = AuditSession.objects.all()
    model = AuditSession
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('compliance:auditsession_list')
