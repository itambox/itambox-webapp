from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from itambox.views.generic import (
    ObjectListView,
    ObjectDetailView,
    ObjectEditView,
    ObjectDeleteView,
    ObjectCloneView,
)
from itambox.panels import Panel
from .models import Software, InstalledSoftware
from .tables import InstalledSoftwareTable
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
        ((Panel('info', _('Software Details')),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        software = self.get_object()

        from django_tables2 import RequestConfig
        from itambox.utils import get_paginate_count

        instances_qs = InstalledSoftware.objects.filter(software=software)
        instances_table = InstalledSoftwareTable(instances_qs)
        instances_table.configure(self.request)
        context['instances_table'] = instances_table

        # Software Licenses & Entitlements
        from licenses.models import License
        from licenses.tables import LicenseTable
        license_qs = License.objects.filter(software=software)
        licenses_table = LicenseTable(license_qs, request=self.request)
        licenses_table.configure(self.request)
        context['licenses_table'] = licenses_table

        related_objects_list = []
        instance_count = instances_qs.count()
        if instance_count:
            related_objects_list.append({
                'label': _('Installed Instances'),
                'count': instance_count,
                'url': f"{reverse('software:software_detail', kwargs={'pk': software.pk})}#instances"
            })
        license_count = license_qs.count()
        if license_count:
            related_objects_list.append({
                'label': _('Licenses'),
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


class SoftwareCloneView(SoftwareEditView, ObjectCloneView):
    model = Software
