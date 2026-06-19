"""Scanner-driven bulk check-in & bulk disposal.

Both flows share a "scan basket" page: the user accumulates assets by camera
scan (mobile), USB/keyboard barcode scan or manual entry (desktop), then applies
one action to the whole batch. The batch is processed by a background ``Job``
(mirrors ``bulk_assign_assets``) so it scales and survives request timeouts.

- ``AssetScanActionResolveView`` — JSON endpoint resolving a scanned code to an
  asset plus per-mode eligibility (reused for server-side seeding).
- ``BulkCheckinScanView`` / ``BulkDisposeScanView`` — the basket pages.
- ``bulk_checkin_assets`` / ``bulk_dispose_assets`` — POST submit → enqueue Job.
"""
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import NoReverseMatch, reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from core.managers import get_current_tenant
from core.models import Job
from itambox.views.generic.utils import safe_return_url

from ..depreciation import compute_book_value
from ..models import Asset
from ..scanning import resolve_scanned_code
from .. import forms

logger = logging.getLogger(__name__)

CHECKIN_PERM = 'assets.change_asset'
CHECKOUT_PERM = 'assets.change_asset'
DISPOSE_PERM = 'assets.add_assetdisposal'


def asset_action_payload(asset, mode):
    """Build the JSON-serializable basket-row payload for an asset.

    Shared by the live resolve endpoint and the server-side seed list so the
    front-end renders seeded and scanned rows through one code path.
    """
    active = asset.active_assignment
    assigned = active.assigned_target if active else None

    eligible = True
    warning = None
    book_value = None

    if mode == 'dispose':
        # inline import: avoids a models-package import cycle at module load
        from ..models import AssetDisposal
        if asset.disposed_at is not None or AssetDisposal.all_objects.filter(asset=asset).exists():
            eligible = False
            warning = str(_("Already disposed — will be skipped."))
        bv = compute_book_value(asset)
        book_value = str(bv) if bv is not None else None
    elif mode == 'checkout':
        status_type = asset.status.type if asset.status_id else None
        if status_type in ('in_repair', 'on_order', 'archived'):
            eligible = False
            warning = str(_("Cannot check out — %(status)s.")) % {'status': asset.status.get_type_display()}
        elif assigned is not None:
            warning = str(_("Currently assigned to %(holder)s — will be reassigned.")) % {'holder': assigned}
    else:  # checkin
        if active is None and not asset.location_id:
            eligible = False
            warning = str(_("Not checked out — nothing to return."))

    return {
        'pk': asset.pk,
        'label': str(asset),
        'asset_tag': asset.asset_tag or '',
        'serial': asset.serial_number or '',
        'status': str(asset.status) if asset.status_id else '',
        'assigned_to': str(assigned) if assigned else '',
        'book_value': book_value,
        'eligible': eligible,
        'warning': warning,
    }


@method_decorator(login_required, name='dispatch')
class AssetScanActionResolveView(View):
    """Resolve a scanned code to a basket-row payload within the active tenant.

    GET /assets/scan/resolve-action/?code=<text>&mode=<checkin|dispose>
    """

    def get(self, request, *args, **kwargs):
        # Fail closed: no active tenant means tenant-scoped queries open up.
        if not get_current_tenant() and not request.user.is_superuser:
            return JsonResponse({'found': False}, status=404)

        mode = request.GET.get('mode', 'checkin')
        action_perm = DISPOSE_PERM if mode == 'dispose' else CHECKIN_PERM
        if not request.user.has_perm('assets.view_asset') or not request.user.has_perm(action_perm):
            return JsonResponse({'found': False}, status=403)

        code = request.GET.get('code', '').strip()
        if not code:
            return JsonResponse({'found': False}, status=400)

        asset = resolve_scanned_code(code)
        if asset is None:
            return JsonResponse({'found': False}, status=404)

        payload = asset_action_payload(asset, mode)
        payload['found'] = True
        return JsonResponse(payload)


