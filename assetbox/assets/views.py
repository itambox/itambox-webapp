from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from .models import Asset, ActivityLog, AssetRole, Manufacturer, AssetType # Keep only Asset models
from .forms import AssetForm, AssetRoleForm, ManufacturerForm, AssetCheckOutForm, AssetTypeForm # Keep only Asset forms
from django.contrib.auth import get_user_model
from django_tables2 import RequestConfig
from .tables import AssetTable, AssetRoleTable, ManufacturerTable, AssetTypeTable
from .filters import AssetFilterSet, AssetRoleFilterSet, ManufacturerFilterSet, AssetTypeFilterSet 
# --- Add imports needed for CBVs --- 
from . import filters
from . import forms
from . import tables
# --- End imports ---
from core.utils import get_paginate_count, get_model_viewname
from django.http import HttpResponse, HttpResponseBadRequest
from django.contrib.contenttypes.models import ContentType
from organization.models import AssetHolderAssignment, AssetHolder, Location
from django.urls import reverse, reverse_lazy
from django.db.models import Q # <-- Import Q (needed by search method in filterset)
from django.contrib import messages # <--- Add this import
from users.models import UserPreference # Import UserPreference
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib.auth.mixins import LoginRequiredMixin
from core.views import ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView

User = get_user_model()

# Create your views here.

@login_required # Ensure user is logged in to see the dashboard
def dashboard(request):
    # We can add context data here later (e.g., asset counts)
    context = {}
    return render(request, 'dashboard.html', context)

# @login_required
# def asset_list(request):
#     queryset = Asset.objects.all().select_related(
#         'asset_role', 
#         'asset_type',  # Select the related asset_type
#         'asset_type__manufacturer', # Select the manufacturer via asset_type
#         'location'
#     )
#     filterset = AssetFilterSet(request.GET, queryset=queryset)
#     queryset = filterset.qs 
# 
#     # --- Determine Columns to Show/Exclude --- 
#     TableClass = AssetTable # Get the table class
#     all_available_columns = list(TableClass.base_columns.keys()) # List of all columns defined on the table
#     
#     prefs, created_at = UserPreference.objects.get_or_create(user=request.user)
#     
#     app_label = TableClass._meta.model._meta.app_label
#     table_class_name = TableClass.__name__
# 
#     user_config = prefs.data.get('tables', {}).get(app_label, {}).get(table_class_name, {}) 
#     
#     saved_visible_columns = user_config.get('columns', None) 
# 
#     # --- Determine Final Visible Column Sequence --- 
#     final_sequence = []
#     if saved_visible_columns is not None:
#         # User has preferences: Use their saved list, ensuring columns still exist
#         final_sequence = [col for col in saved_visible_columns if col in all_available_columns]
#     else:
#         # No user preference: use table defaults
#         meta = getattr(TableClass, 'Meta', None)
#         if hasattr(meta, 'default_columns'):
#              final_sequence = [col for col in meta.default_columns if col in all_available_columns]
#         elif hasattr(meta, 'fields'):
#             final_sequence = [col for col in meta.fields if col in all_available_columns]
#         else:
#             # Fallback: Show all available columns if no defaults defined
#             final_sequence = all_available_columns
#     
#     # --- Ensure pk and actions are correctly positioned --- 
#     # Remove them first to avoid duplicates and control position
#     if 'pk' in final_sequence: final_sequence.remove('pk')
#     if 'actions' in final_sequence: final_sequence.remove('actions')
#     
#     # Add them back in desired positions (if they exist on the table class)
#     if 'pk' in all_available_columns:
#         final_sequence.insert(0, 'pk')
#     if 'actions' in all_available_columns:
#         final_sequence.append('actions')
# 
#     # Instantiate table using BOTH sequence and exclude for maximum explicitness
#     table = TableClass(
#         queryset, 
#         request=request, 
#         sequence=tuple(final_sequence), 
#         exclude=tuple(col for col in all_available_columns if col not in final_sequence)
#     )
# 
#     # Configure pagination (and sorting if needed)
#     RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)
# 
#     model = table.Meta.model
#     # --- Revert model_name_str and add table_config_key --- 
#     model_name_str = f"{model._meta.app_label}.{model._meta.model_name}" # For bulk delete form
#     table_config_key = f"{model._meta.app_label}.{table.__class__.__name__}" # For config modal URL
# 
#     context = {
#         'table': table,
#         'title': 'Assets',
#         'object_type': 'Asset',
#         'create_url_name': 'assets:asset_create',
#         'model_name_str': model_name_str, # Pass the app_label.modelname
#         'table_config_key': table_config_key, # Pass the app_label.TableName
#         'filter_form': filterset.form,
#     }
#     
#     return render(request, 'generic/object_list_base.html', context)

