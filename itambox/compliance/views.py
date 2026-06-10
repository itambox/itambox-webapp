import hashlib
from django.shortcuts import render, get_object_or_404
from django.urls import reverse_lazy
from django.utils import timezone

from itambox.panels import Panel
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectCloneView
)

from .models import CustodyReceipt, CustodyTemplate
from .forms import CustodyTemplateForm
from .tables import CustodyTemplateTable
from assets.views.maintenance_views import (  # noqa: F401
    AssetMaintenanceListView, AssetMaintenanceDetailView, AssetMaintenanceEditView,
    AssetMaintenanceCloneView, AssetMaintenanceDeleteView,
)


def custody_eula_sign(request, token):
    from django.conf import settings
    if getattr(settings, 'REQUIRE_CUSTODY_SIGNIN', False) and not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login
        return redirect_to_login(request.get_full_path())

    receipt = get_object_or_404(CustodyReceipt, token=token)

    if receipt.signature_provider != 'local':
        from compliance.registry import signature_providers
        provider = signature_providers.get(receipt.signature_provider)
        if provider:
            url = provider.initiate_signature(receipt, request)
            if request.GET.get('onsite') == 'true':
                from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
                u = urlparse(url)
                q = dict(parse_qsl(u.query))
                q['onsite'] = 'true'
                url = urlunparse(u._replace(query=urlencode(q)))
            from django.shortcuts import redirect
            return redirect(url)

    if receipt.created_date and (timezone.now() - receipt.created_date).days > 7:
        return render(request, "compliance/custody/sign_error.html", {"error": "This custody acceptance link has expired (7 day limit)."})

    if receipt.acceptance_status == CustodyReceipt.STATUS_ACCEPTED:
        return render(request, "compliance/custody/receipt_success.html", {"receipt": receipt, "asset": receipt.asset, "holder": receipt.holder})

    if receipt.acceptance_status == CustodyReceipt.STATUS_DECLINED:
        return render(request, "compliance/custody/sign_error.html", {"error": "This custody transfer has been declined."})

    asset = receipt.asset
    holder = receipt.holder

    if request.method == 'POST':
        from django.db import transaction

        with transaction.atomic():
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


class CustodyTemplateListView(ObjectListView):
    queryset = CustodyTemplate.objects.select_related('tenant', 'tenant_group').prefetch_related('tags')
    table = CustodyTemplateTable
    action_buttons = ('add',)


class CustodyTemplateDetailView(ObjectDetailView):
    queryset = CustodyTemplate.objects.select_related('tenant', 'tenant_group').prefetch_related('tags')
    template_name = 'compliance/custodytemplates/custodytemplate_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        template = self.get_object()

        from django_tables2 import RequestConfig
        from itambox.utils import get_paginate_count
        from .tables import CustodyReceiptTable

        receipts_qs = template.receipts.all().select_related('asset', 'holder', 'custody_template')
        receipts_table = CustodyReceiptTable(receipts_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(receipts_table)
        context['receipts_table'] = receipts_table

        return context



class CustodyTemplateEditView(ObjectEditView):
    queryset = CustodyTemplate.objects.all()
    model = CustodyTemplate
    model_form = CustodyTemplateForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'compliance:custodytemplate_list'

    def get_initial(self):
        initial = super().get_initial()
        if 'category' in self.request.GET:
            initial['category'] = self.request.GET['category']
        return initial


class CustodyTemplateCloneView(CustodyTemplateEditView, ObjectCloneView):
    model = CustodyTemplate


class CustodyTemplateDeleteView(ObjectDeleteView):
    queryset = CustodyTemplate.objects.all()
    model = CustodyTemplate
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('compliance:custodytemplate_list')


def custody_template_preview(request, pk):
    template = get_object_or_404(CustodyTemplate, pk=pk)

    from organization.models import AssetHolder
    from assets.models import Asset

    asset = Asset.objects.first()
    if not asset:
        asset = Asset(
            name="[Preview] Professional Corporate Laptop (M3 Max)",
            asset_tag="PREVIEW-LT-099",
            serial_number="PREVIEW-SN-88291-XYZ"
        )
    
    holder = AssetHolder.objects.first()
    if not holder:
        holder = AssetHolder(
            first_name="Jane",
            last_name="Doe",
            email="jane.doe@organization.com",
            upn="jane.doe"
        )

    receipt = CustodyReceipt(
        custody_template=template,
        signature_provider=template.signature_provider,
        eula_text=template.eula_text,
        disclaimer=template.disclaimer,
        qms_reference=template.qms_reference,
        acceptance_status=CustodyReceipt.STATUS_PENDING
    )

    return render(request, "compliance/custody/sign_portal.html", {
        "asset": asset,
        "holder": holder,
        "receipt": receipt,
        "token": "preview",
        "is_preview": True
    })
