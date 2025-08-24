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

        instances_qs = InstalledSoftware.objects.filter(software=software).select_related('asset', 'asset__asset_type', 'asset__asset_type__manufacturer')
        instances_table = InstalledSoftwareTable(instances_qs)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(instances_table)
        context['instances_table'] = instances_table
        
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