# --- Asset Views (Refactored to CBV) ---
class AssetListView(ObjectListView):
    queryset = Asset.objects.all().select_related(
        'asset_role', 
        'asset_type', 
        'asset_type__manufacturer',
        'location'
    )
    filterset = filters.AssetFilterSet
    filterset_form = forms.AssetFilterForm # Corrected: Point to AssetFilterForm
    table = tables.AssetTable
    action_buttons = ('add',) # Add action_buttons
    # template_name = 'assets/assets/asset_list.html' # Optionally override generic template
    # Define context overrides if needed, otherwise base class handles it

@login_required
def asset_create(request):
    if request.method == 'POST':
        form = AssetForm(request.POST)
        if form.is_valid():
            asset = form.save() # Save the new asset
            # TODO: Add success message (django.contrib.messages)
            messages.success(request, f"Asset '{asset}' created successfully.")
            # Redirect to the detail view of the created asset
            return redirect('assets:asset_detail', pk=asset.pk)
        else:
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
        Asset.objects.select_related('asset_role', 'location', 'asset_type', 'asset_type__manufacturer'),
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
    return render(request, 'assets/assets/asset_detail.html', context)

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
    return render(request, 'assets/includes/asset_checkout_modal.html', context)

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

# --- AssetRole (Asset Role) Views (Refactored to CBV) ---

class AssetRoleListView(ObjectListView):
    queryset = AssetRole.objects.all()
    filterset = filters.AssetRoleFilterSet
    filterset_form = forms.AssetRoleFilterForm # Corrected: Point to AssetRoleFilterForm
    table = tables.AssetRoleTable
    action_buttons = ('add',) # Add action_buttons
    # template_name = 'assets/assetroles/assetrole_list.html' # Optionally override

@login_required
def assetrole_detail(request, pk):
    asset_role = get_object_or_404(
        AssetRole.objects.prefetch_related(
            'asset_set', 'asset_set__manufacturer', 'asset_set__location' # Prefetch assets and their FKs
        ), 
        pk=pk
    )

    # Prepare Assets table
    assets_table = AssetTable(asset_role.asset_set.all(), request=request)
    RequestConfig(request, paginate=False).configure(assets_table)

    model = AssetRole

    # --- Prepare Related Objects List ---
    related_objects_list = []
    # Assets
    asset_count = asset_role.asset_set.count()
    if asset_count:
        # Add filtering parameters to the URL
        asset_list_url = reverse('assets:asset_list')
        filtered_asset_url = f"{asset_list_url}?asset_role={asset_role.pk}"
        related_objects_list.append({
            'label': 'Assets',
            'count': asset_count,
            'url': filtered_asset_url # <-- Use filtered URL
        })
    # --- End Related Objects List ---

    context = {
        'object': asset_role,
        'title': str(asset_role),
        'object_type': model._meta.verbose_name.title(),
        'assets_table': assets_table,
        'update_url_name': get_model_viewname(model, 'update'),
        'delete_url_name': get_model_viewname(model, 'delete'),
        'view_options': ['update', 'delete'],
        'related_objects_list': related_objects_list,
    }
    return render(request, 'assets/assetroles/assetrole_detail.html', context)

@login_required
def assetrole_create(request):
    if request.method == 'POST':
        form = AssetRoleForm(request.POST)
        if form.is_valid():
            asset_role = form.save()
            messages.success(request, f"Asset Role '{asset_role}' created successfully.")
            # Use standardized URL name
            return redirect('assets:assetrole_detail', pk=asset_role.pk)
    else:
        form = AssetRoleForm()
    context = {
        'form': form,
        'title': 'Create New Asset Role',
        # Use standardized URL name
        'return_url': 'assets:assetrole_list',
    }
    return render(request, 'generic/object_edit.html', context)

