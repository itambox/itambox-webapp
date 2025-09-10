import hashlib
from django.shortcuts import render, get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone

from assetbox.panels import Panel
from assetbox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView
)

from .models import AssetMaintenance, CustodyReceipt
from .filters import AssetMaintenanceFilterSet
from .forms import AssetMaintenanceForm, AssetMaintenanceFilterForm
from .tables import AssetMaintenanceTable

class AssetMaintenanceListView(ObjectListView):
    queryset = AssetMaintenance.objects.select_related('asset')
    filterset = AssetMaintenanceFilterSet
    filterset_form = AssetMaintenanceFilterForm
    table = AssetMaintenanceTable
    action_buttons = ('add',)


class AssetMaintenanceDetailView(ObjectDetailView):
    queryset = AssetMaintenance.objects.select_related('asset')
    template_name = 'compliance/assetmaintenances/assetmaintenance_detail.html'

    layout = (
        ((Panel('metrics', 'Maintenance Overview'),),),
        ((Panel('info', 'Maintenance Details'),),),
    )


class AssetMaintenanceEditView(ObjectEditView):
    queryset = AssetMaintenance.objects.all()
    model = AssetMaintenance
    model_form = AssetMaintenanceForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'compliance:assetmaintenance_list'

    def get_initial(self):
        initial = super().get_initial()
        # Prepopulate asset if passed in GET params
        asset_id = self.request.GET.get('asset')
        if asset_id:
            initial['asset'] = asset_id
        return initial


class AssetMaintenanceDeleteView(ObjectDeleteView):
    queryset = AssetMaintenance.objects.all()
    model = AssetMaintenance
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('compliance:assetmaintenance_list')


def custody_eula_sign(request, token):
    receipt = get_object_or_404(CustodyReceipt, token=token)

    if receipt.created_date and (timezone.now() - receipt.created_date).days > 7:
        return render(request, "compliance/custody/sign_error.html", {"error": "This custody acceptance link has expired (7 day limit)."})

    if receipt.acceptance_status == CustodyReceipt.STATUS_ACCEPTED:
        return render(request, "compliance/custody/receipt_success.html", {"receipt": receipt, "asset": receipt.asset, "holder": receipt.holder})

    if receipt.acceptance_status == CustodyReceipt.STATUS_DECLINED:
        return render(request, "compliance/custody/sign_error.html", {"error": "This custody transfer has been declined."})

    asset = receipt.asset
    holder = receipt.holder

    if request.method == 'POST':
        action = request.POST.get('action', 'accept')
        signature_data = request.POST.get('signature_canvas')

        if action == 'decline':
            receipt.acceptance_status = CustodyReceipt.STATUS_DECLINED
            receipt.save(update_fields=['acceptance_status', 'updated_at'])
            return render(request, "compliance/custody/sign_error.html", {"error": "You have declined the custody transfer."})

        if not signature_data or signature_data == 'empty':
            return render(request, "compliance/custody/sign_portal.html", {
                "asset": asset,
                "holder": holder,
                "token": token,
                "receipt": receipt,
                "error": "Please provide a valid signature."
            })

        timestamp_str = timezone.now().isoformat()
        raw_to_hash = f"{holder.upn}|{asset.asset_tag}|{timestamp_str}|{signature_data}"
        verification_hash = hashlib.sha256(raw_to_hash.encode('utf-8')).hexdigest()

        receipt.accepted = True
        receipt.accepted_date = timezone.now()
        receipt.acceptance_method = 'digital'
        receipt.acceptance_status = CustodyReceipt.STATUS_ACCEPTED
        receipt.signature_canvas = signature_data
        receipt.signature_data = signature_data
        receipt.signature_hash = verification_hash
        receipt.verification_hash = verification_hash
        receipt.eula_version = '1.0'
        receipt.signed_at = timezone.now()
        receipt.save()

        try:
            from django.db import transaction
            transaction.on_commit(lambda: _safe_dispatch_custody(receipt))
        except Exception:
            _safe_dispatch_custody(receipt)

        asset._changelog_action = 'audit'
        asset._changelog_message = f"EULA digital custody receipt accepted. SHA-256 Hash: {verification_hash[:16]}..."
        asset.save()

        return render(request, "compliance/custody/receipt_success.html", {"receipt": receipt, "asset": asset, "holder": holder})

    return render(request, "compliance/custody/sign_portal.html", {"asset": asset, "holder": holder, "token": token, "receipt": receipt})


def _safe_dispatch_custody(receipt):
    try:
        from core.events import dispatch_event
        dispatch_event(CustodyReceipt, receipt, action='update')
    except Exception:
        pass
