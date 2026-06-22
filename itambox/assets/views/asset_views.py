import json
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.http import HttpResponse, HttpResponseRedirect
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse, reverse_lazy, NoReverseMatch
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.utils.translation import gettext_lazy as _
from django_tables2 import RequestConfig
from django.db.models import Count, Prefetch
from django.db import transaction

logger = logging.getLogger(__name__)

from ..models import Asset, StatusLabel, AssetAssignment
from assets.choices import RequestStatusChoices
from software.models import InstalledSoftware
from .. import forms, tables, filters
from ..services import checkout_asset, checkin_asset
from software.tables import InstalledSoftwareTable
from compliance.models import CustodyReceipt
from compliance.reconciliation import audit_asset_from_form
from inventory.models import AccessoryAssignment, ConsumableAssignment
from inventory.tables import AccessoryAssignmentTable, ConsumableAssignmentTable

from itambox.utils import get_paginate_count
from itambox.panels import Panel
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView,
    ObjectDeleteView, ObjectImportView, ObjectBulkEditView,
    ObjectBulkDeleteView, ObjectCloneView,
)
from itambox.views.generic.service_views import GenericTransactionView, SimplePostView
from itambox.views.generic.utils import safe_return_url
from itambox.quick_add import QuickAddMixin

from organization.models import AssetHolder

User = get_user_model()


class AssetListView(ObjectListView):
    queryset = Asset.objects.select_related(
        'asset_role',
        'asset_type',
        'asset_type__manufacturer',
        'asset_type__category',
        'location',
        'tenant',
        'status',
        'supplier',
    ).prefetch_related(
        'tags',
        'maintenances',
        Prefetch(
            'assignments',
            queryset=AssetAssignment.objects.filter(is_active=True).select_related(
                'assigned_user', 'assigned_location', 'assigned_asset'
            ),
            to_attr='prefetched_active_assignments',
        ),
    )

    filterset = filters.AssetFilterSet
    filterset_form = forms.AssetFilterForm
    table = tables.AssetTable
    action_buttons = ('add',)


