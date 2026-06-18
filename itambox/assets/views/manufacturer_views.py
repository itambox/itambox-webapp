from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.shortcuts import redirect
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from django_tables2 import RequestConfig

from ..models import Manufacturer, Asset
from .. import forms, tables, filters

from itambox.utils import get_paginate_count
from itambox.panels import Panel
from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView,
    ObjectDeleteView, ObjectImportView, ObjectCloneView,
)


class ManufacturerListView(ObjectListView):
    queryset = Manufacturer.objects.prefetch_related('tags').annotate(
        asset_count=Count('asset_types__assets'),
        asset_type_count=Count('asset_types'),
    )
    filterset = filters.ManufacturerFilterSet
    filterset_form = forms.ManufacturerFilterForm
    table = tables.ManufacturerTable
    action_buttons = ('add',)


class ManufacturerDetailView(ObjectDetailView):
    queryset = Manufacturer.objects.prefetch_related(
        'asset_types', 'asset_types__assets'
    )

    layout = (
        ((Panel('info', 'Manufacturer Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        manufacturer = self.get_object()

        asset_types_table = tables.AssetTypeTable(manufacturer.asset_types.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(asset_types_table)

        manufacturer_assets = Asset.objects.filter(asset_type__manufacturer=manufacturer).select_related(
            'asset_role', 'asset_type', 'location'
        )
        assets_table = tables.AssetTable(manufacturer_assets, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assets_table)

        related_objects_list = []
        asset_count = manufacturer_assets.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?manufacturer={manufacturer.slug}"
            })
        assettype_count = manufacturer.asset_types.count()
        if assettype_count:
            related_objects_list.append({
                'label': 'Asset Types',
                'count': assettype_count,
                'url': f"{reverse('assets:assettype_list')}?manufacturer={manufacturer.slug}"
            })

        context['asset_types_table'] = asset_types_table
        context['assets_table'] = assets_table
        context['related_objects_list'] = related_objects_list

        # Components
        from inventory.models import Component
        from inventory.tables import ComponentTable
        comp_qs = Component.objects.filter(manufacturer=manufacturer).select_related('category', 'tenant')
        components_table = ComponentTable(comp_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(components_table)
        context['components_table'] = components_table

        # Accessories
        from inventory.models import Accessory
        from inventory.tables import AccessoryTable
        acc_qs = Accessory.objects.filter(manufacturer=manufacturer).select_related('category', 'tenant')
        accessories_table = AccessoryTable(acc_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(accessories_table)
        context['accessories_table'] = accessories_table

        # Consumables
        from inventory.models import Consumable
        from inventory.tables import ConsumableTable
        con_qs = Consumable.objects.filter(manufacturer=manufacturer).select_related('category', 'tenant')
        consumables_table = ConsumableTable(con_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(consumables_table)
        context['consumables_table'] = consumables_table

        # Software products
        from software.models import Software
        from software.tables import SoftwareTable
        sw_qs = Software.objects.filter(manufacturer=manufacturer).select_related('manufacturer').prefetch_related('tags')
        software_table = SoftwareTable(sw_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(software_table)
        context['software_table'] = software_table

        return context


class ManufacturerEditView(ObjectEditView):
    queryset = Manufacturer.objects.all()
    model = Manufacturer
    model_form = forms.ManufacturerForm
    template_name = 'generic/object_edit.html'


class ManufacturerCloneView(ObjectCloneView):
    model = Manufacturer
    model_form = forms.ManufacturerForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:manufacturer_list'


class ManufacturerDeleteView(ObjectDeleteView):
    queryset = Manufacturer.objects.all()
    model = Manufacturer
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:manufacturer_list')

    def post(self, request, *args, **kwargs):
        manufacturer = self.get_object()
        asset_type_count = manufacturer.asset_types.count()

        if asset_type_count > 0:
            messages.error(
                request,
                _("Cannot delete manufacturer '%(name)s': It is associated with %(count)s asset type%(suffix)s.") % {
                    "name": manufacturer.name,
                    "count": asset_type_count,
                    "suffix": 's' if asset_type_count != 1 else '',
                }
            )
            return redirect(manufacturer.get_absolute_url())

        return super().post(request, *args, **kwargs)
