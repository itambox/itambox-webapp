import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.http import HttpResponse, HttpResponseRedirect
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.contrib.auth import get_user_model
from django_tables2 import RequestConfig
from django.db.models import Count

from ..models import Asset, InstalledSoftware, StatusLabel, ActivityLog
from .. import forms, tables, filters
from software.tables import InstalledSoftwareTable
from compliance.models import CustodyReceipt

from core.utils import get_paginate_count
from core.panels import Panel
from core.views import (
    ObjectListView, ObjectDetailView, ObjectEditView,
    ObjectDeleteView, ObjectImportView, ObjectBulkEditView,
    ObjectBulkDeleteView, ObjectCloneView,
)
from core.quick_add import QuickAddMixin

from organization.models import AssetHolderAssignment, AssetHolder

import segno

User = get_user_model()


class AssetListView(ObjectListView):
    queryset = Asset.objects.select_related(
        'asset_role',
        'asset_type',
        'asset_type__manufacturer',
        'location',
        'tenant',
        'status',
    ).prefetch_related('tags', 'maintenances')

    def get_table(self):
        table = super().get_table()
        if hasattr(table, 'data') and table.data is not None:
            try:
                pks = [obj.pk for obj in table.data[:500]]
            except Exception:
                pks = []
            if pks:
                ct = ContentType.objects.get_for_model(Asset)
                assignments = AssetHolderAssignment.objects.filter(
                    content_type=ct, object_id__in=pks
                ).select_related('asset_holder')
                assignee_map = {
                    a.object_id: a.asset_holder for a in assignments if a.asset_holder
                }
                from django.urls import reverse
                from django.utils.safestring import mark_safe
                for obj in table.data:
                    holder = assignee_map.get(obj.pk)
                    if holder:
                        try:
                            url = reverse('organization:assetholder_detail', kwargs={'pk': holder.pk})
                            obj._assignee_display = mark_safe(f'<a href="{url}">{holder}</a>')
                        except Exception:
                            obj._assignee_display = str(holder)
                    elif obj.location:
                        obj._assignee_display = f"Location: {obj.location}"
                    else:
                        obj._assignee_display = "—"
        return table

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['asset_holders'] = AssetHolder.objects.all().order_by('last_name', 'first_name')
        return context

    filterset_form = forms.AssetFilterForm
    table = tables.AssetTable
    action_buttons = ('add',)


