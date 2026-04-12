from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseBadRequest
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import View
from django.urls import reverse, reverse_lazy
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.db.models import Count, Q

from assetbox.views.generic import (
    ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView,
    ObjectImportView, ObjectBulkEditView, ObjectBulkDeleteView,
)
from assetbox.utils import get_paginate_count
from assetbox.panels import Panel

from ..models import AssetHolder, ContactAssignment
from ..forms import AssetHolderForm, AssetHolderFilterForm, ContactAssignmentForm
from ..tables import (
    AssetHolderTable, AssetAssignmentTable,
)
from ..filters import AssetHolderFilterSet
from assets.forms.import_forms import AssetHolderBulkImportForm
from django_tables2 import RequestConfig


class AssetHolderListView(ObjectListView):
    queryset = AssetHolder.objects.select_related('tenant').prefetch_related('tags').annotate(
        assignment_count=Count('asset_assignments', filter=Q(asset_assignments__is_active=True)),
    )
    filterset = AssetHolderFilterSet
    filterset_form = AssetHolderFilterForm
    table = AssetHolderTable
    action_buttons = ('add',)


class AssetHolderDetailView(ObjectDetailView):
    queryset = AssetHolder.objects.select_related('tenant', 'user').prefetch_related(
        'asset_assignments__asset', 'asset_assignments__asset__status', 'tags'
    )

    layout = (
        ((Panel('info', 'Asset Holder Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        assetholder = self.get_object()

        assignments_table = AssetAssignmentTable(assetholder.checked_out_assets, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assignments_table)

        context['assignments_table'] = assignments_table
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
        assignment_count = assetholder.asset_assignments.filter(is_active=True).count()

        if assignment_count > 0:
            messages.error(
                request,
                f"Cannot delete asset holder '{assetholder}': It has {assignment_count} active assignment{'s' if assignment_count != 1 else ''}."
            )
            return redirect(assetholder.get_absolute_url())

        return super().post(request, *args, **kwargs)


class AssetHolderImportView(ObjectImportView):
    model_form = AssetHolderBulkImportForm


class AssetHolderBulkEditView(ObjectBulkEditView):
    queryset = AssetHolder.objects.all()


class AssetHolderBulkDeleteView(ObjectBulkDeleteView):
    queryset = AssetHolder.objects.all()