class AssetDetailView(ObjectDetailView):
    queryset = Asset.objects.select_related(
        'asset_role',
        'location',
        'asset_type',
        'asset_type__manufacturer',
        'asset_type__custom_fieldset',
    ).prefetch_related(
        'tags',
        'maintenances',
        Prefetch(
            'assignments',
            queryset=AssetAssignment.objects.filter(is_active=True).select_related(
                'assigned_user', 'assigned_location', 'assigned_asset'
            ),
            to_attr='prefetched_active_assignments',
        ),
        'asset_type__custom_fieldset__fields',
        'component_allocations__component',
        'component_allocations__component__manufacturer',
    )

    layout = (
        ((Panel('metrics', _('Asset Overview')),),),
        (
            (Panel('asset_info', _('Asset Details')), Panel('specs', _('Hardware Specifications')), Panel('custom_fields', _('Custom Fields'))),
            (Panel('assignment', _('Deployment & Custody')), Panel('financial', _('Financial & Lifecycle')), Panel('audit', _('Audit & Compliance')), Panel('support', _('Support & Warranty Details'))),
        ),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        asset = self.get_object()

        active_assignment = asset.active_assignment
        context['assignment'] = active_assignment

        sw_qs = InstalledSoftware.objects.filter(asset=asset).select_related('software', 'software__manufacturer')
        sw_table = InstalledSoftwareTable(sw_qs)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(sw_table)
        context['software_table'] = sw_table

        comp_qs = asset.component_allocations.filter(deleted_at__isnull=True).select_related('component', 'component__manufacturer')
        comp_table = tables.ComponentAllocationTable(comp_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(comp_table)
        context['components_table'] = comp_table

        maint_qs = asset.maintenances.select_related('asset', 'supplier')
        maint_table = tables.AssetMaintenanceTable(maint_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(maint_table)
        context['maintenances_table'] = maint_table

        acc_qs = AccessoryAssignment.objects.filter(assigned_asset=asset).select_related('accessory', 'accessory__manufacturer')
        acc_table = AccessoryAssignmentTable(acc_qs, request=self.request)
        acc_table.exclude = ('assigned_to',)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(acc_table)
        context['accessory_table'] = acc_table

        con_qs = ConsumableAssignment.objects.filter(assigned_asset=asset).select_related('consumable', 'consumable__manufacturer')
        con_table = ConsumableAssignmentTable(con_qs, request=self.request)
        con_table.exclude = ('assigned_to',)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(con_table)
        context['consumable_table'] = con_table

        # Requests
        from ..models import AssetRequest
        req_qs = AssetRequest.objects.filter(asset=asset).select_related('requester', 'asset', 'asset_type')
        requests_table = tables.AssetRequestTable(req_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(requests_table)
        context['requests_table'] = requests_table

        # Audits
        from compliance.models import AssetAudit
        audit_qs = AssetAudit.objects.filter(asset=asset).select_related('session', 'auditor', 'location', 'status')
        audits_table = tables.AssetAuditTable(audit_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(audits_table)
        context['audits_table'] = audits_table

        # Custody Receipts
        from compliance.models import CustodyReceipt
        from compliance.tables import CustodyReceiptTable
        receipt_qs = CustodyReceipt.objects.filter(asset=asset).select_related('asset', 'holder', 'custody_template')
        receipts_table = CustodyReceiptTable(receipt_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(receipts_table)
        context['receipts_table'] = receipts_table

        # License Seat Assignments
        from licenses.models import LicenseSeatAssignment
        from licenses.tables import LicenseSeatAssignmentTable
        license_qs = LicenseSeatAssignment.objects.filter(asset=asset).select_related('license', 'asset', 'assigned_holder')
        license_seats_table = LicenseSeatAssignmentTable(license_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(license_seats_table)
        context['license_seats_table'] = license_seats_table

        context['eol_date'] = asset.eol_date
        context['time_to_eol'] = asset.time_to_eol
        context['total_cost_of_ownership'] = asset.total_cost_of_ownership

        from assets.depreciation import resolve_policy
        policy, rung = resolve_policy(asset)
        context['resolved_depreciation_policy'] = str(policy) if policy else None
        context['resolved_depreciation_rung'] = rung

        custody_receipt = None
        eula_token = None
        if active_assignment and active_assignment.assigned_target:
            from organization.models import AssetHolder
            if isinstance(active_assignment.assigned_target, AssetHolder):
                custody_receipt = CustodyReceipt.objects.filter(
                    asset=asset, holder=active_assignment.assigned_target
                ).order_by('-created_date').first()
                if custody_receipt:
                    eula_token = custody_receipt.token

        context['custody_receipt'] = custody_receipt
        context['eula_token'] = eula_token

        # Check if current user has an approved request for this asset
        approved_request = None
        if self.request.user.is_authenticated:
            from assets.models import AssetRequest
            approved_request_qs = AssetRequest.objects.filter(
                asset=asset,
                status=RequestStatusChoices.APPROVED
            )
            for req in approved_request_qs:
                is_requester = req.requester == self.request.user
                is_assigned = req.assigned_user and req.assigned_user.user == self.request.user
                if is_requester or is_assigned or self.request.user.is_staff:
                    approved_request = req
                    break
        context['approved_request'] = approved_request

        warranty_qs = asset.warranties.select_related('asset')
        warranties_table = tables.WarrantyTable(warranty_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(warranties_table)
        context['warranties_table'] = warranties_table

        reservation_qs = asset.reservations.select_related('asset', 'reserved_for', 'created_by')
        reservations_table = tables.AssetReservationTable(reservation_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(reservations_table)
        context['reservations_table'] = reservations_table

        from assets.models import AssetDisposal  # inline import: avoid touching the module-level import block
        context['disposal_obj'] = AssetDisposal.objects.filter(asset=asset).first()

        return context


class AssetEditView(ObjectEditView):
    queryset = Asset.objects.all()
    model = Asset
    model_form = forms.AssetForm
    template_name = 'generic/object_edit.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def post(self, request, *args, **kwargs):
        if request.headers.get('HX-Request') and '_reload' in request.POST:
            self.object = self.get_object()
            form = self.get_form()
            return render(request, 'htmx/crispy_form.html', {'form': form})
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        if getattr(self, 'object', None) is not None:
            form.create_inline_warranty(self.object)
        return response


class AssetDeleteView(ObjectDeleteView):
    queryset = Asset.objects.all()
    model = Asset
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:asset_list')


class AssetCloneView(ObjectCloneView):
    model = Asset
    model_form = forms.AssetForm
    template_name = 'generic/object_edit.html'

    def pre_save_clone(self, original, cloned):
        cloned.asset_tag = ''


class AssetCheckoutView(GenericTransactionView):
    permission_required = ('assets.change_asset',)
    queryset = Asset.objects.all()
    model_form = forms.AssetCheckOutForm
    service_callable = checkout_asset
    context_object_name = 'asset'
    template_name = 'assets/includes/asset_checkout_modal.html'
    error_partial = 'assets/includes/asset_checkout_modal.html#checkout-modal-form'
    success_message = "Asset checked out successfully."
    hx_trigger = "assetListUpdated"
    hx_redirect_on_success = True
    form_field_map = {'asset_holder': 'holder'}
    form_exclude_fields = ('target_type',)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        del kwargs['instance']
        asset = self.get_object()
        kwargs['asset'] = asset

        request_id = self.request.GET.get('request_id')
        if request_id:
            from ..models import AssetRequest
            try:
                asset_request = AssetRequest.objects.get(pk=request_id)
                if 'initial' not in kwargs:
                    kwargs['initial'] = {}
                initial = kwargs['initial']
                if asset_request.assigned_user:
                    initial['target_type'] = 'holder'
                    initial['asset_holder'] = asset_request.assigned_user
                elif asset_request.assigned_location:
                    initial['target_type'] = 'location'
                    initial['location'] = asset_request.assigned_location
                elif asset_request.assigned_asset:
                    initial['target_type'] = 'asset'
                    initial['asset_target'] = asset_request.assigned_asset
            except AssetRequest.DoesNotExist:
                pass

        return kwargs

    def get_success_message(self, result=None):
        return f"Asset '{self.get_object()}' checked out to {result}."

    def post_service(self, obj, form, result):
        # Fulfill the originating request, but only when it actually references
        # this asset (or this asset's type) — never an arbitrary request id.
        request_id = self.request.GET.get('request_id')
        if not request_id:
            return
        from django.db.models import Q
        from ..models import AssetRequest
        request_filter = Q(asset=obj)
        if obj.asset_type_id:
            request_filter |= Q(asset__isnull=True, asset_type_id=obj.asset_type_id)
        asset_request = AssetRequest.objects.filter(
            pk=request_id,
            status__in=(RequestStatusChoices.PENDING, RequestStatusChoices.APPROVED),
        ).filter(request_filter).first()
        if asset_request:
            asset_request.status = RequestStatusChoices.FULFILLED
            asset_request.response_date = timezone.now()
            asset_request.responded_by = self.request.user
            asset_request.asset = obj
            asset_request.save(update_fields=['status', 'response_date', 'responded_by', 'asset'])


class AssetCheckinView(GenericTransactionView):
    permission_required = ('assets.change_asset',)
    queryset = Asset.objects.all()
    model_form = forms.AssetCheckInForm
    service_callable = checkin_asset
    context_object_name = 'asset'
    template_name = 'assets/includes/asset_checkin_modal.html'
    error_partial = 'assets/includes/asset_checkin_modal.html#checkin-modal-form'
    success_message = "Asset checked in successfully."
    hx_trigger = "assetListUpdated"
    hx_redirect_on_success = True

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        del kwargs['instance']
        kwargs['asset'] = self.get_object()
        return kwargs

    def get_success_message(self, result=None):
        return f"Asset '{self.get_object()}' checked in successfully."


class AssetAuditView(GenericTransactionView):
    """Modal form for standalone asset verification from the detail page."""
    queryset = Asset.objects.all()
    permission_required = 'compliance.add_assetaudit'
    context_object_name = 'asset'
    template_name = 'assets/includes/asset_audit_modal.html'
    error_partial = 'assets/includes/asset_audit_modal.html#audit-modal-form'
    success_message = "Asset physically verified."
    hx_trigger = "assetAuditRecorded"

    model_form = forms.AssetAuditConfirmForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.pop('instance', None)
        kwargs['asset'] = self.get_object()
        return kwargs

    def get_service_kwargs(self, form):
        return {
            'location': form.cleaned_data['location'],
            'status': form.cleaned_data['status'],
            'notes': form.cleaned_data.get('notes', ''),
        }

    def _htmx_success_response(self, obj, result=None):
        """Return OOB-swapped badge + closeModal + playAuditSound triggers."""
        badge_html = render(
            self.request,
            "assets/includes/asset_audit_badge.html",
            {'asset': obj},
        ).content.decode()
        oob_fragment = (
            f'<div id="asset-audit-badge-container" hx-swap-oob="outerHTML:#asset-audit-badge-container">'
            f'{badge_html}</div>'
        )
        response = HttpResponse(oob_fragment, content_type='text/html')
        response['HX-Trigger'] = json.dumps({
            "closeModalEvent": None,
            "playAuditSound": None,
            "showMessage": {
                "message": str(self.get_success_message(result)),
                "level": "success",
            },
        })
        return response

    def get_success_message(self, result=None):
        asset = self.get_object()
        if result and result.get('session'):
            return _("'%(asset)s' verified inside campaign '%(campaign)s'.") % {"asset": asset.name, "campaign": result['session'].name}
        return _("'%(asset)s' standalone verification recorded.") % {"asset": asset.name}

    service_callable = audit_asset_from_form


@login_required
def asset_label_print(request, pk, template_id=None):
    asset = get_object_or_404(Asset, pk=pk)
    if not request.user.has_perm("assets.view_asset", obj=asset):
        raise PermissionDenied

    if template_id:
        from extras.models import LabelTemplate
        from core.tasks.labels import render_labels_pdf
        label_template = get_object_or_404(LabelTemplate, pk=template_id)
        # Use the same engine as the bulk print job — synchronously, no background Job.
        pdf_bytes = render_labels_pdf([asset], label_template)
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'inline; filename="label_{asset.asset_tag or asset.pk}.pdf"'
        return response

    # No template chosen — render the on-screen preview using the SAME label card
    # the print engine produces, so the preview matches the printed (bulk) output.
    from core.tasks.labels import generate_base64_barcode, _default_label_card
    barcode_uri = generate_base64_barcode(asset, 'qr')
    context = {
        'asset': asset,
        'label_card': _default_label_card(asset, barcode_uri),
    }
    return render(request, "assets/assets/asset_label.html", context)


class AssetBulkEditView(ObjectBulkEditView):
    queryset = Asset.objects.all()
    form_class = forms.AssetBulkEditForm


class AssetBulkDeleteView(ObjectBulkDeleteView):
    queryset = Asset.objects.all()


@login_required
def bulk_print_labels(request):
    if not request.user.has_perm('assets.change_asset'):
        return HttpResponse(status=403)
    if request.method != 'POST':
        return HttpResponse(status=405)

    object_pks = request.POST.getlist('pk')
    template_id = request.POST.get('template_id')
    layout_mode = request.POST.get('layout_mode', 'roll')

    if not object_pks:
        messages.error(request, _("No assets selected for label printing."))
        return HttpResponseRedirect(safe_return_url(request, request.META.get('HTTP_REFERER'), reverse('assets:asset_list')))

    try:
        template_id = int(template_id)
    except (TypeError, ValueError):
        messages.error(request, _("No valid label template specified."))
        return HttpResponseRedirect(safe_return_url(request, request.META.get('HTTP_REFERER'), reverse('assets:asset_list')))

    from django_q.tasks import async_task
    from django.contrib.contenttypes.models import ContentType
    from core.models import Job
    from core.managers import get_current_tenant
    from django.conf import settings
    from django.db import transaction

    ct = ContentType.objects.get_for_model(Asset)
    current_tenant = get_current_tenant()
    tenant_id = current_tenant.pk if current_tenant else None

    job = Job.objects.create(
        name=f"Label Batch Generation: {len(object_pks)} Assets",
        tenant=current_tenant,
        model=ct,
        status=Job.STATUS_PENDING
    )

    if getattr(settings, 'Q_CLUSTER', {}).get('sync', False):
        async_task(
            'core.tasks.labels.generate_label_pdf_batch_task',
            job.pk,
            object_pks,
            template_id,
            layout_mode,
            request.user.pk,
            tenant_id
        )
    else:
        transaction.on_commit(
            lambda: async_task(
                'core.tasks.labels.generate_label_pdf_batch_task',
                job.pk,
                object_pks,
                template_id,
                layout_mode,
                request.user.pk,
                tenant_id
            )
        )

    try:
        redirect_url = reverse('job_detail', kwargs={'pk': job.pk})
    except NoReverseMatch:
        redirect_url = f"/jobs/{job.pk}/"

    messages.success(
        request,
        _("Asynchronous label generation job '%(job)s' enqueued successfully! Tracking progress in real-time.") % {"job": job.name}
    )

    if request.htmx:
        response = HttpResponse(status=204)
        response['HX-Redirect'] = redirect_url
        return response

    return HttpResponseRedirect(redirect_url)
