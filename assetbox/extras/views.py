from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from .models import Tag, CustomField, CustomFieldset
from .forms import TagForm, TagFilterForm, CustomFieldForm, CustomFieldFilterForm, CustomFieldsetForm, CustomFieldsetFilterForm
from django_tables2 import RequestConfig
from .tables import TagTable, CustomFieldTable, CustomFieldsetTable
from .filters import TagFilter, CustomFieldFilterSet, CustomFieldsetFilterSet
from core.utils import get_paginate_count, get_model_viewname # Import the utility function
from assets.tables import AssetTable # Import AssetTable
from users.models import UserPreference # Import UserPreference
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib import messages
from core.views import ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView
from core.panels import Panel

class TagDetailView(ObjectDetailView):
    queryset = Tag.objects.all()
    template_name = 'extras/tags/tag_detail.html'

    layout = (
        ((Panel('info', 'Tag Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tag = self.object

        # Fetch related assets using the related_name from Asset.tags
        related_assets = tag.assets.all().prefetch_related(
            'asset_role', 'asset_type__manufacturer', 'location', 'location__site'
        )

        # Create and configure the assets table
        assets_table = AssetTable(related_assets, request=self.request)
        # Disable pagination for related table
        RequestConfig(self.request, paginate=False).configure(assets_table)

        context['assets_table'] = assets_table
        return context

class TagCreateView(ObjectEditView):
    model_form = TagForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'extras:tag_list'

class TagUpdateView(ObjectEditView):
    queryset = Tag.objects.all()
    model_form = TagForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'extras:tag_list'

class TagDeleteView(ObjectDeleteView):
    queryset = Tag.objects.all()
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('extras:tag_list')

# Refactor tag_list to CBV
class TagListView(ObjectListView):
    queryset = Tag.objects.all()
    filterset = TagFilter
    filterset_form = TagFilterForm # Assuming TagFilterForm exists
    table = TagTable
    action_buttons = ('add',) # Add create button
    template_name = 'generic/object_list.html' # Use base template


# Custom Fields
class CustomFieldListView(ObjectListView):
    queryset = CustomField.objects.all()
    filterset = CustomFieldFilterSet
    filterset_form = CustomFieldFilterForm
    table = CustomFieldTable
    action_buttons = ('add',)


class CustomFieldDetailView(ObjectDetailView):
    queryset = CustomField.objects.all()

    layout = (
        ((Panel('info', 'Custom Field Details'),),),
    )


class CustomFieldEditView(ObjectEditView):
    queryset = CustomField.objects.all()
    model = CustomField
    model_form = CustomFieldForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:customfield_list'


class CustomFieldDeleteView(ObjectDeleteView):
    queryset = CustomField.objects.all()
    model = CustomField
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:customfield_list')


# Custom Fieldsets
class CustomFieldsetListView(ObjectListView):
    queryset = CustomFieldset.objects.annotate(fields_count=Count('fields'))
    filterset = CustomFieldsetFilterSet
    filterset_form = CustomFieldsetFilterForm
    table = CustomFieldsetTable
    action_buttons = ('add',)


class CustomFieldsetDetailView(ObjectDetailView):
    queryset = CustomFieldset.objects.all().prefetch_related('fields', 'asset_types')

    layout = (
        ((Panel('info', 'Custom Field Set Details'),),),
    )


class CustomFieldsetEditView(ObjectEditView):
    queryset = CustomFieldset.objects.all()
    model = CustomFieldset
    model_form = CustomFieldsetForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:customfieldset_list'


class CustomFieldsetDeleteView(ObjectDeleteView):
    queryset = CustomFieldset.objects.all()
    model = CustomFieldset
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:customfieldset_list')

