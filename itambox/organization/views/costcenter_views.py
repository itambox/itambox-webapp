from django.db.models import Count, Q
from django.urls import reverse_lazy

from itambox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectBulkEditView, ObjectBulkDeleteView, ObjectCloneView,
)
from itambox.panels import Panel

from ..models import CostCenter
from ..forms import CostCenterForm, CostCenterFilterForm
from ..tables import CostCenterTable
from ..filters import CostCenterFilterSet


class CostCenterListView(ObjectListView):
    queryset = CostCenter.objects.select_related('tenant', 'parent').annotate(
        child_count=Count('children', filter=Q(children__deleted_at__isnull=True)),
    )
    filterset = CostCenterFilterSet
    filterset_form = CostCenterFilterForm
    table = CostCenterTable
    action_buttons = ('add',)


class CostCenterDetailView(ObjectDetailView):
    queryset = CostCenter.objects.select_related('tenant', 'parent').prefetch_related('children')

    layout = (
        ((Panel('info', 'Cost Center Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        cost_center = self.get_object()

        children = cost_center.children.all()
        if children.exists():
            context['children_table'] = CostCenterTable(children, request=self.request)

        related_objects_list = []
        child_count = children.count()
        if child_count:
            related_objects_list.append({
                'label': 'Sub-units',
                'count': child_count,
                'url': f"{cost_center.get_absolute_url()}",
            })
        context['related_objects_list'] = related_objects_list
        return context


class CostCenterEditView(ObjectEditView):
    queryset = CostCenter.objects.all()
    model = CostCenter
    model_form = CostCenterForm
    template_name = 'generic/object_edit.html'


class CostCenterCloneView(ObjectCloneView):
    model = CostCenter
    model_form = CostCenterForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'organization:costcenter_list'


class CostCenterDeleteView(ObjectDeleteView):
    queryset = CostCenter.objects.all()
    model = CostCenter
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:costcenter_list')


class CostCenterBulkEditView(ObjectBulkEditView):
    queryset = CostCenter.objects.all()


class CostCenterBulkDeleteView(ObjectBulkDeleteView):
    queryset = CostCenter.objects.all()
