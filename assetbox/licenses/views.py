from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from django.contrib.auth.mixins import LoginRequiredMixin
from django_tables2 import RequestConfig
from assetbox.views.generic import (
    ObjectListView,
    ObjectDetailView,
    ObjectEditView,
    ObjectDeleteView,
    ObjectImportView,
    ObjectBulkEditView,
    ObjectBulkDeleteView,
)
from assetbox.utils import get_paginate_count
from assetbox.panels import Panel
from .models import License, LicenseSeatAssignment
from . import forms
from . import tables
from . import filters
from assets.forms.import_forms import LicenseBulkImportForm

# =============================================================================
# License Entitlement Views
# =============================================================================

class LicenseListView(ObjectListView):
    queryset = License.objects.select_related('software', 'software__manufacturer', 'tenant', 'supplier').prefetch_related('tags')
    filterset = filters.LicenseFilterSet
    filterset_form = forms.LicenseFilterForm
    table = tables.LicenseTable
    template_name = 'generic/object_list.html'
    action_buttons = ('add', 'import', 'export')

class LicenseDetailView(ObjectDetailView):
    queryset = License.objects.select_related('software', 'software__manufacturer', 'tenant').prefetch_related('tags')
    template_name = 'licenses/license_detail.html'

    layout = (
        ((Panel('info', 'License Details'),),),
    )

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


class LicenseCloneView(ObjectEditView):
    model = License
    model_form = forms.LicenseForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'licenses:license_list'

    def get_object(self, queryset=None):
        original = get_object_or_404(License, pk=self.kwargs['pk'])
        cloned = original.clone()
        cloned.name = f'{original.name} (Copy)'
        cloned.product_key = ''
        cloned.save()
        cloned.tags.set(original.tags.all())
        return cloned


class LicenseImportView(ObjectImportView):
    model_form = LicenseBulkImportForm


class LicenseBulkEditView(ObjectBulkEditView):
    queryset = License.objects.all()


class LicenseBulkDeleteView(ObjectBulkDeleteView):
    queryset = License.objects.all()
