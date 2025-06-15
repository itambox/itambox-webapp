from django.shortcuts import render
from django.urls import reverse
from core.views import (
    ObjectListView,
    ObjectDetailView,
    ObjectEditView,
    ObjectDeleteView,
    # BulkImportView, # Add later if needed
    # BulkEditView,  # Add later if needed
    # BulkDeleteView # Add later if needed
)
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
    template_name = 'generic/object_list_base.html' # Use a specific template or generic one
    action_buttons = ('add', 'import', 'export') # Standard list view actions

class SoftwareDetailView(ObjectDetailView):
    queryset = Software.objects.select_related('manufacturer').prefetch_related('tags')
    template_name = 'software/software_detail.html' # Use a specific template or generic one

class SoftwareEditView(ObjectEditView):
    queryset = Software.objects.all()
    model_form = forms.SoftwareForm
    template_name = 'generic/object_edit.html' 
    default_return_url = 'software:software_list' 

class SoftwareDeleteView(ObjectDeleteView):
    queryset = Software.objects.all()
    # Set default return URL
    default_return_url = 'software:software_list' 
