from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from .models import Asset, ActivityLog, Category, Manufacturer # Keep only Asset models
from .forms import AssetForm, CategoryForm, ManufacturerForm, AssetCheckOutForm # Keep only Asset forms
from django.contrib.auth import get_user_model
from django_tables2 import RequestConfig
from .tables import AssetTable, CategoryTable, ManufacturerTable
from .filters import AssetFilterSet, CategoryFilterSet, ManufacturerFilterSet # <-- Import the FilterSets
from core.utils import get_paginate_count, get_model_viewname
from django.http import HttpResponse
from django.contrib.contenttypes.models import ContentType
from organization.models import AssetHolderAssignment
from django.urls import reverse
from django.db.models import Q # <-- Import Q (needed by search method in filterset)
from django.contrib import messages # <--- Add this import

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
        'category', 'manufacturer', 'location'
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
        'title': 'Assets', # Title for the page and card header
        'object_type': 'Asset', # Used in the 'Create' button label
        'create_url_name': 'assets:asset_create', # URL name for the create button
        'model_name_str': model_name_str, # Add model name string
        'filter_form': filterset, # <-- Pass the filter form to context
    }
    # Now render the generic template directly
    return render(request, 'generic/object_list_base.html', context)

@login_required
def asset_create(request):
    if request.method == 'POST':
        print("[asset_create] Entered POST block") # DEBUG
        print(f"[asset_create] request.POST: {request.POST}") # DEBUG
        form = AssetForm(request.POST)
        if form.is_valid():
            print("[asset_create] Form IS valid") # DEBUG
            asset = form.save() # Save the new asset
            # TODO: Add success message (django.contrib.messages)
            messages.success(request, f"Asset '{asset}' created successfully.")
            # Redirect to the detail view of the created asset
            return redirect('assets:asset_detail', pk=asset.pk)
        else:
            print("[asset_create] Form IS NOT valid") # DEBUG
            print(f"[asset_create] Form errors: {form.errors}") # DEBUG
    else:
        form = AssetForm() # Create an empty form for GET request

    context = {
        'form': form,
        'title': 'Create New Asset',
        'return_url': 'assets:asset_list', # URL name for the Cancel button
    }
    # Render the generic edit template
    return render(request, 'generic/object_edit.html', context)

@login_required
def asset_detail(request, pk):
    # Fetch asset and related logs efficiently, including asset_holder
    asset = get_object_or_404(
        Asset.objects.select_related('category', 'location', 'manufacturer'),
        pk=pk
    )
    # Fetch assignment separately
    assignment = AssetHolderAssignment.objects.filter(
        content_type=ContentType.objects.get_for_model(Asset),
        object_id=asset.pk
    ).select_related('asset_holder').first()
    
    logs = asset.logs.select_related('user').all() # Fetch logs related to this asset
    
    context = {
        'asset': asset,
        'assignment': assignment, # Pass assignment to context
        'logs': logs, # Add logs to context
    }
    return render(request, 'assets/asset_detail.html', context)

@login_required
def asset_update(request, pk):
    asset = get_object_or_404(Asset, pk=pk) # Get the asset to update
    if request.method == 'POST':
        form = AssetForm(request.POST, instance=asset) # Pass instance for update
        if form.is_valid():
            form.save() # Save the changes
            # TODO: Add success message
            messages.success(request, f"Asset '{asset}' updated successfully.")
            return redirect('assets:asset_detail', pk=asset.pk) # Redirect to detail view
    else:
        form = AssetForm(instance=asset) # Pre-populate form with asset data
    
    context = {
        'form': form,
        'object': asset, # Pass the object being edited
        'title': f'Update Asset: {asset}',
        'return_url': 'assets:asset_list', # Or detail view: 'assets:asset_detail' pk=asset.pk ?
    }
    # Render the generic edit template
    return render(request, 'generic/object_edit.html', context)