class _BaseBulkScanView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = 'assets/bulk_scan.html'
    mode = 'checkin'
    form_class = None
    submit_url_name = None
    page_title = ''
    page_pretitle = _('Bulk Actions')

    def _seed_assets(self):
        pks = [p for p in self.request.GET.getlist('pk') if p.isdigit()]
        if not pks:
            return []
        assets = Asset.objects.filter(pk__in=pks).select_related('status', 'location')
        return [asset_action_payload(a, self.mode) for a in assets]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'mode': self.mode,
            'form': self.form_class(),
            'submit_url': reverse(self.submit_url_name),
            'resolve_url': reverse('assets:asset_scan_resolve_action'),
            'seed_payloads': self._seed_assets(),
            'title': self.page_title,
            'pretitle': self.page_pretitle,
        })
        return context


class BulkCheckinScanView(_BaseBulkScanView):
    permission_required = (CHECKIN_PERM,)
    mode = 'checkin'
    form_class = forms.AssetBulkCheckInForm
    submit_url_name = 'assets:asset_bulk_checkin'
    page_title = _('Bulk Check-in')


class BulkCheckoutScanView(_BaseBulkScanView):
    permission_required = (CHECKOUT_PERM,)
    mode = 'checkout'
    form_class = forms.AssetBulkCheckOutForm
    submit_url_name = 'assets:asset_bulk_checkout'
    page_title = _('Bulk Check-out')


class BulkDisposeScanView(_BaseBulkScanView):
    permission_required = (DISPOSE_PERM,)
    mode = 'dispose'
    form_class = forms.AssetBulkDisposeForm
    submit_url_name = 'assets:asset_bulk_dispose'
    page_title = _('Bulk Disposal')


def _job_redirect(request, job, message):
    messages.success(request, message)
    try:
        redirect_url = reverse('job_detail', kwargs={'pk': job.pk})
    except NoReverseMatch:
        redirect_url = f"/jobs/{job.pk}/"
    if request.htmx:
        response = HttpResponse(status=204)
        response['HX-Redirect'] = redirect_url
        return response
    return HttpResponseRedirect(redirect_url)


def _enqueue(job, task_path, *task_args):
    """Dispatch via on_commit unless running inline (Q_CLUSTER sync, e.g. tests)."""
    from django_q.tasks import async_task
    if getattr(settings, 'Q_CLUSTER', {}).get('sync', False):
        async_task(task_path, *task_args)
    else:
        transaction.on_commit(lambda: async_task(task_path, *task_args))


@login_required
def bulk_checkin_assets(request):
    if not request.user.has_perm(CHECKIN_PERM):
        return HttpResponse(status=403)
    if request.method != 'POST':
        return HttpResponse(status=405)

    object_pks = request.POST.getlist('pk')
    fallback = reverse('assets:asset_bulk_checkin_scan')
    if not object_pks:
        messages.error(request, _("No assets selected for check-in."))
        return HttpResponseRedirect(safe_return_url(request, request.META.get('HTTP_REFERER'), fallback))

    current_tenant = get_current_tenant()
    tenant_id = current_tenant.pk if current_tenant else None

    job = Job.objects.create(
        name=f"Bulk Check-in: {len(object_pks)} Assets",
        tenant=current_tenant,
        model=ContentType.objects.get_for_model(Asset),
        status=Job.STATUS_PENDING,
    )

    _enqueue(
        job,
        'core.tasks.bulk_checkin_task',
        job.pk,
        object_pks,
        request.user.pk,
        tenant_id,
        request.POST.get('status') or None,
        request.POST.get('location') or None,
        request.POST.get('checkin_date') or None,
        request.POST.get('notes', ''),
    )

    return _job_redirect(
        request, job,
        _("Asynchronous check-in job '%(job)s' enqueued. Tracking progress in real-time.") % {"job": job.name},
    )


