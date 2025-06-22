from django.shortcuts import render
from django.urls import reverse
from django_tables2 import RequestConfig
from core.views import (
    ObjectListView,
    ObjectDetailView,
    ObjectEditView,
    ObjectDeleteView
)
from core.utils import get_paginate_count
from .models import License, LicenseSeatAssignment
from . import forms
from . import tables
from . import filters

# =============================================================================
# License Entitlement Views
# =============================================================================

class LicenseListView(ObjectListView):
    queryset = License.objects.select_related('software', 'software__manufacturer', 'tenant').prefetch_related('tags')
    filterset = filters.LicenseFilterSet
    filterset_form = forms.LicenseFilterForm
    table = tables.LicenseTable
    template_name = 'generic/object_list.html'
    action_buttons = ('add', 'import', 'export')

class LicenseDetailView(ObjectDetailView):
    queryset = License.objects.select_related('software', 'software__manufacturer', 'tenant').prefetch_related('tags')
    template_name = 'licenses/license_detail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        license_obj = self.get_object()

        # Query all seat assignments (to either assets or holders)
        assignments_qs = license_obj.assignments.select_related('asset', 'assigned_holder')
        assignments_table = tables.LicenseSeatAssignmentTable(assignments_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assignments_table)
        context['assignments_table'] = assignments_table

        return context

class LicenseEditView(ObjectEditView):
    queryset = License.objects.all()
    model_form = forms.LicenseForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'licenses:license_list'

class LicenseDeleteView(ObjectDeleteView):
    queryset = License.objects.all()
    default_return_url = 'licenses:license_list'
