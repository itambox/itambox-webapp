from django.shortcuts import redirect
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.db.models import Count

from core.views import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectBulkEditView, ObjectBulkDeleteView,
)
from core.utils import get_paginate_count
from core.panels import Panel

from ..models import TenantGroup
from ..forms import TenantGroupForm, TenantGroupFilterForm
from ..tables import TenantGroupTable, TenantTable
from ..filters import TenantGroupFilterSet
from django_tables2 import RequestConfig


class TenantGroupListView(ObjectListView):
    queryset = TenantGroup.objects.annotate(
        tenant_count=Count('tenants')
    ).prefetch_related('tags')
    filterset = TenantGroupFilterSet
    filterset_form = TenantGroupFilterForm
    table = TenantGroupTable
    action_buttons = ('add',)


class TenantGroupDetailView(ObjectDetailView):
    queryset = TenantGroup.objects.prefetch_related(
        'children', 'tags', 'tenants'
    )

    layout = (
        ((Panel('info', 'Tenant Group Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenantgroup = self.get_object()

        tenants_table = TenantTable(tenantgroup.tenants.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(tenants_table)

        related_objects_list = []
        tenant_count = tenantgroup.tenants.count()
        if tenant_count:
            related_objects_list.append({
                'label': 'Tenants',
                'count': tenant_count,
                'url': f"{reverse('organization:tenant_list')}?group={tenantgroup.slug}"
            })
        child_count = tenantgroup.children.count()
        if child_count:
            related_objects_list.append({
                'label': 'Child Groups',
                'count': child_count,
                'url': f"{reverse('organization:tenantgroup_list')}?parent={tenantgroup.slug}"
            })

        context['tenants_table'] = tenants_table
        context['related_objects_list'] = related_objects_list

        children = tenantgroup.children.all()
        if children.exists():
            context['children_table'] = TenantGroupTable(children, request=self.request)

        return context


class TenantGroupEditView(ObjectEditView):
    queryset = TenantGroup.objects.all()
    model = TenantGroup
    model_form = TenantGroupForm
    template_name = 'generic/object_edit.html'


class TenantGroupDeleteView(ObjectDeleteView):
    queryset = TenantGroup.objects.all()
    model = TenantGroup
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:tenantgroup_list')

    def post(self, request, *args, **kwargs):
        tenantgroup = self.get_object()
        tenant_count = tenantgroup.tenants.count()

        if tenant_count > 0:
            messages.error(
                request,
                f"Cannot delete tenant group '{tenantgroup.name}': It is associated with {tenant_count} tenant{'s' if tenant_count != 1 else ''}."
            )
            return redirect(tenantgroup.get_absolute_url())

        return super().post(request, *args, **kwargs)
