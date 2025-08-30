from django.urls import reverse_lazy
from django.shortcuts import redirect
from django.contrib import messages
from django_tables2 import RequestConfig
from assetbox.utils import get_paginate_count
from assetbox.panels import Panel
from assetbox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, 
    ObjectDeleteView, ObjectCloneView, ObjectBulkEditView, ObjectBulkDeleteView
)
from .models import ComponentType, ComponentInstance
from .forms import ComponentTypeForm, ComponentInstanceForm, ComponentTypeFilterForm, ComponentInstanceFilterForm
from .filters import ComponentTypeFilterSet, ComponentInstanceFilterSet
from .tables import ComponentTypeTable, ComponentInstanceTable

class ComponentTypeCloneView(ObjectCloneView):
    model = ComponentType
    model_form = ComponentTypeForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:componenttype_list'


class ComponentTypeListView(ObjectListView):
    queryset = ComponentType.objects.select_related('manufacturer').prefetch_related('tags')
    filterset = ComponentTypeFilterSet
    filterset_form = ComponentTypeFilterForm
    table = ComponentTypeTable
    action_buttons = ('add',)


class ComponentTypeDetailView(ObjectDetailView):
    queryset = ComponentType.objects.select_related('manufacturer').prefetch_related('tags', 'instances')
    template_name = 'assets/componenttypes/componenttype_detail.html'

    layout = (
        ((Panel('info', 'Component Type Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        componenttype = self.get_object()

        # Prepare instances table
        instances_table = ComponentInstanceTable(componenttype.instances.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(instances_table)
        context['instances_table'] = instances_table

        return context


class ComponentTypeEditView(ObjectEditView):
    queryset = ComponentType.objects.all()
    model = ComponentType
    model_form = ComponentTypeForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:componenttype_list'


class ComponentTypeDeleteView(ObjectDeleteView):
    queryset = ComponentType.objects.all()
    model = ComponentType
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:componenttype_list')

    def post(self, request, *args, **kwargs):
        comp_type = self.get_object()
        instance_count = comp_type.instances.count()
        if instance_count > 0:
            messages.error(
                request,
                f"Cannot delete component type '{comp_type}': It has {instance_count} active physical parts."
            )
            return redirect(comp_type.get_absolute_url())
        return super().post(request, *args, **kwargs)


class ComponentInstanceListView(ObjectListView):
    queryset = ComponentInstance.objects.select_related('component_type', 'component_type__manufacturer', 'parent_asset').prefetch_related('tags')
    filterset = ComponentInstanceFilterSet
    filterset_form = ComponentInstanceFilterForm
    table = ComponentInstanceTable
    action_buttons = ('add',)


class ComponentInstanceDetailView(ObjectDetailView):
    queryset = ComponentInstance.objects.select_related('component_type', 'component_type__manufacturer', 'parent_asset').prefetch_related('tags')
    template_name = 'assets/componentinstances/componentinstance_detail.html'

    layout = (
        ((Panel('info', 'Component Details'),),),
    )


class ComponentInstanceEditView(ObjectEditView):
    queryset = ComponentInstance.objects.all()
    model = ComponentInstance
    model_form = ComponentInstanceForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:componentinstance_list'


class ComponentInstanceDeleteView(ObjectDeleteView):
    queryset = ComponentInstance.objects.all()
    model = ComponentInstance
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:componentinstance_list')


class ComponentInstanceBulkEditView(ObjectBulkEditView):
    queryset = ComponentInstance.objects.all()


class ComponentInstanceBulkDeleteView(ObjectBulkDeleteView):
    queryset = ComponentInstance.objects.all()