class AssetDetailView(ObjectDetailView):
    queryset = Asset.objects.select_related(
        'asset_role', 'location', 'asset_type', 'asset_type__manufacturer'
    ).prefetch_related(
        'tags', 'maintenances'
    )

    layout = (
        ((Panel('metrics', 'Asset Overview'),),),
        (
            (Panel('asset_info', 'Asset Details'), Panel('specs', 'Hardware Specifications'), Panel('custom_fields', 'Custom Fields')),
            (Panel('assignment', 'Deployment & Custody'), Panel('financial', 'Financial & Lifecycle'), Panel('audit', 'Audit & Compliance'), Panel('support', 'Support & Warranty Details')),
        ),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        asset = self.get_object()

        assignment = AssetHolderAssignment.objects.filter(
            content_type=ContentType.objects.get_for_model(Asset),
            object_id=asset.pk
        ).select_related('asset_holder').first()

        context['assignment'] = assignment

        sw_qs = InstalledSoftware.objects.filter(asset=asset).select_related('software', 'software__manufacturer')
        sw_table = InstalledSoftwareTable(sw_qs)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(sw_table)
        context['software_table'] = sw_table

        comp_qs = asset.components.select_related('component_type', 'component_type__manufacturer')
        comp_table = tables.ComponentInstanceTable(comp_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(comp_table)
        context['components_table'] = comp_table

        maint_qs = asset.maintenances.all()
        maint_table = tables.AssetMaintenanceTable(maint_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(maint_table)
        context['maintenances_table'] = maint_table

        context['eol_date'] = asset.eol_date
        context['time_to_eol'] = asset.time_to_eol
        context['total_cost_of_ownership'] = asset.total_cost_of_ownership

        custody_receipt = None
        eula_token = None
        if assignment and assignment.asset_holder:
            custody_receipt = CustodyReceipt.objects.filter(asset=asset, holder=assignment.asset_holder).first()
            if custody_receipt:
                eula_token = custody_receipt.token

        context['custody_receipt'] = custody_receipt
        context['eula_token'] = eula_token

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


@login_required
def asset_checkout_modal(request, pk):
    asset = get_object_or_404(Asset, pk=pk)

    if not asset.status or asset.status.slug != 'available':
        return HttpResponse("Asset is not available for assignment.", status=403)

    if request.method == 'POST':
        form = forms.AssetCheckOutForm(request.POST)
        if form.is_valid():
            from .services import checkout_asset
            selected_holder = form.cleaned_data.get('asset_holder')
            selected_location = form.cleaned_data.get('location')

            try:
                assignee = checkout_asset(
                    asset,
                    holder=selected_holder,
                    location=selected_location,
                    user=request.user,
                    request=request
                )
                messages.success(request, f"Asset '{asset}' checked out successfully to {assignee}.")

                response = HttpResponse(status=204)
                response['HX-Trigger'] = json.dumps({
                    "closeModalEvent": None,
                    "assetListUpdated": None,
                    "showMessage": {"message": f"Asset '{asset}' checked out to {assignee}.", "level": "success"}
                })
                return response
            except Exception as e:
                form.add_error(None, str(e))
                context = {'form': form, 'asset': asset, 'request': request}
                return render(request, "assets/includes/asset_checkout_modal.html#checkout-modal-form", context)
        else:
            context = {'form': form, 'asset': asset, 'request': request}
            return render(request, "assets/includes/asset_checkout_modal.html#checkout-modal-form", context)
    else:
        form = forms.AssetCheckOutForm()

    context = {'form': form, 'asset': asset}
    return render(request, 'assets/includes/asset_checkout_modal.html', context)


@login_required
@require_POST
def asset_checkin(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    from .services import checkin_asset

    msg = checkin_asset(asset, user=request.user)
    if msg:
        messages.success(request, f"Asset '{asset}' successfully {msg.lower()}.")
    else:
        messages.warning(request, f"Asset '{asset}' was not checked out to a holder or assigned to a location.")

    return redirect('assets:asset_detail', pk=asset.pk)


@login_required
@require_POST
def asset_audit(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    asset.last_audited = timezone.now()
    asset.last_audited_by = request.user
    asset._changelog_action = 'audit'
    asset._changelog_message = f"Physical presence verified by {request.user.get_full_name() or request.user.username}."
    asset.save(update_fields=['last_audited', 'last_audited_by'])
    ActivityLog.objects.create(
        asset=asset,
        action='audited',
        user=request.user,
        notes=asset._changelog_message
    )
    response = render(request, "assets/includes/asset_audit_badge.html", {'asset': asset})
    response['HX-Trigger'] = json.dumps({
        "playAuditSound": None,
        "showMessage": {"message": f"Asset '{asset.name}' physically audited successfully!", "level": "success"}
    })
    return response


@login_required
def asset_label_print(request, pk, template_id=None):
    asset = get_object_or_404(Asset, pk=pk)

    if template_id:
        from core.models import LabelTemplate
        label_template = get_object_or_404(LabelTemplate, pk=template_id)
        if label_template.template_code:
            from django.template import Template, Context
            tpl = Template(label_template.template_code)
            ctx = Context({'obj': asset, 'barcode_format': label_template.barcode_format})
            html = tpl.render(ctx)
            return HttpResponse(html)

    qr_data = request.build_absolute_uri(asset.get_absolute_url())
    qr = segno.make(qr_data)
    qr_svg = qr.svg_inline(scale=4, border=0)

    context = {
        'asset': asset,
        'qr_svg': qr_svg,
    }
    return render(request, "assets/assets/asset_label.html", context)


@login_required
def bulk_assign_assets(request):
    if request.method != 'POST':
        return HttpResponse(status=405)

    object_pks = request.POST.getlist('pk')
    holder_id = request.POST.get('holder_id')

    if not object_pks or not holder_id:
        messages.error(request, "No assets selected or no holder specified.")
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('assets:asset_list')))

    holder = get_object_or_404(AssetHolder, pk=holder_id)
    assets = Asset.objects.filter(pk__in=object_pks).select_related('status')
    ct = ContentType.objects.get_for_model(Asset)
    in_use_status = StatusLabel.objects.filter(slug='in-use').first()

    from django.db import transaction
    assigned = 0
    skipped = 0

    with transaction.atomic():
        for asset in assets:
            existing = AssetHolderAssignment.objects.filter(
                content_type=ct, object_id=asset.pk
            ).first()
            if existing and existing.asset_holder_id == int(holder_id):
                skipped += 1
                continue

            AssetHolderAssignment.objects.update_or_create(
                content_type=ct,
                object_id=asset.pk,
                defaults={'asset_holder': holder}
            )

            if in_use_status:
                asset.status = in_use_status
                asset._changelog_action = 'checkout'
                asset._changelog_message = f'Bulk assigned to {holder}'
                asset.save(update_fields=['status'])
            assigned += 1

    messages.success(
        request,
        f"{assigned} asset(s) assigned to {holder}. {skipped} already assigned (skipped)."
    )

    if request.htmx:
        response = HttpResponse(status=204)
        response['HX-Trigger'] = json.dumps({
            "assetListUpdated": None,
            "showMessage": {
                "message": f"{assigned} asset(s) assigned to {holder}.",
                "level": "success"
            }
        })
        return response

    return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('assets:asset_list')))


class AssetImportView(ObjectImportView):
    model_form = forms.AssetBulkImportForm


class AssetBulkEditView(ObjectBulkEditView):
    queryset = Asset.objects.all()
    form_class = forms.AssetBulkEditForm


class AssetBulkDeleteView(ObjectBulkDeleteView):
    queryset = Asset.objects.all()
