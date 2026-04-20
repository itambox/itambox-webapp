from django.shortcuts import render
from django.urls import reverse
from assetbox.views.generic import (
    ObjectListView,
    ObjectDetailView,
    ObjectEditView,
    ObjectDeleteView,
    # BulkImportView, # Add later if needed
    # BulkEditView,  # Add later if needed
    # BulkDeleteView # Add later if needed
)
from assetbox.panels import Panel
from .models import Software
from . import forms
from . import tables
from . import filters

# =============================================================================
# Software Views
# =============================================================================

class SoftwareListView(ObjectListView):
    queryset = Software.objects.select_related('manufacturer').prefetch_related('tags')
    filterset = filters.SoftwareFilterSet
    filterset_form = forms.SoftwareFilterForm
    table = tables.SoftwareTable
    template_name = 'generic/object_list.html' # Use a specific template or generic one
    action_buttons = ('add', 'import', 'export') # Standard list view actions

class SoftwareDetailView(ObjectDetailView):
    queryset = Software.objects.select_related('manufacturer').prefetch_related('tags')
    template_name = 'software/software_detail.html' # Use a specific template or generic one

    layout = (
        ((Panel('info', 'Software Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        software = self.get_object()

        from assets.models import InstalledSoftware
        from .tables import InstalledSoftwareTable
        from django_tables2 import RequestConfig
        from assetbox.utils import get_paginate_count

        instances_qs = InstalledSoftware.objects.filter(software=software).select_related('asset', 'asset__asset_type', 'asset__asset_type__manufacturer')
        instances_table = InstalledSoftwareTable(instances_qs)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(instances_table)
        context['instances_table'] = instances_table

        # Software Licenses & Entitlements
        from licenses.models import License
        from licenses.tables import LicenseTable
        license_qs = License.objects.filter(software=software).select_related('software', 'tenant').prefetch_related('tags')
        licenses_table = LicenseTable(license_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(licenses_table)
        context['licenses_table'] = licenses_table

        related_objects_list = []
        instance_count = instances_qs.count()
        if instance_count:
            related_objects_list.append({
                'label': 'Installed Instances',
                'count': instance_count,
                'url': f"{reverse('software:software_detail', kwargs={'pk': software.pk})}#instances"
            })
        license_count = license_qs.count()
        if license_count:
            related_objects_list.append({
                'label': 'Licenses',
                'count': license_count,
                'url': f"{reverse('licenses:license_list')}?software={software.pk}"
            })
        context['related_objects_list'] = related_objects_list

        return context


class SoftwareEditView(ObjectEditView):
    queryset = Software.objects.all()
    model_form = forms.SoftwareForm
    template_name = 'generic/object_edit.html' 
    default_return_url = 'software:software_list' 

class SoftwareDeleteView(ObjectDeleteView):
    queryset = Software.objects.all()
    # Set default return URL
    default_return_url = 'software:software_list' 
