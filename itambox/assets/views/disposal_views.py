from django.urls import reverse_lazy
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _

from itambox.panels import Panel
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
)

from assets.models import AssetDisposal, Asset
from assets.forms import AssetDisposalForm, AssetDisposalFilterForm
from assets.filters import AssetDisposalFilterSet
from assets.tables import AssetDisposalTable


class AssetDisposalListView(ObjectListView):
    queryset = AssetDisposal.objects.select_related('asset', 'asset__asset_type__manufacturer')
    filterset = AssetDisposalFilterSet
    filterset_form = AssetDisposalFilterForm
    table = AssetDisposalTable
    action_buttons = ('add',)


class AssetDisposalDetailView(ObjectDetailView):
    queryset = AssetDisposal.objects.select_related(
        'asset', 'asset__asset_type__manufacturer', 'asset__tenant'
    )
    template_name = 'assets/assetdisposal_detail.html'

    layout = (
        ((Panel('overview', _('Disposal Overview')),),),
        ((Panel('sanitization', _('Data Sanitization')),),),
        ((Panel('financial', _('Financial / Proceeds')),),),
    )


class AssetDisposalEditView(ObjectEditView):
    queryset = AssetDisposal.objects.all()
    model = AssetDisposal
    model_form = AssetDisposalForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:assetdisposal_list'

    def get_initial(self):
        initial = super().get_initial()
        asset_id = self.request.GET.get('asset')
        if asset_id:
            initial['asset'] = asset_id
        return initial


class AssetDisposalDeleteView(ObjectDeleteView):
    queryset = AssetDisposal.objects.all()
    model = AssetDisposal
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:assetdisposal_list')


class AssetDisposeActionView(ObjectEditView):
    """
    Dedicated 'dispose this asset' action view.

    Navigates to via /assets/<pk>/dispose/ — pre-fills the asset field and
    calls the dispose_asset() service on successful form submission so that
    the asset status / disposed_at fields are updated atomically alongside the
    disposal record.
    """
    queryset = AssetDisposal.objects.all()
    model = AssetDisposal
    model_form = AssetDisposalForm
    template_name = 'assets/assetdispose_action.html'
    default_return_url = 'assets:asset_list'

    def get_object(self, queryset=None):
        # The asset pk comes from the URL; the disposal may or may not exist yet.
        asset = get_object_or_404(Asset, pk=self.kwargs['pk'])
        try:
            return AssetDisposal.all_objects.get(asset=asset)
        except AssetDisposal.DoesNotExist:
            return None

    def get_initial(self):
        initial = super().get_initial() or {}
        initial['asset'] = self.kwargs.get('pk')
        return initial

    def form_valid(self, form):
        from assets.services import dispose_asset
        from django.core.exceptions import ValidationError as DjangoValidationError

        data = form.cleaned_data
        asset = data['asset']
        try:
            disposal = dispose_asset(
                asset=asset,
                disposal_method=data['disposal_method'],
                disposal_date=data['disposal_date'],
                data_sanitization_method=data.get('data_sanitization_method', 'none'),
                sanitization_certificate=data.get('sanitization_certificate', ''),
                sanitized_by=data.get('sanitized_by', ''),
                recipient=data.get('recipient', ''),
                proceeds=data.get('proceeds'),
                currency=data.get('currency', ''),
                weee_compliant=data.get('weee_compliant', False),
                notes=data.get('notes', ''),
                user=self.request.user,
            )
        except DjangoValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        messages.success(
            self.request,
            _("Asset '%(asset)s' has been marked as disposed and archived.") % {"asset": asset}
        )
        return HttpResponseRedirect(asset.get_absolute_url())
