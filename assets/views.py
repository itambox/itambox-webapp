from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
# Updated imports
from .models import Asset, ActivityLog, AssetRole, Manufacturer
from .forms import AssetForm, AssetRoleForm, ManufacturerForm, AssetCheckOutForm
from django.contrib.auth import get_user_model
from django_tables2 import RequestConfig
# Updated imports
from .tables import AssetTable, AssetRoleTable, ManufacturerTable
from .filters import AssetFilterSet, AssetRoleFilterSet, ManufacturerFilterSet
from assetbox.core.utils import get_paginate_count, get_model_viewname
from django.http import HttpResponse
from django.contrib.contenttypes.models import ContentType
from assetbox.organization.models import AssetHolderAssignment
from django.urls import reverse
from django.db.models import Q
from django.contrib import messages

User = get_user_model()

# Create your views here.

@login_required # Ensure user is logged in to see the dashboard
def dashboard(request):
    # We can add context data here later (e.g., asset counts)
    context = {}
    return render(request, 'dashboard.html', context)

@login_required
def asset_list(request):
    # Start with base queryset
    queryset = Asset.objects.all().select_related(
        'asset_role', 'manufacturer', 'location' # Updated field
    )

    # Apply filters
    filterset = AssetFilterSet(request.GET, queryset=queryset)
    queryset = filterset.qs # Use the filtered queryset

    # Create and configure table with the filtered queryset
    table = AssetTable(queryset, request=request)
    RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)

    model = table.Meta.model
    model_name_str = f"{model._meta.app_label}.{model._meta.model_name}"

    context = {
        'table': table,
        'title': 'Assets',
        'object_type': 'Asset',
        'create_url_name': 'assets:asset_create',
        'model_name_str': model_name_str,
        'filter_form': filterset,
    }
    return render(request, 'generic/object_list_base.html', context)

@login_required
def asset_create(request):
    if request.method == 'POST':
        form = AssetForm(request.POST)
        if form.is_valid():
            asset = form.save()
            messages.success(request, f"Asset '{asset}' created successfully.")
            return redirect('assets:asset_detail', pk=asset.pk)
    else:
        form = AssetForm()

    context = {
        'form': form,
        'title': 'Create New Asset',
        'return_url': 'assets:asset_list',
    }
    return render(request, 'generic/object_edit.html', context)

@login_required
def asset_detail(request, pk):
    asset = get_object_or_404(
        Asset.objects.select_related('asset_role', 'location', 'manufacturer'), # Updated field
        pk=pk
    )
    assignment = AssetHolderAssignment.objects.filter(
        content_type=ContentType.objects.get_for_model(Asset),
        object_id=asset.pk
    ).select_related('asset_holder').first()
    
    logs = asset.logs.select_related('user').all()
    
    context = {
        'asset': asset,
        'assignment': assignment,
        'logs': logs,
    }
    return render(request, 'assets/assets/asset_detail.html', context)

