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
from assetbox.views.generic.service_views import GenericTransactionView, SimplePostView
from .services import checkout_license, checkin_license_seat

# =============================================================================
# License Entitlement Views
# =============================================================================

class LicenseListView(ObjectListView):
    queryset = License.objects.with_counts().select_related('software', 'software__manufacturer', 'tenant', 'supplier').prefetch_related('tags')
    filterset = filters.LicenseFilterSet
    filterset_form = forms.LicenseFilterForm
    table = tables.LicenseTable
    template_name = 'generic/object_list.html'
    action_buttons = ('add', 'import', 'export')

class LicenseDetailView(ObjectDetailView):
    queryset = License.objects.select_related('software', 'software__manufacturer', 'tenant').prefetch_related('tags')
    template_name = 'licenses/license_detail.html'

    layout = (
        ((Panel('metrics', 'License Overview'),),),
        ((Panel('info', 'License Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        license_obj = self.get_object()

        # Query all seat assignments (to either assets or holders)
        assignments_qs = license_obj.assignments.all()
        assignments_table = tables.LicenseSeatAssignmentTable(assignments_qs, request=self.request)
        assignments_table.configure(self.request)
        context['assignments_table'] = assignments_table

        # Kits
        from inventory.models import Kit
        from inventory.tables import KitTable
        kits_qs = Kit.objects.filter(items__license=license_obj).distinct()
        kits_table = KitTable(kits_qs, request=self.request)
        kits_table.configure(self.request)
        context['kits_table'] = kits_table

        related_objects_list = []
        assignment_count = assignments_qs.count()
        if assignment_count:
            related_objects_list.append({
                'label': 'Seat Assignments',
                'count': assignment_count,
                'url': f"{reverse('licenses:license_detail', kwargs={'pk': license_obj.pk})}#assignments"
            })
        kit_count = kits_qs.count()
        if kit_count:
            related_objects_list.append({
                'label': 'Kits',
                'count': kit_count,
                'url': f"{reverse('inventory:kit_list')}?license={license_obj.pk}"
            })
        context['related_objects_list'] = related_objects_list

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


class LicenseCheckoutView(GenericTransactionView):
    permission_required = ('licenses.change_license',)
    queryset = License.objects.all()
    model_form = forms.LicenseCheckOutForm
    service_callable = checkout_license
    context_object_name = 'license'
    template_name = 'licenses/includes/license_checkout_modal.html'
    success_message = "License checked out successfully."
    hx_trigger = "licenseUpdated"
    form_field_map = {}
    form_exclude_fields = ('target_type',)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        del kwargs['instance']
        kwargs['license'] = self.get_object()
        return kwargs

    def get_success_message(self, result=None):
        target = result.asset or result.assigned_holder
        return f"License seat for '{self.get_object().name}' checked out to {target}."


class LicenseCheckinView(SimplePostView):
    permission_required = ('licenses.delete_licenseseatassignment',)
    queryset = LicenseSeatAssignment.objects.select_related('license', 'asset', 'assigned_holder').all()
    hx_trigger = "licenseUpdated"

    def perform_action(self, assignment, request):
        return checkin_license_seat(assignment, user=request.user)
