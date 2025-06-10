from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import Tag
from .forms import TagForm # Assuming a TagForm exists
from django_tables2 import RequestConfig
from .tables import TagTable
from .filters import TagFilterSet # <-- Import FilterSet
from core.utils import get_paginate_count, get_model_viewname # Import the utility function
from assets.tables import AssetTable # Import AssetTable
from users.models import UserPreference # Import UserPreference

# Create your views here.

# --- Tag Views ---

@login_required
def tag_list(request):
    queryset = Tag.objects.all()
    filterset = TagFilterSet(request.GET, queryset=queryset)
    queryset = filterset.qs

    # --- Determine Columns & Configure Table ---
    TableClass = TagTable
    all_available_columns = list(TableClass.base_columns.keys()) 
    prefs, _ = UserPreference.objects.get_or_create(user=request.user)
    app_label = TableClass._meta.model._meta.app_label
    table_class_name = TableClass.__name__
    user_config = prefs.data.get('tables', {}).get(app_label, {}).get(table_class_name, {})
    saved_visible_columns = user_config.get('columns', None)
    
    final_sequence = []
    if saved_visible_columns is not None:
        final_sequence = [col for col in saved_visible_columns if col in all_available_columns]
    else:
        meta = getattr(TableClass, 'Meta', None)
        if hasattr(meta, 'default_columns'):
             final_sequence = [col for col in meta.default_columns if col in all_available_columns]
        elif hasattr(meta, 'fields'):
            final_sequence = [col for col in meta.fields if col in all_available_columns]
        else:
            final_sequence = all_available_columns
            
    if 'pk' in final_sequence: final_sequence.remove('pk')
    if 'actions' in final_sequence: final_sequence.remove('actions')
    if 'pk' in all_available_columns: final_sequence.insert(0, 'pk')
    if 'actions' in all_available_columns: final_sequence.append('actions')
    
    columns_to_exclude = tuple(col for col in all_available_columns if col not in final_sequence)
    table = TableClass(queryset, request=request, sequence=tuple(final_sequence), exclude=columns_to_exclude)
    RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)
    # --- End Configuration ---

    model = table.Meta.model
    model_name_str = f"{model._meta.app_label}.{model._meta.model_name}" # For bulk delete form
    table_config_key = f"{model._meta.app_label}.{table.__class__.__name__}" # For config modal URL

    context = {
        'table': table,
        'title': 'Tags',
        'object_type': 'Tag',
        'create_url_name': 'extras:tag_create',
        'list_url_name': 'extras:tag_list',
        'model_name_str': model_name_str, # Pass the app_label.modelname
        'table_config_key': table_config_key, # Pass the app_label.TableName
        'filter_form': filterset,
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