@login_required
def bulk_dispose_assets(request):
    if not request.user.has_perm(DISPOSE_PERM):
        return HttpResponse(status=403)
    if request.method != 'POST':
        return HttpResponse(status=405)

    object_pks = request.POST.getlist('pk')
    fallback = reverse('assets:asset_bulk_dispose_scan')
    if not object_pks:
        messages.error(request, _("No assets selected for disposal."))
        return HttpResponseRedirect(safe_return_url(request, request.META.get('HTTP_REFERER'), fallback))

    disposal_date = request.POST.get('disposal_date') or None
    if not disposal_date:
        messages.error(request, _("A disposal date is required."))
        return HttpResponseRedirect(safe_return_url(request, request.META.get('HTTP_REFERER'), fallback))

    disposal_kwargs = {
        'disposal_method': request.POST.get('disposal_method', 'destruction'),
        'disposal_date': disposal_date,
        'data_sanitization_method': request.POST.get('data_sanitization_method', 'none'),
        'sanitization_certificate': request.POST.get('sanitization_certificate', ''),
        'sanitized_by': request.POST.get('sanitized_by', ''),
        'recipient': request.POST.get('recipient', ''),
        'currency': request.POST.get('currency', ''),
        'weee_compliant': bool(request.POST.get('weee_compliant')),
        'notes': request.POST.get('notes', ''),
    }
    proceeds_map = {pk: (request.POST.get(f'proceeds_{pk}') or None) for pk in object_pks}

    current_tenant = get_current_tenant()
    tenant_id = current_tenant.pk if current_tenant else None

    job = Job.objects.create(
        name=f"Bulk Disposal: {len(object_pks)} Assets",
        tenant=current_tenant,
        model=ContentType.objects.get_for_model(Asset),
        status=Job.STATUS_PENDING,
    )

    _enqueue(
        job,
        'core.tasks.bulk_dispose_task',
        job.pk,
        object_pks,
        request.user.pk,
        tenant_id,
        disposal_kwargs,
        proceeds_map,
    )

    return _job_redirect(
        request, job,
        _("Asynchronous disposal job '%(job)s' enqueued. Tracking progress in real-time.") % {"job": job.name},
    )


@login_required
def bulk_checkout_assets(request):
    if not request.user.has_perm(CHECKOUT_PERM):
        return HttpResponse(status=403)
    if request.method != 'POST':
        return HttpResponse(status=405)

    object_pks = request.POST.getlist('pk')
    fallback = reverse('assets:asset_bulk_checkout_scan')
    if not object_pks:
        messages.error(request, _("No assets selected for check-out."))
        return HttpResponseRedirect(safe_return_url(request, request.META.get('HTTP_REFERER'), fallback))

    targets = (
        ('assetholder', request.POST.get('asset_holder') or None),
        ('location', request.POST.get('location') or None),
        ('asset', request.POST.get('asset_target') or None),
    )
    chosen = [(t, i) for t, i in targets if i]
    if len(chosen) != 1:
        messages.error(request, _("Select exactly one check-out target: a holder, a location, or a parent asset."))
        return HttpResponseRedirect(safe_return_url(request, request.META.get('HTTP_REFERER'), fallback))
    target_type_str, target_pk = chosen[0]

    current_tenant = get_current_tenant()
    tenant_id = current_tenant.pk if current_tenant else None

    job = Job.objects.create(
        name=f"Bulk Check-out: {len(object_pks)} Assets",
        tenant=current_tenant,
        model=ContentType.objects.get_for_model(Asset),
        status=Job.STATUS_PENDING,
    )

    _enqueue(
        job,
        'core.tasks.bulk_checkout_task',
        job.pk,
        object_pks,
        target_type_str,
        target_pk,
        request.user.pk,
        request.POST.get('notes', ''),
        request.POST.get('expected_checkin') or None,
        tenant_id,
        request.POST.get('status') or None,
        request.POST.get('checkout_date') or None,
    )

    return _job_redirect(
        request, job,
        _("Asynchronous check-out job '%(job)s' enqueued. Tracking progress in real-time.") % {"job": job.name},
    )
