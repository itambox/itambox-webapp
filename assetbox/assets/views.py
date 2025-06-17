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
from django.db.models import Count

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
#     return render(request, 'generic/object_list.html', context)

# --- Asset Views ---
class AssetListView(ObjectListView):
    queryset = Asset.objects.all().select_related(
        'asset_role', 
        'asset_type', 
        'asset_type__manufacturer',
        'location'
    ).prefetch_related('tags') # Add tags prefetch
    filterset = filters.AssetFilterSet
    filterset_form = forms.AssetFilterForm
    table = tables.AssetTable
    action_buttons = ('add',)

class AssetDetailView(ObjectDetailView):
    queryset = Asset.objects.select_related(
        'asset_role', 'location', 'asset_type', 'asset_type__manufacturer'
    ).prefetch_related(
        'logs__user', 'tags' # Prefetch user for logs and tags
    )
    # template_name = 'assets/assets/asset_detail.html' # Can be inferred

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        asset = self.get_object()

        # Fetch assignment separately
        assignment = AssetHolderAssignment.objects.filter(
            content_type=ContentType.objects.get_for_model(Asset),
            object_id=asset.pk
        ).select_related('asset_holder').first()

        # Add assignment and logs to context
        context['assignment'] = assignment
        context['logs'] = asset.logs.all() # Logs are prefetched
        # Base view handles title, object_type, etc.
        return context

class AssetEditView(ObjectEditView):
    queryset = Asset.objects.all()
    model = Asset
    model_form = AssetForm
    template_name = 'generic/object_edit.html'
    # Default success_url goes to object detail view

class AssetDeleteView(ObjectDeleteView):
    queryset = Asset.objects.all()
    model = Asset
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:asset_list')
    # No related objects check needed for Asset deletion itself (dependencies handled by other models)
    # Base view handles success message

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
    # template_name = 'assets/assetroles/assetrole_list.html' # Optional override

class AssetRoleDetailView(ObjectDetailView):
    queryset = AssetRole.objects.prefetch_related('tags', 'asset_set') # Use related_name 'asset_set'
    # template_name = 'assets/assetroles/assetrole_detail.html' # Can be inferred

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        assetrole = self.get_object()

        # Prepare Assets table (using related_name 'asset_set')
        assets_table = AssetTable(assetrole.asset_set.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assets_table)

        # Prepare Related Objects List
        related_objects_list = []
        asset_count = assetrole.asset_set.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?asset_role={assetrole.slug}" # Filter link
            })

        context['assets_table'] = assets_table
        context['related_objects_list'] = related_objects_list
        return context

class AssetRoleEditView(ObjectEditView):
    queryset = AssetRole.objects.all()
    model = AssetRole
    model_form = AssetRoleForm
    template_name = 'generic/object_edit.html'
    # Default success_url goes to object detail view

class AssetRoleDeleteView(ObjectDeleteView):
    queryset = AssetRole.objects.all()
    model = AssetRole
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:assetrole_list')

    def post(self, request, *args, **kwargs):
        assetrole = self.get_object()
        asset_count = assetrole.asset_set.count() # Use related_name

        if asset_count > 0:
            messages.error(
                request,
                f"Cannot delete asset role '{assetrole.name}': It is associated with {asset_count} asset{'s' if asset_count != 1 else ''}."
            )
            return redirect(assetrole.get_absolute_url())

        return super().post(request, *args, **kwargs)

# --- Manufacturer Views (Refactored to CBV) ---

class ManufacturerListView(ObjectListView):
    queryset = Manufacturer.objects.annotate(
        asset_count=Count('asset_types__assets') # Count assets through AssetType
    )
    filterset = filters.ManufacturerFilterSet
    filterset_form = forms.ManufacturerFilterForm
    table = tables.ManufacturerTable
    action_buttons = ('add',)

