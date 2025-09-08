from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseBadRequest
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import View
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count

from assetbox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectImportView, ObjectBulkEditView, ObjectBulkDeleteView,
)
from assetbox.utils import get_paginate_count
from assetbox.panels import Panel

from ..models import AssetHolder, AssetHolderAssignment, ContactAssignment
from ..forms import AssetHolderForm, AssetHolderFilterForm, ContactAssignmentForm, AssetHolderAssignmentFilterForm
from ..tables import (
    AssetHolderTable, AssetHolderAssignmentTable,
)
from ..filters import AssetHolderFilterSet, AssetHolderAssignmentFilterSet
from assets.forms.import_forms import AssetHolderBulkImportForm
from django_tables2 import RequestConfig


class AssetHolderListView(ObjectListView):
    queryset = AssetHolder.objects.select_related('tenant').prefetch_related('tags').annotate(
        assignment_count=Count('assignments'),
    )
    filterset = AssetHolderFilterSet
    filterset_form = AssetHolderFilterForm
    table = AssetHolderTable
    action_buttons = ('add',)


class AssetHolderDetailView(ObjectDetailView):
    queryset = AssetHolder.objects.select_related('tenant', 'user').prefetch_related(
        'assignments__assigned_object', 'assignments__content_type', 'tags'
    )

    layout = (
        ((Panel('info', 'Asset Holder Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        assetholder = self.get_object()

        assignments_table = AssetHolderAssignmentTable(assetholder.assignments.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assignments_table)

        related_objects_list = []
        assignment_count = assetholder.assignments.count()
        if assignment_count:
            related_objects_list.append({
                'label': 'Assignments',
                'count': assignment_count,
                'url': f"{reverse('organization:assetholderassignment_list')}?asset_holder={assetholder.pk}"
            })

        context['assignments_table'] = assignments_table
        context['related_objects_list'] = related_objects_list
        return context


class AssetHolderEditView(ObjectEditView):
    queryset = AssetHolder.objects.all()
    model = AssetHolder
    model_form = AssetHolderForm
    template_name = 'generic/object_edit.html'


class AssetHolderDeleteView(ObjectDeleteView):
    queryset = AssetHolder.objects.all()
    model = AssetHolder
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:assetholder_list')

    def post(self, request, *args, **kwargs):
        assetholder = self.get_object()
        assignment_count = assetholder.assignments.count()

        if assignment_count > 0:
            messages.error(
                request,
                f"Cannot delete asset holder '{assetholder}': It has {assignment_count} assignment{'s' if assignment_count != 1 else ''}."
            )
            return redirect(assetholder.get_absolute_url())

        return super().post(request, *args, **kwargs)


class AssetHolderAssignmentListView(ObjectListView):
    queryset = AssetHolderAssignment.objects.select_related('asset_holder', 'content_type')
    table = AssetHolderAssignmentTable
    action_buttons = ()
    filterset = AssetHolderAssignmentFilterSet
    filterset_form = AssetHolderAssignmentFilterForm


    def get_breadcrumbs(self):
        return [
            (reverse('dashboard'), 'Dashboard'),
            (reverse('organization:assetholder_list'), 'Asset Holders'),
            (None, 'Assignments')
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Asset Holder Assignments'
        return context


class AssetHolderImportView(ObjectImportView):
    model_form = AssetHolderBulkImportForm


class AssetHolderBulkEditView(ObjectBulkEditView):
    queryset = AssetHolder.objects.all()


class AssetHolderBulkDeleteView(ObjectBulkDeleteView):
    queryset = AssetHolder.objects.all()
