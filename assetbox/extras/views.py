from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import Tag
from .forms import TagForm # Assuming a TagForm exists
from django_tables2 import RequestConfig
from .tables import TagTable
from .filters import TagFilterSet # <-- Import FilterSet
from core.utils import get_paginate_count, get_model_viewname # Import the utility function
from assets.tables import AssetTable # Import AssetTable

# Create your views here.

# --- Tag Views ---

@login_required
def tag_list(request):
    queryset = Tag.objects.all()

    # Apply filters
    filterset = TagFilterSet(request.GET, queryset=queryset)
    queryset = filterset.qs

    table = TagTable(queryset, request=request)
    RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)

    model = table.Meta.model
    model_name_str = f"{model._meta.app_label}.{model._meta.model_name}"

    context = {
        'table': table,
        'title': 'Tags',
        'object_type': 'Tag',
        'create_url_name': 'extras:tag_create',
        'list_url_name': 'extras:tag_list',
        'model_name_str': model_name_str,
        'filter_form': filterset, # <-- Add filter form
    }
    return render(request, 'generic/object_list_base.html', context)

@login_required
def tag_detail(request, pk):
    tag = get_object_or_404(Tag, pk=pk)
    model = Tag # Get the model class

    # Fetch related assets using the related_name from Asset.tags
    related_assets = tag.assets.all().prefetch_related(
        'asset_role', 'manufacturer', 'location', 'location__site' # Prefetch for efficiency
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