@login_required
def assetrole_update(request, pk):
    asset_role = get_object_or_404(AssetRole, pk=pk)
    if request.method == 'POST':
        form = AssetRoleForm(request.POST, instance=asset_role)
        if form.is_valid():
            form.save()
            messages.success(request, f"Asset Role '{asset_role}' updated successfully.")
            # Use standardized URL name
            return redirect('assets:assetrole_detail', pk=asset_role.pk)
    else:
        form = AssetRoleForm(instance=asset_role)
    context = {
        'form': form,
        'object': asset_role,
        'title': f'Update Asset Role: {asset_role}',
        # Use standardized URL name
        'return_url': 'assets:assetrole_list', 
    }
    return render(request, 'generic/object_edit.html', context)

@login_required
def assetrole_delete(request, pk):
    asset_role = get_object_or_404(AssetRole, pk=pk)
    related_objects_count = asset_role.asset_set.count() # Check related assets

    if request.method == 'POST':
        if related_objects_count > 0:
            # Error handling for deletion prevention should ideally be done here
            # or rely on the template displaying the warning correctly.
            # For now, just redirecting back as before if POST attempted on protected object.
            # TODO: Add message if deletion is prevented
            messages.error(request, f"AssetRole '{asset_role.name}' cannot be deleted because it is associated with {related_objects_count} asset(s).")
            return redirect('assets:assetrole_list')
        assetrole_name = asset_role.name # Store name for message
        asset_role.delete()
        # TODO: Message
        messages.success(request, f"AssetRole '{assetrole_name}' deleted successfully.")
        return redirect('assets:assetrole_list')

    context = {
        'object': asset_role,
        'related_objects_count': related_objects_count,
        # Use standardized URL name
        'list_url_name': 'assets:assetrole_list'
    }
    # Render the generic delete confirmation template
    return render(request, 'generic/object_confirm_delete.html', context)

# --- Manufacturer Views (Refactored to CBV) ---

class ManufacturerListView(ObjectListView):
    queryset = Manufacturer.objects.all()
    filterset = filters.ManufacturerFilterSet
    filterset_form = forms.ManufacturerFilterForm # Corrected: Point to ManufacturerFilterForm
    table = tables.ManufacturerTable
    action_buttons = ('add',) # Add action_buttons
    # template_name = 'assets/manufacturers/manufacturer_list.html' # Optionally override

