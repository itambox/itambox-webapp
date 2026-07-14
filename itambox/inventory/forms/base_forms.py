from django import forms
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from core.managers import get_current_tenant
from organization.models import Location, AssetHolder
from assets.models import Asset


class BaseCheckoutForm(forms.Form):
    assigned_holder = forms.ModelChoiceField(
        queryset=AssetHolder.objects.all().order_by('last_name', 'first_name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Assign to Asset Holder")
    )
    assigned_location = forms.ModelChoiceField(
        queryset=Location.objects.all().select_related('site').order_by('site__name', 'name'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Assign to Location")
    )
    assigned_asset = forms.ModelChoiceField(
        queryset=Asset.objects.all().order_by('asset_tag'),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label=_("Assign to Asset")
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        label=_("Notes")
    )

    def __init__(self, *args, **kwargs):
        tenant = kwargs.pop('tenant', None)
        item = kwargs.pop('item', None)
        stock_model = kwargs.pop('stock_model', None)
        super().__init__(*args, **kwargs)
        # ADR-0001 phase 4b: a checkout assigns INTO the acting tenant, so
        # targets scope to the ACTIVE tenant (falling back to the item's
        # tenant outside a request context — identical for the owner flow,
        # correct for a grantee consuming a shared pool).
        scope_tenant = get_current_tenant() or tenant
        if scope_tenant:
            # Rescope every tenant-owned FK choice. These querysets are
            # import-frozen and unscoped, so without this a checkout could
            # target (and expose in the dropdown) another tenant's holders,
            # locations or assets. Subclasses contribute their own Location
            # source field (source_location / from_location), scoped below.
            self.fields['assigned_holder'].queryset = AssetHolder.objects.filter(
                tenant=scope_tenant).order_by('last_name', 'first_name')
            self.fields['assigned_location'].queryset = Location.objects.filter(
                tenant=scope_tenant).select_related('site').order_by('site__name', 'name')
            self.fields['assigned_asset'].queryset = Asset.objects.filter(
                tenant=scope_tenant).order_by('asset_tag')
            source_q = Q(tenant=scope_tenant)
            shared_location_ids = self._shared_pool_location_ids(item, stock_model)
            if shared_location_ids:
                source_q |= Q(pk__in=shared_location_ids)
            for loc_field in ('source_location', 'from_location'):
                if loc_field in self.fields:
                    # _base_manager: a granted pool's location belongs to the
                    # OWNING tenant and would be hidden by scoped managers
                    # (incl. the ModelChoiceField monkey-patch).
                    self.fields[loc_field].queryset = Location._base_manager.filter(
                        source_q).order_by('name')

    @staticmethod
    def _shared_pool_location_ids(item, stock_model):
        """Locations of THIS item's pools shared to the active tenant."""
        if item is None or stock_model is None or item.pk is None:
            return []
        active = get_current_tenant()
        if active is None:
            return []
        # inline import: breaks an inventory <-> organization form-import cycle
        from organization.access import shared_resource_ids
        item_field = stock_model._meta.model_name.replace('stock', '')
        return list(
            stock_model._base_manager.filter(
                pk__in=shared_resource_ids(stock_model, active),
                **{f'{item_field}_id': item.pk},
            ).values_list('location_id', flat=True)
        )

    def clean(self):
        cleaned_data = super().clean()
        holder = cleaned_data.get('assigned_holder')
        location = cleaned_data.get('assigned_location')
        asset = cleaned_data.get('assigned_asset')

        filled = [t for t in [holder, location, asset] if t is not None]
        if len(filled) == 0:
            raise ValidationError(_("You must select either an Asset Holder, a Location, or an Asset."))
        if len(filled) > 1:
            raise ValidationError(_("Please select exactly one target (either Asset Holder, Location, OR Asset)."))
        return cleaned_data