class ManufacturerDetailView(ObjectDetailView):
    queryset = Manufacturer.objects.prefetch_related(
        'asset_types', 'asset_types__assets' # Prefetch asset types and their assets
    )
    # template_name = 'assets/manufacturers/manufacturer_detail.html' # Can be inferred

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        manufacturer = self.get_object()

        # Prepare Asset Types table
        asset_types_table = AssetTypeTable(manufacturer.asset_types.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(asset_types_table)

        # Prepare Related Objects List (Assets count)
        related_objects_list = []
        # We need to count assets linked through this manufacturer's asset types
        asset_count = Asset.objects.filter(asset_type__manufacturer=manufacturer).count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?manufacturer={manufacturer.slug}" # Filter link
            })
        assettype_count = manufacturer.asset_types.count()
        if assettype_count:
            related_objects_list.append({
                'label': 'Asset Types',
                'count': assettype_count,
                'url': f"{reverse('assets:assettype_list')}?manufacturer={manufacturer.slug}" # Filter link
            })

        context['asset_types_table'] = asset_types_table
        context['related_objects_list'] = related_objects_list
        return context

class ManufacturerEditView(ObjectEditView):
    queryset = Manufacturer.objects.all()
    model = Manufacturer
    model_form = ManufacturerForm
    template_name = 'generic/object_edit.html'
    # Default success_url goes to object detail view

class ManufacturerDeleteView(ObjectDeleteView):
    queryset = Manufacturer.objects.all()
    model = Manufacturer
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:manufacturer_list')

    def post(self, request, *args, **kwargs):
        manufacturer = self.get_object()
        # Prevent deletion if linked to any AssetTypes
        asset_type_count = manufacturer.asset_types.count()

        if asset_type_count > 0:
            messages.error(
                request,
                f"Cannot delete manufacturer '{manufacturer.name}': It is associated with {asset_type_count} asset type{'s' if asset_type_count != 1 else ''}."
            )
            return redirect(manufacturer.get_absolute_url())

        return super().post(request, *args, **kwargs)

# --- Asset Type Views ---

class AssetTypeListView(ObjectListView): # Inherit from ObjectListView
    queryset = AssetType.objects.select_related('manufacturer').prefetch_related('tags') # Add tags
    filterset = filters.AssetTypeFilterSet # Keep filterset
    filterset_form = forms.AssetTypeFilterForm # Explicitly set the filter form
    table = tables.AssetTypeTable # Keep the table
    action_buttons = ('add',) # Define action buttons like others

class AssetTypeDetailView(ObjectDetailView): # Inherit from ObjectDetailView
    queryset = AssetType.objects.select_related('manufacturer').prefetch_related('tags', 'assets')
    slug_field = 'slug' # Still need this if lookup is by slug
    slug_url_kwarg = 'slug' # Still need this if lookup is by slug

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        assettype = self.get_object()

        # Prepare Assets table
        assets_table = AssetTable(assettype.assets.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assets_table)

        # Prepare Related Objects List
        related_objects_list = []
        asset_count = assettype.assets.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?asset_type={assettype.slug}" # Filter link
            })

        context['assets_table'] = assets_table
        context['related_objects_list'] = related_objects_list
        return context

class AssetTypeEditView(ObjectEditView): # Consolidate Create and Update
    queryset = AssetType.objects.all() # Base queryset for edit view
    model = AssetType
    model_form = AssetTypeForm
    template_name = 'generic/object_edit.html' # Use generic template
    # Need to handle slug lookup for the update case
    slug_field = 'slug'
    slug_url_kwarg = 'slug'

    # Success URL and messages are handled by the base ObjectEditView

class AssetTypeDeleteView(ObjectDeleteView): # Inherit from ObjectDeleteView
    queryset = AssetType.objects.all()
    model = AssetType
    template_name = 'generic/object_confirm_delete.html' # Use generic template
    success_url = reverse_lazy('assets:assettype_list')
    slug_field = 'slug' # Still need this if lookup is by slug
    slug_url_kwarg = 'slug' # Still need this if lookup is by slug

    def post(self, request, *args, **kwargs):
        assettype = self.get_object()
        asset_count = assettype.assets.count()

        if asset_count > 0:
            messages.error(
                request,
                f"Cannot delete asset type '{assettype}': It is associated with {asset_count} asset{'s' if asset_count != 1 else ''}."
            )
            return redirect(assettype.get_absolute_url())

        return super().post(request, *args, **kwargs)

# Site, Region, SiteGroup views moved to organization/views.py