@login_required
def manufacturer_detail(request, pk):
    manufacturer = get_object_or_404(
        Manufacturer.objects.prefetch_related(
            'asset_types', # Prefetch related AssetType objects
            'asset_types__assets', # Prefetch Assets via AssetType
            'asset_types__assets__asset_role', # Prefetch AssetRole via AssetType->Asset
            'asset_types__assets__location'  # Prefetch Location via AssetType->Asset
        ), 
        pk=pk
    )

    # Prepare Assets table by filtering Assets based on the manufacturer via AssetType
    related_assets = Asset.objects.filter(asset_type__manufacturer=manufacturer).select_related(
        'asset_type', 'asset_role', 'location' # Necessary select_related for the table
    )
    assets_table = AssetTable(related_assets, request=request)
    RequestConfig(request, paginate=False).configure(assets_table)

    model = Manufacturer

    # --- Prepare Related Objects List --- 
    related_objects_list = []
    # Asset Types linked to this Manufacturer
    asset_type_count = manufacturer.asset_types.count()
    if asset_type_count:
        asset_type_list_url = reverse('assets:assettype_list')
        filtered_asset_type_url = f"{asset_type_list_url}?manufacturer={manufacturer.pk}"
        related_objects_list.append({
            'label': 'Asset Types',
            'count': asset_type_count,
            'url': filtered_asset_type_url
        })
        
    # Assets (count derived from the filtered queryset)
    asset_count = related_assets.count()
    if asset_count:
        asset_list_url = reverse('assets:asset_list')
        # Filter assets by manufacturer indirectly via asset_type
        filtered_asset_url = f"{asset_list_url}?asset_type__manufacturer={manufacturer.pk}" 
        related_objects_list.append({
            'label': 'Assets',
            'count': asset_count,
            'url': filtered_asset_url
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
    return render(request, 'assets/manufacturers/manufacturer_detail.html', context)

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

# --- Asset Type Views (Class-Based) ---

# Refactor AssetTypeListView to use ObjectListView
class AssetTypeListView(ObjectListView): # Inherit from ObjectListView
    queryset = AssetType.objects.select_related('manufacturer') # Keep the base queryset
    filterset = filters.AssetTypeFilterSet # Keep filterset
    filterset_form = forms.AssetTypeFilterForm # Explicitly set the filter form
    table = tables.AssetTypeTable # Keep the table
    action_buttons = ('add',) # Define action buttons like others
    # Remove template_name - ObjectListView handles it
    # Remove context_object_name - ObjectListView handles it
    # Remove paginate_by - ObjectListView handles it via get_paginate_count
    # Remove get_queryset method - ObjectListView handles filtering
    # Remove get_context_data method - ObjectListView handles context generation

# Keep Detail, Create, Update, Delete views as they are for now,
# unless further refactoring to ObjectDetailView etc. is desired later.

class AssetTypeDetailView(LoginRequiredMixin, DetailView):
    model = AssetType
    template_name = 'assets/assettypes/assettype_detail.html'
    slug_field = 'slug' # Use slug for lookup
    slug_url_kwarg = 'slug' # Match URL pattern kwarg
    context_object_name = 'object' # Use 'object' for consistency with base template

    def get_queryset(self):
        # Prefetch related manufacturer and tags for efficiency
        return super().get_queryset().select_related('manufacturer').prefetch_related('tags')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        asset_type = self.object # Get the current AssetType instance

        # --- Prepare Related Assets Table --- 
        related_assets = Asset.objects.filter(asset_type=asset_type).select_related(
            'asset_role', 'location' # Necessary select_related for the table columns
        )
        assets_table = AssetTable(related_assets, request=self.request)
        RequestConfig(self.request, paginate=False).configure(assets_table) # Disable pagination for related table
        context['assets_table'] = assets_table

        # --- Prepare Related Objects List (for right column card) ---
        related_objects_list = []
        asset_count = related_assets.count()
        if asset_count:
            asset_list_url = reverse('assets:asset_list')
            filtered_asset_url = f"{asset_list_url}?asset_type={asset_type.pk}" # Filter by asset_type pk
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': filtered_asset_url
            })
        context['related_objects_list'] = related_objects_list
        
        # --- Add other necessary context for base template ---
        context['object_type'] = self.model._meta.verbose_name.title()
        context['update_url_name'] = get_model_viewname(self.model, 'update')
        context['delete_url_name'] = get_model_viewname(self.model, 'delete')
        context['view_options'] = ['update', 'delete'] # Enable Edit/Delete buttons

        return context

class AssetTypeCreateView(CreateView):
    model = AssetType
    form_class = AssetTypeForm
    template_name = 'assets/assettypes/assettype_form.html' # Standardized path
    # success_url set in get_success_url

    def get_success_url(self):
        # Redirect to the detail view of the newly created object
        return reverse('assets:assettype_detail', kwargs={'slug': self.object.slug})

    def form_valid(self, form):
        messages.success(self.request, "Asset type created successfully.")
        return super().form_valid(form)

class AssetTypeUpdateView(UpdateView):
    model = AssetType
    form_class = AssetTypeForm
    template_name = 'assets/assettypes/assettype_form.html'
    slug_field = 'slug'
    slug_url_kwarg = 'slug'
    # success_url set in get_success_url

    def get_success_url(self):
        return reverse('assets:assettype_detail', kwargs={'slug': self.object.slug})

    def form_valid(self, form):
        messages.success(self.request, "Asset type updated successfully.")
        return super().form_valid(form)

class AssetTypeDeleteView(DeleteView):
    model = AssetType
    template_name = 'assets/assettypes/assettype_confirm_delete.html' # Standardized path
    slug_field = 'slug'
    slug_url_kwarg = 'slug'
    success_url = reverse_lazy('assets:assettype_list')

    def delete(self, request, *args, **kwargs):
        # TODO: Add protection check if assets are linked to this type
        asset_type_name = self.get_object().model # Get name before deletion
        messages.success(request, f"Asset type '{asset_type_name}' deleted successfully.")
        return super().delete(request, *args, **kwargs)

# Site, Region, SiteGroup views moved to organization/views.py