@login_required
def asset_update(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    if request.method == 'POST':
        form = AssetForm(request.POST, instance=asset)
        if form.is_valid():
            form.save()
            messages.success(request, f"Asset '{asset}' updated successfully.")
            return redirect('assets:asset_detail', pk=asset.pk)
    else:
        form = AssetForm(instance=asset)
    
    context = {
        'form': form,
        'object': asset,
        'title': f'Update Asset: {asset}',
        'return_url': 'assets:asset_list',
    }
    return render(request, 'generic/object_edit.html', context)

@login_required
def asset_delete(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    related_objects_count = 0

    if request.method == 'POST':
        asset_name = asset.name
        asset.delete()
        messages.success(request, f"Asset '{asset_name}' deleted successfully.")
        return redirect('assets:asset_list')

    context = {
        'object': asset,
        'related_objects_count': related_objects_count,
        'list_url_name': 'assets:asset_list'
    }
    return render(request, 'generic/object_confirm_delete.html', context)

@login_required
def asset_checkout_modal(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    
    if asset.status != 'available':
        return HttpResponse("Asset is not available for assignment.", status=403)

    if request.method == 'POST':
        form = AssetCheckOutForm(request.POST)
        if form.is_valid():
            selected_holder = form.cleaned_data.get('asset_holder')
            selected_location = form.cleaned_data.get('location')
            
            log_action = 'updated'
            log_notes = ''

            if selected_holder:
                AssetHolderAssignment.objects.create(
                    asset_holder=selected_holder,
                    assigned_object=asset
                )
                asset.status = 'in_use'
                asset.save()
                log_action = 'checked_out'
                log_notes = f"Assigned to Asset Holder: {selected_holder}"

            elif selected_location:
                asset.location = selected_location
                asset.save()
                log_action = 'updated'
                log_notes = f"Assigned to Location: {selected_location}"
            
            ActivityLog.objects.create(
                asset=asset,
                user=request.user,
                action=log_action, 
                notes=log_notes
            )
            
            response = HttpResponse("")
            response['HX-Refresh'] = 'true'
            return response
        else:
            pass
    else:
        form = AssetCheckOutForm()

    context = {
        'form': form,
        'asset': asset,
    }
    return render(request, 'assets/includes/asset_checkout_modal.html', context)

@login_required
@require_POST
def asset_checkin(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    
    assignment = AssetHolderAssignment.objects.filter(
        content_type=ContentType.objects.get_for_model(Asset),
        object_id=asset.pk
    ).select_related('asset_holder').first()
    
    if assignment:
        checked_in_from = assignment.asset_holder
        from_str = str(checked_in_from) if checked_in_from else 'N/A'
        
        assignment.delete()
        asset.status = 'available'
        asset.save()
        
        ActivityLog.objects.create(
            asset=asset, 
            user=request.user, 
            action='checked_in',
            notes=f"Checked in from Asset Holder: {from_str}" 
        )
        messages.success(request, f"Asset '{asset}' successfully checked in from Asset Holder: {from_str}.")
    elif asset.location:
        checked_in_from = asset.location
        from_str = str(checked_in_from) if checked_in_from else 'N/A'
        
        asset.location = None
        asset.save()
        
        ActivityLog.objects.create(
            asset=asset, 
            user=request.user, 
            action='checked_in',
            notes=f"Checked in from Location: {from_str}" 
        )
        messages.success(request, f"Asset '{asset}' successfully checked in from Location: {from_str}.")
    else:
        messages.warning(request, f"Asset '{asset}' was not checked out to a holder or assigned to a location.")
        
    return redirect('assets:asset_detail', pk=asset.pk)

# --- Asset Role (Category) Views --- Renamed section

@login_required
def assetrole_list(request): # Renamed view
    queryset = AssetRole.objects.all() # Updated model
    
    # Apply filters
    filterset = AssetRoleFilterSet(request.GET, queryset=queryset) # Updated filter
    queryset = filterset.qs

    table = AssetRoleTable(queryset, request=request) # Updated table
    RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)

    model = table.Meta.model
    model_name_str = f"{model._meta.app_label}.{model._meta.model_name}"

    context = {
        'table': table,
        'title': 'Asset Roles', # Updated title
        'object_type': 'Asset Role', # Updated object type
        'create_url_name': 'assets:assetrole_create', # Updated URL name
        'list_url_name': 'assets:assetrole_list', # Updated URL name
        'model_name_str': model_name_str,
        'filter_form': filterset,
    }
    return render(request, 'generic/object_list_base.html', context)

@login_required
def assetrole_detail(request, pk): # Renamed view
    asset_role = get_object_or_404( # Renamed variable
        AssetRole.objects.prefetch_related( # Updated model
            'assets', 'assets__manufacturer', 'assets__location' # Updated related name
        ), 
        pk=pk
    )

    # Prepare Assets table
    assets_table = AssetTable(asset_role.assets.all(), request=request) # Renamed variable, updated related name
    RequestConfig(request, paginate=False).configure(assets_table)

    model = AssetRole # Updated model

    # --- Prepare Related Objects List ---
    related_objects_list = []
    # Assets
    asset_count = asset_role.assets.count() # Renamed variable, updated related name
    if asset_count:
        # Add filtering parameters to the URL
        asset_list_url = reverse('assets:asset_list')
        filtered_asset_url = f"{asset_list_url}?asset_role={asset_role.slug}" # Updated filter param name
        related_objects_list.append({
            'label': 'Assets',
            'count': asset_count,
            'url': filtered_asset_url
        })
    # --- End Related Objects List ---

    context = {
        'object': asset_role, # Renamed variable
        'title': str(asset_role), # Renamed variable
        'object_type': model._meta.verbose_name.title(),
        'assets_table': assets_table,
        'update_url_name': get_model_viewname(model, 'update'), # URL name updated via reverse lookup
        'delete_url_name': get_model_viewname(model, 'delete'), # URL name updated via reverse lookup
        'view_options': ['update', 'delete'],
        'related_objects_list': related_objects_list,
    }
    # Update template path
    return render(request, 'assets/assetroles/assetrole_detail.html', context)

@login_required
def assetrole_create(request): # Renamed view
    if request.method == 'POST':
        form = AssetRoleForm(request.POST) # Updated form
        if form.is_valid():
            asset_role = form.save() # Renamed variable
            messages.success(request, f"Asset Role '{asset_role.name}' created successfully.") # Updated text
            return redirect('assets:assetrole_list') # Updated URL name
    else:
        form = AssetRoleForm() # Updated form
    context = {
        'form': form,
        'title': 'Create New Asset Role', # Updated title
        'return_url': 'assets:assetrole_list', # Updated URL name
    }
    return render(request, 'generic/object_edit.html', context)

@login_required
def assetrole_update(request, pk): # Renamed view
    asset_role = get_object_or_404(AssetRole, pk=pk) # Updated model, renamed variable
    if request.method == 'POST':
        form = AssetRoleForm(request.POST, instance=asset_role) # Updated form, renamed variable
        if form.is_valid():
            form.save()
            messages.success(request, f"Asset Role '{asset_role.name}' updated successfully.") # Updated text
            return redirect('assets:assetrole_list') # Updated URL name
    else:
        form = AssetRoleForm(instance=asset_role) # Updated form, renamed variable
    context = {
        'form': form,
        'object': asset_role, # Renamed variable
        'title': f'Update Asset Role: {asset_role.name}', # Updated title
        'return_url': 'assets:assetrole_list', # Updated URL name
    }
    return render(request, 'generic/object_edit.html', context)

@login_required
def assetrole_delete(request, pk): # Renamed view
    asset_role = get_object_or_404(AssetRole, pk=pk) # Updated model, renamed variable
    related_objects_count = asset_role.assets.count() # Renamed variable, updated related name

    if request.method == 'POST':
        if related_objects_count > 0:
            messages.error(request, f"Asset Role '{asset_role.name}' cannot be deleted because it is associated with {related_objects_count} asset(s).") # Updated text
            return redirect('assets:assetrole_list') # Updated URL name
        asset_role_name = asset_role.name # Renamed variable
        asset_role.delete() # Renamed variable
        messages.success(request, f"Asset Role '{asset_role_name}' deleted successfully.") # Updated text
        return redirect('assets:assetrole_list') # Updated URL name

    context = {
        'object': asset_role, # Renamed variable
        'related_objects_count': related_objects_count,
        'list_url_name': 'assets:assetrole_list' # Updated URL name
    }
    return render(request, 'generic/object_confirm_delete.html', context)

# --- Manufacturer Views --- (No changes needed here)
# ... (manufacturer views remain the same) ... 