@login_required
def asset_delete(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    related_objects_count = 0 # Assets might not have direct blocking relations

    if request.method == 'POST':
        asset_name = asset.name # Store name before deleting for potential message
        asset.delete()
        # TODO: Add success message (e.g., f"Asset '{asset_name}' deleted successfully.")
        messages.success(request, f"Asset '{asset_name}' deleted successfully.")
        return redirect('assets:asset_list')

    context = {
        'object': asset,
        'related_objects_count': related_objects_count,
        'list_url_name': 'assets:asset_list' # For Cancel button and title
    }
    # Render the generic delete confirmation template
    return render(request, 'generic/object_confirm_delete.html', context)

@login_required
def asset_checkout_modal(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    
    # Ensure asset is available before proceeding
    if asset.status != 'available':
        # TODO: Handle error - return forbidden or message?
        return HttpResponse("Asset is not available for assignment.", status=403)

    if request.method == 'POST':
        form = AssetCheckOutForm(request.POST)
        if form.is_valid():
            selected_holder = form.cleaned_data.get('asset_holder') # Use .get()
            selected_location = form.cleaned_data.get('location') # Use .get()
            
            log_action = 'updated' # Default log action
            log_notes = ''

            if selected_holder:
                # Assign to Asset Holder
                AssetHolderAssignment.objects.create(
                    asset_holder=selected_holder,
                    assigned_object=asset
                )
                asset.status = 'in_use' # Set status to in_use for holder assignment
                asset.save()
                log_action = 'checked_out'
                log_notes = f"Assigned to Asset Holder: {selected_holder}"

            elif selected_location:
                # Assign to Location
                asset.location = selected_location
                # Do NOT change status when assigning to location
                asset.save()
                log_action = 'updated' # Or a new 'location_assigned'?
                log_notes = f"Assigned to Location: {selected_location}"
            
            # Always create log entry
            ActivityLog.objects.create(
                asset=asset,
                user=request.user,
                action=log_action, 
                notes=log_notes
            )
            
            # Send HX-Refresh header to reload the main page (asset detail)
            response = HttpResponse("")
            response['HX-Refresh'] = 'true'
            return response
        else:
            # Form is invalid, re-render the modal with errors
            pass # Fall through to render GET part
    else:
        form = AssetCheckOutForm() # Empty form for GET

    context = {
        'form': form,
        'asset': asset,
    }
    return render(request, 'assets/partials/asset_checkout_modal.html', context)

@login_required
@require_POST
def asset_checkin(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    
    # Find the AssetHolderAssignment, if one exists
    assignment = AssetHolderAssignment.objects.filter(
        content_type=ContentType.objects.get_for_model(Asset),
        object_id=asset.pk
    ).select_related('asset_holder').first()
    
    # Check if the asset has an assignment record first
    if assignment:
        # Check in from Asset Holder
        checked_in_from = assignment.asset_holder
        from_str = str(checked_in_from) if checked_in_from else 'N/A'
        
        assignment.delete()
        asset.status = 'available' # Set status back to available
        asset.save()
        
        ActivityLog.objects.create(
            asset=asset, 
            user=request.user, 
            action='checked_in',
            notes=f"Checked in from Asset Holder: {from_str}" 
        )
        # TODO: Add success message
        messages.success(request, f"Asset '{asset}' successfully checked in from Asset Holder: {from_str}.")
    elif asset.location:
        # Check in (clear) from Location
        checked_in_from = asset.location
        from_str = str(checked_in_from) if checked_in_from else 'N/A'
        
        asset.location = None
        # Do NOT change status when clearing location
        asset.save()
        
        ActivityLog.objects.create(
            asset=asset, 
            user=request.user, 
            action='checked_in', # Still log as checked_in
            notes=f"Checked in from Location: {from_str}" 
        )
        # TODO: Add success message
        messages.success(request, f"Asset '{asset}' successfully checked in from Location: {from_str}.")
    else:
        # Asset was not assigned to a holder or location
        # TODO: Add potential error message (e.g., asset not checked out)
        messages.warning(request, f"Asset '{asset}' was not checked out to a holder or assigned to a location.")
        
    return redirect('assets:asset_detail', pk=asset.pk)

# --- Category (Asset Role) Views ---

@login_required
def category_list(request):
    queryset = Category.objects.all()
    
    # Apply filters
    filterset = CategoryFilterSet(request.GET, queryset=queryset)
    queryset = filterset.qs

    table = CategoryTable(queryset, request=request)
    RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)

    model = table.Meta.model
    model_name_str = f"{model._meta.app_label}.{model._meta.model_name}"

    context = {
        'table': table,
        'title': 'Categories', # Title for the page
        'object_type': 'Category', # For Create button
        'create_url_name': 'assets:category_create',
        'list_url_name': 'assets:category_list', # For potential future use (e.g., breadcrumbs)
        'model_name_str': model_name_str, # Add model name string
        'filter_form': filterset, # <-- Add filter form
    }
    return render(request, 'generic/object_list_base.html', context)

@login_required
def category_detail(request, pk):
    category = get_object_or_404(
        Category.objects.prefetch_related(
            'asset_set', 'asset_set__manufacturer', 'asset_set__location' # Prefetch assets and their FKs
        ), 
        pk=pk
    )

    # Prepare Assets table
    assets_table = AssetTable(category.asset_set.all(), request=request)
    RequestConfig(request, paginate=False).configure(assets_table)

    model = Category

    # --- Prepare Related Objects List ---
    related_objects_list = []
    # Assets
    asset_count = category.asset_set.count()
    if asset_count:
        # Add filtering parameters to the URL
        asset_list_url = reverse('assets:asset_list')
        filtered_asset_url = f"{asset_list_url}?category={category.pk}"
        related_objects_list.append({
            'label': 'Assets',
            'count': asset_count,
            'url': filtered_asset_url # <-- Use filtered URL
        })
    # --- End Related Objects List ---

    context = {
        'object': category,
        'title': str(category),
        'object_type': model._meta.verbose_name.title(),
        'assets_table': assets_table,
        'update_url_name': get_model_viewname(model, 'update'),
        'delete_url_name': get_model_viewname(model, 'delete'),
        'view_options': ['update', 'delete'],
        'related_objects_list': related_objects_list,
    }
    return render(request, 'assets/category_detail.html', context)

@login_required
def category_create(request):
    if request.method == 'POST':
        form = CategoryForm(request.POST)
        if form.is_valid():
            form.save()
            # TODO: Message
            messages.success(request, f"Category '{form.cleaned_data['name']}' created successfully.")
            return redirect('assets:category_list')
    else:
        form = CategoryForm()
    context = {
        'form': form,
        'title': 'Create New Category',
        'return_url': 'assets:category_list',
    }
    # Render the generic edit template
    return render(request, 'generic/object_edit.html', context)

@login_required
def category_update(request, pk):
    category = get_object_or_404(Category, pk=pk)
    if request.method == 'POST':
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            # TODO: Message
            messages.success(request, f"Category '{category.name}' updated successfully.")
            return redirect('assets:category_list')
    else:
        form = CategoryForm(instance=category)
    context = {
        'form': form,
        'object': category, # Pass the object being edited
        'title': f'Update Category: {category.name}',
        'return_url': 'assets:category_list',
    }
    # Render the generic edit template
    return render(request, 'generic/object_edit.html', context)

@login_required
def category_delete(request, pk):
    category = get_object_or_404(Category, pk=pk)
    related_objects_count = category.asset_set.count() # Check related assets

    if request.method == 'POST':
        if related_objects_count > 0:
            # Error handling for deletion prevention should ideally be done here
            # or rely on the template displaying the warning correctly.
            # For now, just redirecting back as before if POST attempted on protected object.
            # TODO: Add message if deletion is prevented
            messages.error(request, f"Category '{category.name}' cannot be deleted because it is associated with {related_objects_count} asset(s).")
            return redirect('assets:category_list')
        category_name = category.name # Store name for message
        category.delete()
        # TODO: Message
        messages.success(request, f"Category '{category_name}' deleted successfully.")
        return redirect('assets:category_list')

    context = {
        'object': category,
        'related_objects_count': related_objects_count,
        'list_url_name': 'assets:category_list'
    }
    # Render the generic delete confirmation template
    return render(request, 'generic/object_confirm_delete.html', context)

# --- Manufacturer Views ---

@login_required
def manufacturer_list(request):
    queryset = Manufacturer.objects.all()

    # Apply filters
    filterset = ManufacturerFilterSet(request.GET, queryset=queryset)
    queryset = filterset.qs

    table = ManufacturerTable(queryset, request=request)
    RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)

    model = table.Meta.model
    model_name_str = f"{model._meta.app_label}.{model._meta.model_name}"

    context = {
        'table': table,
        'title': 'Manufacturers',
        'object_type': 'Manufacturer',
        'create_url_name': 'assets:manufacturer_create',
        'list_url_name': 'assets:manufacturer_list',
        'model_name_str': model_name_str, # Add to context
        'filter_form': filterset, # <-- Add filter form
    }
    return render(request, 'generic/object_list_base.html', context)

@login_required
def manufacturer_detail(request, pk):
    manufacturer = get_object_or_404(
        Manufacturer.objects.prefetch_related(
            'assets', 'assets__category', 'assets__location' # Prefetch assets and their FKs for table
        ), 
        pk=pk
    )

    # Prepare Assets table
    assets_table = AssetTable(manufacturer.assets.all(), request=request)
    RequestConfig(request, paginate=False).configure(assets_table)

    model = Manufacturer

    # --- Prepare Related Objects List ---
    related_objects_list = []
    # Assets
    asset_count = manufacturer.assets.count()
    if asset_count:
        # Add filtering parameters to the URL
        asset_list_url = reverse('assets:asset_list')
        filtered_asset_url = f"{asset_list_url}?manufacturer={manufacturer.pk}"
        related_objects_list.append({
            'label': 'Assets',
            'count': asset_count,
            'url': filtered_asset_url # <-- Use filtered URL
        })
    # --- End Related Objects List ---

    context = {
        'object': manufacturer,
        'title': str(manufacturer),
        'object_type': model._meta.verbose_name.title(),
        'assets_table': assets_table,
        'update_url_name': get_model_viewname(model, 'update'),
        'delete_url_name': get_model_viewname(model, 'delete'),
        'view_options': ['update', 'delete'],
        'related_objects_list': related_objects_list,
    }
    return render(request, 'assets/manufacturer_detail.html', context)

@login_required
def manufacturer_create(request):
    if request.method == 'POST':
        form = ManufacturerForm(request.POST)
        if form.is_valid():
            form.save()
            # TODO: Message
            messages.success(request, f"Manufacturer '{form.cleaned_data['name']}' created successfully.")
            return redirect('assets:manufacturer_list')
    else:
        form = ManufacturerForm()
    context = {
        'form': form,
        'title': 'Create New Manufacturer',
        'return_url': 'assets:manufacturer_list',
    }
    # Render the generic edit template
    return render(request, 'generic/object_edit.html', context)

@login_required
def manufacturer_update(request, pk):
    manufacturer = get_object_or_404(Manufacturer, pk=pk)
    if request.method == 'POST':
        form = ManufacturerForm(request.POST, instance=manufacturer)
        if form.is_valid():
            form.save()
            # TODO: Message
            messages.success(request, f"Manufacturer '{manufacturer.name}' updated successfully.")
            return redirect('assets:manufacturer_list')
    else:
        form = ManufacturerForm(instance=manufacturer)
    context = {
        'form': form,
        'object': manufacturer, # Pass the object being edited
        'title': f'Update Manufacturer: {manufacturer.name}',
        'return_url': 'assets:manufacturer_list',
    }
    # Render the generic edit template
    return render(request, 'generic/object_edit.html', context)

@login_required
def manufacturer_delete(request, pk):
    manufacturer = get_object_or_404(Manufacturer, pk=pk)
    related_objects_count = manufacturer.assets.count() # Check related assets

    if request.method == 'POST':
        if related_objects_count > 0:
             # TODO: Add message if deletion is prevented
            messages.error(request, f"Manufacturer '{manufacturer.name}' cannot be deleted because it is associated with {related_objects_count} asset(s).")
            return redirect('assets:manufacturer_list')
        manufacturer_name = manufacturer.name # Store name for message
        manufacturer.delete()
        # TODO: Message
        messages.success(request, f"Manufacturer '{manufacturer_name}' deleted successfully.")
        return redirect('assets:manufacturer_list')

    context = {
        'object': manufacturer,
        'related_objects_count': related_objects_count,
        'list_url_name': 'assets:manufacturer_list'
    }
    # Render the generic delete confirmation template
    return render(request, 'generic/object_confirm_delete.html', context)

# Site, Region, SiteGroup views moved to organization/views.py
