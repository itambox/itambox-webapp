from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.db.models import Count

from core.views import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectImportView, ObjectBulkEditView, ObjectBulkDeleteView,
)
from core.quick_add import QuickAddMixin
from core.utils import get_paginate_count
from core.panels import Panel
from assets.tables import AssetTable

from ..models import Location
from ..forms import LocationForm, LocationFilterForm
from ..tables import LocationTable
from ..filters import LocationFilterSet
from assets.forms.import_forms import LocationBulkImportForm
from django_tables2 import RequestConfig


class LocationListView(ObjectListView):
    queryset = Location.objects.select_related('site', 'site__region', 'tenant').prefetch_related('tags').annotate(
        asset_count=Count('assets'),
    )
    filterset = LocationFilterSet
    filterset_form = LocationFilterForm
    table = LocationTable
    action_buttons = ('add',)


class LocationDetailView(ObjectDetailView):
    queryset = Location.objects.select_related(
        'site', 'parent', 'tenant'
    ).prefetch_related(
        'children', 'tags', 'assets'
    )

    layout = (
        ((Panel('info', 'Location Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        location = self.get_object()

        assets_table = AssetTable(location.assets.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assets_table)

        related_objects_list = []
        asset_count = location.assets.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?location={location.slug}"
            })
        child_count = location.children.count()
        if child_count:
            related_objects_list.append({
                'label': 'Child Locations',
                'count': child_count,
                'url': f"{reverse('organization:location_list')}?parent={location.slug}"
            })

        context['assets_table'] = assets_table
        context['related_objects_list'] = related_objects_list
        return context


class LocationEditView(QuickAddMixin, ObjectEditView):
    queryset = Location.objects.all()
    model = Location
    model_form = LocationForm
    template_name = 'generic/object_edit.html'
    quick_add_target = 'id_location'


class LocationDeleteView(ObjectDeleteView):
    queryset = Location.objects.all()
    model = Location
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:location_list')

    def post(self, request, *args, **kwargs):
        location = self.get_object()
        asset_count = location.assets.count()

        if asset_count > 0:
            messages.error(
                request,
                f"Cannot delete location '{location.name}': It is associated with {asset_count} asset{'s' if asset_count != 1 else ''}."
            )
            return redirect(location.get_absolute_url())

        return super().post(request, *args, **kwargs)


class LocationImportView(ObjectImportView):
    model_form = LocationBulkImportForm


class LocationBulkEditView(ObjectBulkEditView):
    queryset = Location.objects.all()


class LocationBulkDeleteView(ObjectBulkDeleteView):
    queryset = Location.objects.all()
