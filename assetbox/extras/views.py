from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
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
#     return render(request, 'generic/object_list_base.html', context)
# End removal of tag_list

@login_required
def tag_detail(request, pk):
    tag = get_object_or_404(Tag, pk=pk)
    model = Tag # Get the model class

    # Fetch related assets using the related_name from Asset.tags
    related_assets = tag.assets.all().prefetch_related(
        'asset_role', 'asset_type__manufacturer', 'location', 'location__site' # Prefetch for efficiency
    )
    
    # Create and configure the assets table
    assets_table = AssetTable(related_assets, request=request)
    # Disable pagination for related table or set a smaller page size
    RequestConfig(request, paginate=False).configure(assets_table) 

    # TODO: Fetch related objects for the sidebar list (locations, etc.)
    # related_objects_list = [] 

    context = {
        'object': tag, # Pass object with standard key
        'title': str(tag), # Use tag name as title
        'object_type': model._meta.verbose_name.title(), # Pass verbose name
        'assets_table': assets_table, # Add assets table to context
        'update_url_name': get_model_viewname(model, 'update'),
        'delete_url_name': get_model_viewname(model, 'delete'),
        'view_options': ['update', 'delete'], # Enable buttons
        # 'related_objects_list': related_objects_list, # Add related items later
    }
    # Use the standard detail template path
    return render(request, 'extras/tags/tag_detail.html', context)

@login_required
def tag_create(request):
    if request.method == 'POST':
        form = TagForm(request.POST)
        if form.is_valid():
            form.save()
            # TODO: Message
            return redirect('extras:tag_list')
    else:
        form = TagForm()
    context = {'form': form, 'title': 'Create Tag', 'return_url': 'extras:tag_list'}
    return render(request, 'generic/object_edit.html', context)

@login_required
def tag_update(request, pk):
    tag = get_object_or_404(Tag, pk=pk)
    if request.method == 'POST':
        form = TagForm(request.POST, instance=tag)
        if form.is_valid():
            form.save()
            # TODO: Message
            return redirect('extras:tag_list')
    else:
        form = TagForm(instance=tag)
    context = {'form': form, 'object': tag, 'title': f'Update Tag: {tag.name}', 'return_url': 'extras:tag_list'}
    return render(request, 'generic/object_edit.html', context)

@login_required
def tag_delete(request, pk):
    tag = get_object_or_404(Tag, pk=pk)
    # How to count related items depends on how tags are implemented (e.g., GenericRelation)
    # related_count = tag.taggit_taggeditem_items.count() # Example for django-taggit
    related_count = 0 # Placeholder: Implement actual check if needed
    if request.method == 'POST':
        if related_count > 0:
            # TODO: Add error message - cannot delete tag in use
            return redirect('extras:tag_list')
        tag.delete()
        # TODO: Message
        return redirect('extras:tag_list')

    context = {
        'object': tag,
        'related_objects_count': related_count, # Pass count to template
        'list_url_name': 'extras:tag_list'
    }
    return render(request, 'generic/object_confirm_delete.html', context)

# --- ConfigTemplate Views ---

# Refactor ConfigTemplateListView to use ObjectListView
class ConfigTemplateListView(ObjectListView): # Inherit from ObjectListView
    queryset = ConfigTemplate.objects.all() # Base queryset
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
    template_name = 'generic/object_list_base.html' # Use base template
