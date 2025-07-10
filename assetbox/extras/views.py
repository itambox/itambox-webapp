from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from .models import Tag, ConfigTemplate
from .forms import TagForm, ConfigTemplateForm, TagFilterForm # Added TagFilterForm
from django_tables2 import RequestConfig
from .tables import TagTable, ConfigTemplateTable
from .filters import TagFilter, ConfigTemplateFilter # <-- Import FilterSet
from core.utils import get_paginate_count, get_model_viewname # Import the utility function
from assets.tables import AssetTable # Import AssetTable
from users.models import UserPreference # Import UserPreference
from django.urls import reverse, reverse_lazy
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib import messages
from core.views import ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView
from . import forms
from . import filters
from . import tables

# Create your views here.

# --- Tag Views ---

# Remove the function-based tag_list view below
# @login_required
# def tag_list(request):
#     ...
#     return render(request, 'generic/object_list.html', context)
# End removal of tag_list

class TagDetailView(ObjectDetailView):
    queryset = Tag.objects.all()
    template_name = 'extras/tags/tag_detail.html'

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

# --- ConfigTemplate Views ---

# Refactor ConfigTemplateListView to use ObjectListView
class ConfigTemplateListView(ObjectListView): # Inherit from ObjectListView
    queryset = ConfigTemplate.objects.annotate(asset_roles_count=Count('asset_roles'))
    filterset = filters.ConfigTemplateFilter # Set the filterset
    filterset_form = forms.ConfigTemplateFilterForm # Set the filter form
    table = tables.ConfigTemplateTable # Set the table
    action_buttons = ('add',) # Add standard action buttons
    # template_name removed, ObjectListView handles it
    # context_object_name removed, ObjectListView handles it
    # get_context_data removed, ObjectListView handles table instantiation

# Keep Detail, Create, Update, Delete views as standard CBVs for now

class ConfigTemplateDetailView(LoginRequiredMixin, DetailView):
    model = ConfigTemplate
    template_name = 'extras/configtemplates/configtemplate_detail.html'
    context_object_name = 'config_template'

class ConfigTemplateCreateView(LoginRequiredMixin, CreateView):
    model = ConfigTemplate
    form_class = ConfigTemplateForm
    template_name = 'extras/configtemplates/configtemplate_form.html'
    success_url = reverse_lazy('extras:configtemplate_list')
    
    def form_valid(self, form):
        messages.success(self.request, "Config template created successfully.")
        return super().form_valid(form)

class ConfigTemplateUpdateView(LoginRequiredMixin, UpdateView):
    model = ConfigTemplate
    form_class = ConfigTemplateForm
    template_name = 'extras/configtemplates/configtemplate_form.html'
    success_url = reverse_lazy('extras:configtemplate_list')
    
    def form_valid(self, form):
        messages.success(self.request, "Config template updated successfully.")
        return super().form_valid(form)

class ConfigTemplateDeleteView(LoginRequiredMixin, DeleteView):
    model = ConfigTemplate
    template_name = 'extras/configtemplates/configtemplate_confirm_delete.html'
    success_url = reverse_lazy('extras:configtemplate_list')
    
    def delete(self, request, *args, **kwargs):
        messages.success(request, "Config template deleted successfully.")
        return super().delete(request, *args, **kwargs)

# Refactor tag_list to CBV
class TagListView(ObjectListView):
    queryset = Tag.objects.all()
    filterset = TagFilter
    filterset_form = TagFilterForm # Assuming TagFilterForm exists
    table = TagTable
    action_buttons = ('add',) # Add create button
    template_name = 'generic/object_list.html' # Use base template
