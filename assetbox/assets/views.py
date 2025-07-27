from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.utils import timezone
from .models import Asset, AssetRole, Manufacturer, AssetType, InstalledSoftware, ComponentType, ComponentInstance, Accessory, AccessoryAssignment, Consumable, ConsumableAssignment, StatusLabel, AssetMaintenance, CustomField, CustomFieldset, Depreciation, Kit, KitItem, ActivityLog, Supplier, Category, AssetRequest, AssetTagSequence, CustodyReceipt
from licenses.models import License, LicenseSeatAssignment
from .forms import AssetForm, AssetRoleForm, ManufacturerForm, AssetCheckOutForm, AssetTypeForm # Keep only Asset forms
from django.contrib.auth import get_user_model
from django_tables2 import RequestConfig
from .tables import AssetTable, AssetRoleTable, ManufacturerTable, AssetTypeTable
from software.tables import InstalledSoftwareTable
from .filters import AssetFilterSet, AssetRoleFilterSet, ManufacturerFilterSet, AssetTypeFilterSet 
# --- Add imports needed for CBVs --- 
from . import filters
from . import forms
from . import tables
# --- End imports ---
from core.utils import get_paginate_count, get_model_viewname
from core.panels import Panel
from django.http import HttpResponse, HttpResponseBadRequest
from django.contrib.contenttypes.models import ContentType
from organization.models import AssetHolderAssignment, AssetHolder, Location
from django.urls import reverse, reverse_lazy
from django.db.models import Q # <-- Import Q (needed by search method in filterset)
from django.contrib import messages # <--- Add this import
from users.models import UserPreference # Import UserPreference
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from core.views import ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectImportView, BaseHTMXView
from core.quick_add import QuickAddMixin
from django.db.models import Count
import json
from django.utils import timezone
from django.template.loader import render_to_string # Added for partial rendering
from django.views import View # Import base View
from core.constants import ( # Import HTMX constants
    RESULTS_PANE_CONTENT_ID,
    RESULTS_TAB_COUNT_ID,
    FILTERS_APPLIED_COUNT_ID,
    DEFAULT_PAGINATE_COUNT # Also import pagination default if needed
)

User = get_user_model()

# Create your views here.

class DashboardView(LoginRequiredMixin, BaseHTMXView, TemplateView):
    template_name = 'dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Dashboard'
        context['breadcrumbs'] = [(None, 'Dashboard')]

        from extras.dashboard.utils import get_dashboard
        dashboard = get_dashboard(self.request.user)

        # Build list of visible widget configs with rendered content
        widget_list = []
        for idx, config in enumerate(dashboard.layout):
            if not config.get('visible', True):
                continue
            widget_list.append({'index': idx, 'config': config})

        context['dashboard_widgets'] = widget_list
        return context

# --- Asset Views ---
class AssetListView(ObjectListView):
    queryset = Asset.objects.select_related(
        'asset_role',
        'asset_type',
        'asset_type__manufacturer',
        'location',
        'tenant',
        'status',
    ).prefetch_related('tags')

    def get_table(self):
        table = super().get_table()
        # Patch objects with _assignee_display AFTER table is built (1 query)
        if hasattr(table, 'data') and table.data is not None:
            try:
                pks = [obj.pk for obj in table.data[:500]]
            except Exception:
                pks = []
            if pks:
                ct = ContentType.objects.get_for_model(Asset)
                assignments = AssetHolderAssignment.objects.filter(
                    content_type=ct, object_id__in=pks
                ).select_related('asset_holder')
                assignee_map = {
                    a.object_id: a.asset_holder for a in assignments if a.asset_holder
                }
                from django.urls import reverse
                from django.utils.safestring import mark_safe
                for obj in table.data:
                    holder = assignee_map.get(obj.pk)
                    if holder:
                        try:
                            url = reverse('organization:assetholder_detail', kwargs={'pk': holder.pk})
                            obj._assignee_display = mark_safe(f'<a href="{url}">{holder}</a>')
                        except Exception:
                            obj._assignee_display = str(holder)
                    elif obj.location:
                        obj._assignee_display = f"Location: {obj.location}"
                    else:
                        obj._assignee_display = "—"
        return table
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['asset_holders'] = AssetHolder.objects.all().order_by('last_name', 'first_name')
        return context
    filterset_form = forms.AssetFilterForm
    table = tables.AssetTable
    action_buttons = ('add',)

class AssetDetailView(ObjectDetailView):
    queryset = Asset.objects.select_related(
        'asset_role', 'location', 'asset_type', 'asset_type__manufacturer'
    ).prefetch_related(
        'tags', 'maintenances'
    )
    # template_name = 'assets/assets/asset_detail.html' # Can be inferred

    layout = (
        ((Panel('metrics', 'Asset Overview'),),),
        (
            (Panel('asset_info', 'Asset Details'), Panel('specs', 'Hardware Specifications'), Panel('custom_fields', 'Custom Fields')),
            (Panel('assignment', 'Deployment & Custody'), Panel('financial', 'Financial & Lifecycle'), Panel('audit', 'Audit & Compliance'), Panel('support', 'Support & Warranty Details')),
        ),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        asset = self.get_object()

        # Fetch assignment separately
        assignment = AssetHolderAssignment.objects.filter(
            content_type=ContentType.objects.get_for_model(Asset),
            object_id=asset.pk
        ).select_related('asset_holder').first()

        # Add assignment to context
        context['assignment'] = assignment

        # Fetch installed software and build software_table
        sw_qs = InstalledSoftware.objects.filter(asset=asset).select_related('software', 'software__manufacturer')
        sw_table = InstalledSoftwareTable(sw_qs)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(sw_table)
        context['software_table'] = sw_table

        # Fetch installed components and build components_table
        comp_qs = asset.components.select_related('component_type', 'component_type__manufacturer')
        comp_table = tables.ComponentInstanceTable(comp_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(comp_table)
        context['components_table'] = comp_table

        # Fetch maintenance records and build maintenances_table
        maint_qs = asset.maintenances.all()
        maint_table = tables.AssetMaintenanceTable(maint_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': 10}).configure(maint_table)
        context['maintenances_table'] = maint_table

        # Add EOL and TCO context
        context['eol_date'] = asset.eol_date
        context['time_to_eol'] = asset.time_to_eol
        context['total_cost_of_ownership'] = asset.total_cost_of_ownership

        # Fetch custody receipt and generate EULA token if checked out to a holder
        custody_receipt = None
        eula_token = None
        if assignment and assignment.asset_holder:
            custody_receipt = CustodyReceipt.objects.filter(asset=asset, holder=assignment.asset_holder).first()
            if custody_receipt:
                eula_token = custody_receipt.token
        
        context['custody_receipt'] = custody_receipt
        context['eula_token'] = eula_token

        # Base view handles title, object_type, etc.
        return context

class AssetEditView(ObjectEditView):
    queryset = Asset.objects.all()
    model = Asset
    model_form = AssetForm
    template_name = 'generic/object_edit.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['request'] = self.request
        return kwargs

    def post(self, request, *args, **kwargs):
        if request.headers.get('HX-Request') and '_reload' in request.POST:
            self.object = self.get_object()
            form = self.get_form()
            return render(request, 'htmx/crispy_form.html', {'form': form})
        return super().post(request, *args, **kwargs)
    # Default success_url goes to object detail view

class AssetDeleteView(ObjectDeleteView):
    queryset = Asset.objects.all()
    model = Asset
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:asset_list')

class AssetCloneView(ObjectEditView):
    model = Asset
    model_form = forms.AssetForm
    template_name = 'generic/object_edit.html'

    def get_object(self, queryset=None):
        original = get_object_or_404(Asset, pk=self.kwargs['pk'])
        cloned = original.clone()
        cloned.asset_tag = ''
        cloned.name = f'{original.name} (Copy)'
        cloned.save()
        cloned.tags.set(original.tags.all())
        return cloned

@login_required
def asset_checkout_modal(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    
    # Ensure asset is available before proceeding
    if not asset.status or asset.status.slug != 'available':
        # Returning a simple message inside the modal might be better UX
        # For now, returning a basic forbidden response
        return HttpResponse("Asset is not available for assignment.", status=403)

    if request.method == 'POST':
        form = AssetCheckOutForm(request.POST)
        if form.is_valid():
            from django.db import transaction
            with transaction.atomic():
                selected_holder = form.cleaned_data.get('asset_holder')
                selected_location = form.cleaned_data.get('location')
                
                # --- Checkout Logic --- 
                assignee = None
                if selected_holder:
                    assignee = selected_holder
                    asset.location = None # Clear location if assigned to holder
                elif selected_location:
                    assignee = selected_location
                    asset.location = selected_location # Set location

                # Update asset status
                in_use_status = StatusLabel.objects.filter(slug='in-use').first()
                if in_use_status:
                    asset.status = in_use_status
                asset._changelog_action = 'checkout'
                asset._changelog_message = f"Checked out to {'Holder' if selected_holder else 'Location'}: {assignee}"
                asset.save(update_fields=['status', 'location'])

                # Create/update assignment record
                if selected_holder:
                    AssetHolderAssignment.objects.update_or_create(
                        content_type=ContentType.objects.get_for_model(Asset),
                        object_id=asset.pk,
                        defaults={
                            'asset_holder': selected_holder,
                        }
                    )
                else:
                    # If assigned to location, clear any existing holder assignment
                    AssetHolderAssignment.objects.filter(
                        content_type=ContentType.objects.get_for_model(Asset),
                        object_id=asset.pk
                    ).delete()

                messages.success(request, f"Asset '{asset}' checked out successfully to {assignee}.")

                if selected_holder:
                    category = asset.asset_type.category if asset.asset_type else None
                    if category and category.require_acceptance:
                        receipt = CustodyReceipt.objects.create(
                            asset=asset,
                            holder=selected_holder,
                        )
                        if category.email_eula:
                            try:
                                from django.core.mail import send_mail
                                from core.models import EmailSettings
                                email_config = EmailSettings.load()
                                if email_config and email_config.enabled and email_config.from_address:
                                    recipient = selected_holder.email
                                    if not recipient:
                                        recipient = email_config.test_recipient or email_config.from_address
                                    if recipient:
                                        sign_url = request.build_absolute_uri(
                                            reverse('assets:custody_eula_sign', kwargs={'token': receipt.token})
                                        )
                                        send_mail(
                                            subject=f'Asset Acceptance Required: {asset.name} ({asset.asset_tag})',
                                            message=(
                                                f'You have been assigned custody of:\n\n'
                                                f'  Asset: {asset.name}\n'
                                                f'  Asset Tag: {asset.asset_tag}\n'
                                                f'  Serial: {asset.serial_number or "N/A"}\n\n'
                                                f'Please accept custody at the following link:\n{sign_url}\n\n'
                                                f'This link expires in 7 days.'
                                            ),
                                            from_email=email_config.from_address,
                                            recipient_list=[recipient],
                                            fail_silently=True,
                                        )
                            except Exception:
                                pass
                # --- End Checkout Logic ---

                # --- HTMX Response for Success --- 
                import json
                response = HttpResponse(status=204) # No Content
                # Trigger events for JS to close modal and potentially refresh list
                response['HX-Trigger'] = json.dumps({
                    "closeModalEvent": None, 
                    "assetListUpdated": None, # Trigger list refresh
                    "showMessage": {"message": f"Asset '{asset}' checked out to {assignee}.", "level": "success"} # Send message via trigger
                }) 
                return response
            # --- End HTMX Response --- 
        else:
            # --- HTMX Response for Validation Error ---
            # Re-render only the partial form body inside the modal
            context = {'form': form, 'asset': asset, 'request': request}
            return render(request, "assets/includes/asset_checkout_modal.html#checkout-modal-form", context)
            # --- End HTMX Response ---


    else: # GET request
        form = AssetCheckOutForm()

    # Initial GET still renders the whole modal template via the placeholder swap
    context = {'form': form, 'asset': asset}
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
        from django.db import transaction
        with transaction.atomic():
            # Check in from Asset Holder
            checked_in_from = assignment.asset_holder
            from_str = str(checked_in_from) if checked_in_from else 'N/A'
            
            assignment.delete()
            available_status = StatusLabel.objects.filter(slug='available').first()
            if available_status:
                asset.status = available_status
            asset._changelog_action = 'checkin'
            asset._changelog_message = f"Checked in from Asset Holder: {from_str}"
            asset.save()
        messages.success(request, f"Asset '{asset}' successfully checked in from Asset Holder: {from_str}.")
    elif asset.location:
        from django.db import transaction
        with transaction.atomic():
            # Check in (clear) from Location
            checked_in_from = asset.location
            from_str = str(checked_in_from) if checked_in_from else 'N/A'
            
            asset.location = None
            available_status = StatusLabel.objects.filter(slug='available').first()
            if available_status:
                asset.status = available_status
            asset._changelog_action = 'checkin'
            asset._changelog_message = f"Checked in from Location: {from_str}"
            asset.save()
        messages.success(request, f"Asset '{asset}' successfully checked in from Location: {from_str}.")
    else:
        # Asset was not assigned to a holder or location
        messages.warning(request, f"Asset '{asset}' was not checked out to a holder or assigned to a location.")
        
    return redirect('assets:asset_detail', pk=asset.pk)

# --- AssetRole (Asset Role) Views (Refactored to CBV) ---

class AssetRoleListView(ObjectListView):
    queryset = AssetRole.objects.annotate(asset_count=Count('asset'))
    filterset = filters.AssetRoleFilterSet
    filterset_form = forms.AssetRoleFilterForm # Corrected: Point to AssetRoleFilterForm
    table = tables.AssetRoleTable
    action_buttons = ('add',) # Add action_buttons
    # template_name = 'assets/assetroles/assetrole_list.html' # Optional override

class AssetRoleDetailView(ObjectDetailView):
    queryset = AssetRole.objects.prefetch_related('tags', 'asset_set') # Use related_name 'asset_set'
    # template_name = 'assets/assetroles/assetrole_detail.html' # Can be inferred

    layout = (
        ((Panel('info', 'Asset Role Details'),),),
    )

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

class AssetRoleEditView(QuickAddMixin, ObjectEditView):
    queryset = AssetRole.objects.all()
    model = AssetRole
    model_form = AssetRoleForm
    template_name = 'generic/object_edit.html'
    quick_add_target = 'id_asset_role'
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

# --- StatusLabel Views ---

class StatusLabelListView(ObjectListView):
    queryset = StatusLabel.objects.annotate(asset_count=Count('assets'))
    filterset = filters.StatusLabelFilterSet
    filterset_form = forms.StatusLabelFilterForm
    table = tables.StatusLabelTable
    action_buttons = ('add',)

class StatusLabelDetailView(ObjectDetailView):
    queryset = StatusLabel.objects.prefetch_related('assets')

    layout = (
        ((Panel('metrics', 'Status Label Overview'),),),
        ((Panel('info', 'Status Label Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        statuslabel = self.get_object()

        # Prepare Assets table
        assets_qs = statuslabel.assets.select_related('asset_role', 'asset_type', 'location')
        assets_table = tables.AssetTable(assets_qs, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assets_table)

        # Prepare Related Objects List
        related_objects_list = []
        asset_count = assets_qs.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?status={statuslabel.slug}" # Filter link
            })

        context['assets_table'] = assets_table
        context['related_objects_list'] = related_objects_list
        return context

class StatusLabelEditView(ObjectEditView):
    queryset = StatusLabel.objects.all()
    model = StatusLabel
    model_form = forms.StatusLabelForm
    template_name = 'generic/object_edit.html'

class StatusLabelDeleteView(ObjectDeleteView):
    queryset = StatusLabel.objects.all()
    model = StatusLabel
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:statuslabel_list')

    def post(self, request, *args, **kwargs):
        statuslabel = self.get_object()
        asset_count = statuslabel.assets.count()

        if asset_count > 0:
            messages.error(
                request,
                f"Cannot delete status label '{statuslabel.name}': It is associated with {asset_count} asset{'s' if asset_count != 1 else ''}."
            )
            return redirect(statuslabel.get_absolute_url())

        return super().post(request, *args, **kwargs)

# --- Manufacturer Views (Refactored to CBV) ---

class ManufacturerListView(ObjectListView):
    queryset = Manufacturer.objects.annotate(
        asset_count=Count('asset_types__assets'), # Count assets through AssetType
        asset_type_count=Count('asset_types'), # Count asset types
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

    layout = (
        ((Panel('metrics', 'Manufacturer Overview'),),),
        ((Panel('info', 'Manufacturer Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        manufacturer = self.get_object()

        # Prepare Asset Types table
        asset_types_table = AssetTypeTable(manufacturer.asset_types.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(asset_types_table)

        # Prepare Assets table
        manufacturer_assets = Asset.objects.filter(asset_type__manufacturer=manufacturer).select_related(
            'asset_role', 'asset_type', 'location'
        )
        assets_table = AssetTable(manufacturer_assets, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assets_table)

        # Prepare Related Objects List (Assets count)
        related_objects_list = []
        asset_count = manufacturer_assets.count()
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
        context['assets_table'] = assets_table
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

    layout = (
        ((Panel('metrics', 'Asset Type Overview'),),),
        ((Panel('info', 'Asset Type Details'), Panel('specs', 'Hardware Specifications')),),
    )

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

class AssetTypeEditView(QuickAddMixin, ObjectEditView): # Consolidate Create and Update
    queryset = AssetType.objects.all() # Base queryset for edit view
    model = AssetType
    model_form = AssetTypeForm
    template_name = 'generic/object_edit.html' # Use generic template
    quick_add_target = 'id_asset_type'
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

# Component Type Views
class ComponentTypeListView(ObjectListView):
    queryset = ComponentType.objects.select_related('manufacturer').prefetch_related('tags')
    filterset = filters.ComponentTypeFilterSet
    filterset_form = forms.ComponentTypeFilterForm
    table = tables.ComponentTypeTable
    action_buttons = ('add',)

class ComponentTypeDetailView(ObjectDetailView):
    queryset = ComponentType.objects.select_related('manufacturer').prefetch_related('tags', 'instances')
    template_name = 'assets/componenttypes/componenttype_detail.html'

    layout = (
        ((Panel('metrics', 'Component Type Overview'),),),
        ((Panel('info', 'Component Type Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        componenttype = self.get_object()

        # Prepare instances table
        instances_table = tables.ComponentInstanceTable(componenttype.instances.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(instances_table)
        context['instances_table'] = instances_table

        return context

class ComponentTypeEditView(ObjectEditView):
    queryset = ComponentType.objects.all()
    model = ComponentType
    model_form = forms.ComponentTypeForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:componenttype_list'

class ComponentTypeDeleteView(ObjectDeleteView):
    queryset = ComponentType.objects.all()
    model = ComponentType
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:componenttype_list')

    def post(self, request, *args, **kwargs):
        comp_type = self.get_object()
        instance_count = comp_type.instances.count()
        if instance_count > 0:
            messages.error(
                request,
                f"Cannot delete component type '{comp_type}': It has {instance_count} active physical parts."
            )
            return redirect(comp_type.get_absolute_url())
        return super().post(request, *args, **kwargs)


# Component Instance Views
class ComponentInstanceListView(ObjectListView):
    queryset = ComponentInstance.objects.select_related('component_type', 'component_type__manufacturer', 'parent_asset').prefetch_related('tags')
    filterset = filters.ComponentInstanceFilterSet
    filterset_form = forms.ComponentInstanceFilterForm
    table = tables.ComponentInstanceTable
    action_buttons = ('add',)

class ComponentInstanceDetailView(ObjectDetailView):
    queryset = ComponentInstance.objects.select_related('component_type', 'component_type__manufacturer', 'parent_asset').prefetch_related('tags')
    template_name = 'assets/componentinstances/componentinstance_detail.html'

    layout = (
        ((Panel('metrics', 'Component Status'),),),
        ((Panel('info', 'Component Details'),),),
    )

class ComponentInstanceEditView(ObjectEditView):
    queryset = ComponentInstance.objects.all()
    model = ComponentInstance
    model_form = forms.ComponentInstanceForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:componentinstance_list'

class ComponentInstanceDeleteView(ObjectDeleteView):
    queryset = ComponentInstance.objects.all()
    model = ComponentInstance
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:componentinstance_list')


# Accessory Views
class AccessoryListView(ObjectListView):
    queryset = Accessory.objects.select_related('manufacturer').prefetch_related('tags')
    filterset = filters.AccessoryFilterSet
    filterset_form = forms.AccessoryFilterForm
    table = tables.AccessoryTable
    action_buttons = ('add',)


class AccessoryDetailView(ObjectDetailView):
    queryset = Accessory.objects.select_related('manufacturer').prefetch_related('tags', 'assignments__assigned_holder', 'assignments__assigned_location')
    template_name = 'assets/accessories/accessory_detail.html'

    layout = (
        ((Panel('metrics', 'Inventory Overview'),),),
        ((Panel('info', 'Accessory Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        accessory = self.get_object()

        # Prepare assignments table
        assignments_table = tables.AccessoryAssignmentTable(accessory.assignments.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assignments_table)
        context['assignments_table'] = assignments_table
        return context


class AccessoryEditView(ObjectEditView):
    queryset = Accessory.objects.all()
    model = Accessory
    model_form = forms.AccessoryForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:accessory_list'


class AccessoryDeleteView(ObjectDeleteView):
    queryset = Accessory.objects.all()
    model = Accessory
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:accessory_list')

    def post(self, request, *args, **kwargs):
        accessory = self.get_object()
        assignment_count = accessory.assignments.count()
        if assignment_count > 0:
            messages.error(
                request,
                f"Cannot delete accessory '{accessory}': It has {assignment_count} active assignments."
            )
            return redirect(accessory.get_absolute_url())
        return super().post(request, *args, **kwargs)


class AccessoryCloneView(ObjectEditView):
    model = Accessory
    model_form = forms.AccessoryForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:accessory_list'

    def get_object(self, queryset=None):
        original = get_object_or_404(Accessory, pk=self.kwargs['pk'])
        cloned = original.clone()
        cloned.name = f'{original.name} (Copy)'
        cloned.slug = ''
        cloned.save()
        cloned.tags.set(original.tags.all())
        return cloned


@login_required
def accessory_checkout(request, pk):
    accessory = get_object_or_404(Accessory, pk=pk)
    
    if not accessory.allow_overallocate and accessory.remaining_qty <= 0:
        return HttpResponse("No stock available for checkout.", status=403)

    if request.method == 'POST':
        form = forms.AccessoryCheckoutForm(request.POST, accessory=accessory)
        if form.is_valid():
            holder = form.cleaned_data.get('assigned_holder')
            location = form.cleaned_data.get('assigned_location')
            qty = form.cleaned_data.get('qty')
            notes = form.cleaned_data.get('notes')
            
            AccessoryAssignment.objects.create(
                accessory=accessory,
                assigned_holder=holder,
                assigned_location=location,
                qty=qty,
                notes=notes
            )
            
            recipient = holder or location
            messages.success(request, f"Checked out {qty}x '{accessory}' successfully to {recipient}.")
            
            response = HttpResponse(status=204)
            response['HX-Trigger'] = json.dumps({
                "closeModalEvent": None,
                "assetListUpdated": None,
                "showMessage": {"message": f"Checked out {qty}x '{accessory}' successfully to {recipient}.", "level": "success"}
            })
            return response
        else:
            context = {'form': form, 'accessory': accessory, 'request': request}
            return render(request, "assets/includes/accessory_checkout_modal.html#checkout-modal-form", context)
    else:
        form = forms.AccessoryCheckoutForm(accessory=accessory)

    context = {'form': form, 'accessory': accessory}
    return render(request, 'assets/includes/accessory_checkout_modal.html', context)


@login_required
@require_POST
def accessory_checkin(request, pk):
    assignment = get_object_or_404(AccessoryAssignment, pk=pk)
    accessory = assignment.accessory
    qty = assignment.qty
    recipient = assignment.assigned_holder or assignment.assigned_location
    
    assignment.delete()
    messages.success(request, f"Checked in {qty}x '{accessory}' from {recipient}.")
    return redirect(accessory.get_absolute_url())


# Consumable Views
class ConsumableListView(ObjectListView):
    queryset = Consumable.objects.select_related('manufacturer').prefetch_related('tags')
    filterset = filters.ConsumableFilterSet
    filterset_form = forms.ConsumableFilterForm
    table = tables.ConsumableTable
    action_buttons = ('add',)


class ConsumableDetailView(ObjectDetailView):
    queryset = Consumable.objects.select_related('manufacturer').prefetch_related('tags', 'consumptions__assigned_holder', 'consumptions__assigned_location')
    template_name = 'assets/consumables/consumable_detail.html'

    layout = (
        ((Panel('metrics', 'Inventory Overview'),),),
        ((Panel('info', 'Consumable Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        consumable = self.get_object()

        # Prepare consumptions table
        consumptions_table = tables.ConsumableAssignmentTable(consumable.consumptions.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(consumptions_table)
        context['consumptions_table'] = consumptions_table
        return context


class ConsumableEditView(ObjectEditView):
    queryset = Consumable.objects.all()
    model = Consumable
    model_form = forms.ConsumableForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:consumable_list'


class ConsumableDeleteView(ObjectDeleteView):
    queryset = Consumable.objects.all()
    model = Consumable
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:consumable_list')

    def post(self, request, *args, **kwargs):
        consumable = self.get_object()
        consumption_count = consumable.consumptions.count()
        if consumption_count > 0:
            messages.error(
                request,
                f"Cannot delete consumable '{consumable}': It has {consumption_count} historical consumption records."
            )
            return redirect(consumable.get_absolute_url())
        return super().post(request, *args, **kwargs)


class ConsumableCloneView(ObjectEditView):
    model = Consumable
    model_form = forms.ConsumableForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:consumable_list'

    def get_object(self, queryset=None):
        original = get_object_or_404(Consumable, pk=self.kwargs['pk'])
        cloned = original.clone()
        cloned.name = f'{original.name} (Copy)'
        cloned.slug = ''
        cloned.save()
        cloned.tags.set(original.tags.all())
        return cloned


@login_required
def consumable_checkout(request, pk):
    consumable = get_object_or_404(Consumable, pk=pk)
    
    if not consumable.allow_overallocate and consumable.remaining_qty <= 0:
        return HttpResponse("No stock available for consumption checkout.", status=403)

    if request.method == 'POST':
        form = forms.ConsumableCheckoutForm(request.POST, consumable=consumable)
        if form.is_valid():
            holder = form.cleaned_data.get('assigned_holder')
            location = form.cleaned_data.get('assigned_location')
            qty = form.cleaned_data.get('qty')
            notes = form.cleaned_data.get('notes')
            
            ConsumableAssignment.objects.create(
                consumable=consumable,
                assigned_holder=holder,
                assigned_location=location,
                qty=qty,
                notes=notes
            )
            
            recipient = holder or location
            messages.success(request, f"Checked out / Consumed {qty}x '{consumable}' for {recipient}.")
            
            response = HttpResponse(status=204)
            response['HX-Trigger'] = json.dumps({
                "closeModalEvent": None,
                "assetListUpdated": None,
                "showMessage": {"message": f"Consumed {qty}x '{consumable}' for {recipient}.", "level": "success"}
            })
            return response
        else:
            context = {'form': form, 'consumable': consumable, 'request': request}
            return render(request, "assets/includes/consumable_checkout_modal.html#checkout-modal-form", context)
    else:
        form = forms.ConsumableCheckoutForm(consumable=consumable)

    context = {'form': form, 'consumable': consumable}
    return render(request, 'assets/includes/consumable_checkout_modal.html', context)


# Asset Maintenance Views
class AssetMaintenanceListView(ObjectListView):
    queryset = AssetMaintenance.objects.select_related('asset')
    filterset = filters.AssetMaintenanceFilterSet
    filterset_form = forms.AssetMaintenanceFilterForm
    table = tables.AssetMaintenanceTable
    action_buttons = ('add',)


class AssetMaintenanceDetailView(ObjectDetailView):
    queryset = AssetMaintenance.objects.select_related('asset')
    template_name = 'assets/assetmaintenances/assetmaintenance_detail.html'

    layout = (
        ((Panel('metrics', 'Maintenance Overview'),),),
        ((Panel('info', 'Maintenance Details'),),),
    )


class AssetMaintenanceEditView(ObjectEditView):
    queryset = AssetMaintenance.objects.all()
    model = AssetMaintenance
    model_form = forms.AssetMaintenanceForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:assetmaintenance_list'

    def get_initial(self):
        initial = super().get_initial()
        # Prepopulate asset if passed in GET params
        asset_id = self.request.GET.get('asset')
        if asset_id:
            initial['asset'] = asset_id
        return initial


class AssetMaintenanceDeleteView(ObjectDeleteView):
    queryset = AssetMaintenance.objects.all()
    model = AssetMaintenance
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:assetmaintenance_list')


# --- Phase 4 views ---
import segno
import hashlib
from django.views.decorators.clickjacking import xframe_options_exempt

@login_required
@require_POST
def asset_audit(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    asset.last_audited = timezone.now()
    asset.last_audited_by = request.user
    asset._changelog_action = 'audit'
    asset._changelog_message = f"Physical presence verified by {request.user.get_full_name() or request.user.username}."
    asset.save(update_fields=['last_audited', 'last_audited_by'])
    ActivityLog.objects.create(
        asset=asset,
        action='audited',
        user=request.user,
        notes=asset._changelog_message
    )
    response = render(request, "assets/includes/asset_audit_badge.html", {'asset': asset})
    response['HX-Trigger'] = json.dumps({
        "playAuditSound": None,
        "showMessage": {"message": f"Asset '{asset.name}' physically audited successfully!", "level": "success"}
    })
    return response


@login_required
def asset_label_print(request, pk, template_id=None):
    asset = get_object_or_404(Asset, pk=pk)

    if template_id:
        from core.models import LabelTemplate
        label_template = get_object_or_404(LabelTemplate, pk=template_id)
        if label_template.template_code:
            from django.template import Template, Context
            tpl = Template(label_template.template_code)
            ctx = Context({'obj': asset, 'barcode_format': label_template.barcode_format})
            html = tpl.render(ctx)
            return HttpResponse(html)

    qr_data = request.build_absolute_uri(asset.get_absolute_url())
    qr = segno.make(qr_data)
    qr_svg = qr.svg_inline(scale=4, border=0)

    context = {
        'asset': asset,
        'qr_svg': qr_svg,
    }
    return render(request, "assets/assets/asset_label.html", context)


def custody_eula_sign(request, token):
    receipt = get_object_or_404(CustodyReceipt, token=token)

    if receipt.created_date and (timezone.now() - receipt.created_date).days > 7:
        return render(request, "assets/custody/sign_error.html", {"error": "This custody acceptance link has expired (7 day limit)."})

    if receipt.acceptance_status == CustodyReceipt.STATUS_ACCEPTED:
        return render(request, "assets/custody/receipt_success.html", {"receipt": receipt, "asset": receipt.asset, "holder": receipt.holder})

    if receipt.acceptance_status == CustodyReceipt.STATUS_DECLINED:
        return render(request, "assets/custody/sign_error.html", {"error": "This custody transfer has been declined."})

    asset = receipt.asset
    holder = receipt.holder

    if request.method == 'POST':
        action = request.POST.get('action', 'accept')
        signature_data = request.POST.get('signature_canvas')

        if action == 'decline':
            receipt.acceptance_status = CustodyReceipt.STATUS_DECLINED
            receipt.save(update_fields=['acceptance_status', 'updated_at'])
            return render(request, "assets/custody/sign_error.html", {"error": "You have declined the custody transfer."})

        if not signature_data or signature_data == 'empty':
            return render(request, "assets/custody/sign_portal.html", {
                "asset": asset,
                "holder": holder,
                "token": token,
                "receipt": receipt,
                "error": "Please provide a valid signature."
            })

        timestamp_str = timezone.now().isoformat()
        raw_to_hash = f"{holder.upn}|{asset.asset_tag}|{timestamp_str}|{signature_data}"
        verification_hash = hashlib.sha256(raw_to_hash.encode('utf-8')).hexdigest()

        receipt.accepted = True
        receipt.accepted_date = timezone.now()
        receipt.acceptance_method = 'digital'
        receipt.acceptance_status = CustodyReceipt.STATUS_ACCEPTED
        receipt.signature_canvas = signature_data
        receipt.signature_data = signature_data
        receipt.signature_hash = verification_hash
        receipt.verification_hash = verification_hash
        receipt.eula_version = '1.0'
        receipt.signed_at = timezone.now()
        receipt.save()

        try:
            from django.db import transaction, DatabaseError
            from core.events import dispatch_event
            transaction.on_commit(lambda: _safe_dispatch_custody(receipt))
        except Exception:
            _safe_dispatch_custody(receipt)

        asset._changelog_action = 'audit'
        asset._changelog_message = f"EULA digital custody receipt accepted. SHA-256 Hash: {verification_hash[:16]}..."
        asset.save()

        return render(request, "assets/custody/receipt_success.html", {"receipt": receipt, "asset": asset, "holder": holder})

    return render(request, "assets/custody/sign_portal.html", {"asset": asset, "holder": holder, "token": token, "receipt": receipt})


def _safe_dispatch_custody(receipt):
    try:
        dispatch_event(CustodyReceipt, receipt, action='update')
    except Exception:
        pass


# =============================================================================
# Custom Fields, Fieldsets, Depreciation & Onboarding Kits Views
# =============================================================================

# Custom Fields
class CustomFieldListView(ObjectListView):
    queryset = CustomField.objects.all()
    filterset = filters.CustomFieldFilterSet
    filterset_form = forms.CustomFieldFilterForm
    table = tables.CustomFieldTable
    action_buttons = ('add',)


class CustomFieldDetailView(ObjectDetailView):
    queryset = CustomField.objects.all()

    layout = (
        ((Panel('info', 'Custom Field Details'),),),
    )


class CustomFieldEditView(ObjectEditView):
    queryset = CustomField.objects.all()
    model = CustomField
    model_form = forms.CustomFieldForm
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
    filterset = filters.CustomFieldsetFilterSet
    filterset_form = forms.CustomFieldsetFilterForm
    table = tables.CustomFieldsetTable
    action_buttons = ('add',)


class CustomFieldsetDetailView(ObjectDetailView):
    queryset = CustomFieldset.objects.all().prefetch_related('fields', 'asset_types')

    layout = (
        ((Panel('info', 'Custom Field Set Details'),),),
    )


class CustomFieldsetEditView(ObjectEditView):
    queryset = CustomFieldset.objects.all()
    model = CustomFieldset
    model_form = forms.CustomFieldsetForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:customfieldset_list'


class CustomFieldsetDeleteView(ObjectDeleteView):
    queryset = CustomFieldset.objects.all()
    model = CustomFieldset
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:customfieldset_list')


# Depreciation
class DepreciationListView(ObjectListView):
    queryset = Depreciation.objects.all()
    filterset = filters.DepreciationFilterSet
    filterset_form = forms.DepreciationFilterForm
    table = tables.DepreciationTable
    action_buttons = ('add',)


class DepreciationDetailView(ObjectDetailView):
    queryset = Depreciation.objects.all().prefetch_related('asset_types')

    layout = (
        ((Panel('info', 'Depreciation Rule Details'),),),
    )


class DepreciationEditView(ObjectEditView):
    queryset = Depreciation.objects.all()
    model = Depreciation
    model_form = forms.DepreciationForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:depreciation_list'


class DepreciationDeleteView(ObjectDeleteView):
    queryset = Depreciation.objects.all()
    model = Depreciation
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:depreciation_list')


# Kits & KitItems
class KitListView(ObjectListView):
    queryset = Kit.objects.all().annotate(item_count=Count('items'))
    filterset = filters.KitFilterSet
    filterset_form = forms.KitFilterForm
    table = tables.KitTable
    action_buttons = ('add',)


class KitDetailView(ObjectDetailView):
    queryset = Kit.objects.all().prefetch_related('items__asset_type', 'items__accessory', 'items__license__software')
    template_name = 'assets/kits/kit_detail.html'

    layout = (
        ((Panel('info', 'Kit Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Check availability of each kit item
        items_with_availability = []
        all_available = True
        
        for item in self.object.items.all():
            avail = 0
            if item.asset_type:
                avail = Asset.objects.filter(asset_type=item.asset_type, status__slug='available').count()
                if avail < 1:
                    all_available = False
            elif item.accessory:
                avail = item.accessory.remaining_qty
                if avail < item.qty:
                    all_available = False
            elif item.license:
                avail = item.license.available_seats
                if avail < 1:
                    all_available = False
            
            items_with_availability.append({
                'item': item,
                'available_count': avail,
                'is_available': (avail >= (item.qty if item.accessory else 1))
            })
            
        context['items_with_availability'] = items_with_availability
        context['all_available'] = all_available
        return context


class KitEditView(ObjectEditView):
    queryset = Kit.objects.all()
    model = Kit
    model_form = forms.KitForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:kit_list'


class KitDeleteView(ObjectDeleteView):
    queryset = Kit.objects.all()
    model = Kit
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:kit_list')


class KitItemEditView(ObjectEditView):
    queryset = KitItem.objects.all()
    model = KitItem
    model_form = forms.KitItemForm
    template_name = 'generic/object_edit.html'

    def get_initial(self):
        initial = super().get_initial()
        kit_id = self.request.GET.get('kit')
        if kit_id:
            initial['kit'] = kit_id
        return initial

    def get_success_url(self):
        if self.object and self.object.kit:
            return self.object.kit.get_absolute_url()
        return reverse('assets:kit_list')


class KitItemDeleteView(ObjectDeleteView):
    queryset = KitItem.objects.all()
    model = KitItem
    template_name = 'generic/object_confirm_delete.html'

    def get_success_url(self):
        if self.object and self.object.kit:
            return self.object.kit.get_absolute_url()
        return reverse('assets:kit_list')


# Atomic Kit Checkout
class KitCheckoutView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from django.db import transaction
        from django.core.exceptions import ValidationError
        kit = get_object_or_404(Kit, pk=pk)
        form = forms.KitCheckoutForm(request.POST)
        
        if not form.is_valid():
            if request.htmx:
                context = {
                    'form': form,
                    'kit': kit,
                }
                return render(request, "assets/includes/kit_checkout_modal.html#checkout-modal-form", context)
            return HttpResponseBadRequest("Invalid checkout form data.")

        holder = form.cleaned_data.get('assigned_holder')
        location = form.cleaned_data.get('assigned_location')
        notes = form.cleaned_data.get('notes') or ''

        try:
            with transaction.atomic():
                in_use_status = StatusLabel.objects.filter(slug='in-use').first()
                if not in_use_status:
                    raise ValidationError("The 'in-use' Status Label does not exist. Please configure it.")

                allocated_assets = []

                # Verification Pass
                for item in kit.items.all():
                    if item.asset_type:
                        asset = Asset.objects.filter(asset_type=item.asset_type, status__slug='available').first()
                        if not asset:
                            raise ValidationError(f"No available assets of type '{item.asset_type}' in stock.")
                        allocated_assets.append(asset)
                    elif item.accessory:
                        rem = item.accessory.remaining_qty
                        if not item.accessory.allow_overallocate and rem < item.qty:
                            raise ValidationError(f"Insufficient stock for accessory '{item.accessory}'. Required: {item.qty}, Available: {rem}")
                    elif item.license:
                        rem = item.license.available_seats
                        if rem < 1:
                            raise ValidationError(f"No available seats for software license '{item.license}'.")

                # Execution Pass
                for item in kit.items.all():
                    if item.asset_type:
                        asset = Asset.objects.filter(asset_type=item.asset_type, status__slug='available').first()
                        asset.status = in_use_status
                        
                        if holder:
                            asset.location = None
                            assignee = holder
                        else:
                            asset.location = location
                            assignee = location
                        
                        asset._changelog_action = 'checkout'
                        asset._changelog_message = f"Checked out via Kit '{kit.name}'. {notes}"
                        asset.save(update_fields=['status', 'location'])
                        ActivityLog.objects.create(
                            asset=asset,
                            action='checked_out',
                            user=request.user,
                            notes=f"Checked out via Kit '{kit.name}'. {notes}"
                        )
                        
                        if holder:
                            AssetHolderAssignment.objects.update_or_create(
                                content_type=ContentType.objects.get_for_model(Asset),
                                object_id=asset.pk,
                                defaults={
                                    'asset_holder': holder,
                                }
                            )
                        else:
                            AssetHolderAssignment.objects.filter(
                                content_type=ContentType.objects.get_for_model(Asset),
                                object_id=asset.pk
                            ).delete()
                        
                    elif item.accessory:
                        AccessoryAssignment.objects.create(
                            accessory=item.accessory,
                            assigned_holder=holder,
                            assigned_location=location,
                            qty=item.qty,
                            notes=f"Checked out via Kit '{kit.name}'. {notes}"
                        )
                    elif item.license:
                        if holder:
                            LicenseSeatAssignment.objects.create(
                                license=item.license,
                                assigned_holder=holder,
                                notes=f"Checked out via Kit '{kit.name}'. {notes}"
                            )
                        elif allocated_assets:
                            LicenseSeatAssignment.objects.create(
                                license=item.license,
                                asset=allocated_assets[0],
                                notes=f"Checked out via Kit '{kit.name}'. {notes}"
                            )
                        else:
                            raise ValidationError(f"License seat for '{item.license.name}' must be assigned to either a Holder or an Asset.")

            messages.success(request, f"Kit '{kit.name}' checked out successfully.")
            
            if request.htmx:
                response = HttpResponse(status=204)
                response['HX-Trigger'] = json.dumps({
                    "closeModalEvent": None,
                    "kitListUpdated": None,
                    "showMessage": {"message": f"Kit '{kit.name}' checked out successfully.", "level": "success"}
                })
                return response
            return redirect(kit.get_absolute_url())

        except ValidationError as e:
            form.add_error(None, e.message)
            if request.htmx:
                context = {
                    'form': form,
                    'kit': kit,
                }
                return render(request, "assets/includes/kit_checkout_modal.html#checkout-modal-form", context)
            return render(request, "assets/includes/kit_checkout_modal.html", {'form': form, 'kit': kit})


@login_required
def kit_checkout_modal(request, pk):
    kit = get_object_or_404(Kit, pk=pk)
    
    # Check if kit has items
    if not kit.items.exists():
        return HttpResponse("This kit has no items to check out.", status=400)

    if request.method == 'POST':
        # Let KitCheckoutView handle it
        return KitCheckoutView.as_view()(request, pk=pk)
    else:
        form = forms.KitCheckoutForm()

    context = {'form': form, 'kit': kit}
    return render(request, 'assets/includes/kit_checkout_modal.html', context)


@login_required
def bulk_assign_assets(request):
    """Assign multiple selected assets to a single AssetHolder."""
    if request.method != 'POST':
        return HttpResponse(status=405)

    import json
    object_pks = request.POST.getlist('pk')
    holder_id = request.POST.get('holder_id')

    if not object_pks or not holder_id:
        messages.error(request, "No assets selected or no holder specified.")
        return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('assets:asset_list')))

    holder = get_object_or_404(AssetHolder, pk=holder_id)
    assets = Asset.objects.filter(pk__in=object_pks).select_related('status')
    ct = ContentType.objects.get_for_model(Asset)
    in_use_status = StatusLabel.objects.filter(slug='in-use').first()

    from django.db import transaction
    assigned = 0
    skipped = 0

    with transaction.atomic():
        for asset in assets:
            # Skip assets that are already assigned to this holder
            existing = AssetHolderAssignment.objects.filter(
                content_type=ct, object_id=asset.pk
            ).first()
            if existing and existing.asset_holder_id == int(holder_id):
                skipped += 1
                continue

            # Update or create assignment
            AssetHolderAssignment.objects.update_or_create(
                content_type=ct,
                object_id=asset.pk,
                defaults={'asset_holder': holder}
            )

            # Set status to in-use
            if in_use_status:
                asset.status = in_use_status
                asset._changelog_action = 'checkout'
                asset._changelog_message = f'Bulk assigned to {holder}'
                asset.save(update_fields=['status'])
            assigned += 1

    messages.success(
        request,
        f"{assigned} asset(s) assigned to {holder}. {skipped} already assigned (skipped)."
    )

    # Return HTMX-triggered refresh if requested, otherwise redirect
    if request.htmx:
        response = HttpResponse(status=204)
        response['HX-Trigger'] = json.dumps({
            "assetListUpdated": None,
            "showMessage": {
                "message": f"{assigned} asset(s) assigned to {holder}.",
                "level": "success"
            }
        })
        return response

    return HttpResponseRedirect(request.META.get('HTTP_REFERER', reverse('assets:asset_list')))

class SupplierListView(ObjectListView):
    queryset = Supplier.objects.all()
    filterset = filters.SupplierFilterSet
    filterset_form = forms.SupplierFilterForm
    table = tables.SupplierTable
    action_buttons = ("add",)

class SupplierDetailView(ObjectDetailView):
    queryset = Supplier.objects.all()

    layout = (
        ((Panel('metrics', 'Supplier Overview'),),),
        ((Panel('info', 'Supplier Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        supplier = self.get_object()

        # Prepare Assets table supplied by this supplier
        supplier_assets = Asset.objects.filter(supplier=supplier).select_related(
            'asset_role', 'asset_type', 'location'
        )
        assets_table = tables.AssetTable(supplier_assets, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assets_table)
        context['assets_table'] = assets_table

        # Prepare Related Objects List
        related_objects_list = []
        asset_count = supplier_assets.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?supplier={supplier.slug}"
            })
        context['related_objects_list'] = related_objects_list
        return context

class SupplierEditView(ObjectEditView):
    queryset = Supplier.objects.all()
    model = Supplier
    model_form = forms.SupplierForm
    template_name = "generic/object_edit.html"
    default_return_url = "assets:supplier_list"

class SupplierDeleteView(ObjectDeleteView):
    queryset = Supplier.objects.all()
    model = Supplier
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("assets:supplier_list")

class CategoryListView(ObjectListView):
    queryset = Category.objects.all()
    filterset = filters.CategoryFilterSet
    filterset_form = forms.CategoryFilterForm
    table = tables.CategoryTable
    action_buttons = ("add",)

class CategoryDetailView(ObjectDetailView):
    queryset = Category.objects.all()

    layout = (
        ((Panel('metrics', 'Category Overview'),),),
        ((Panel('info', 'Category Details'),),),
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        category = self.get_object()

        # Prepare Asset Types table for this category
        cat_asset_types = AssetType.objects.filter(category=category).select_related('manufacturer')
        asset_types_table = tables.AssetTypeTable(cat_asset_types, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(asset_types_table)
        context['asset_types_table'] = asset_types_table

        # Prepare Accessories table for this category
        cat_accessories = Accessory.objects.filter(notification_category=category).select_related('manufacturer')
        accessories_table = tables.AccessoryTable(cat_accessories, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(accessories_table)
        context['accessories_table'] = accessories_table

        # Prepare Related Objects List
        related_objects_list = []
        assettype_count = cat_asset_types.count()
        if assettype_count:
            related_objects_list.append({
                'label': 'Asset Types',
                'count': assettype_count,
                'url': f"{reverse('assets:assettype_list')}?category={category.slug}"
            })
        accessory_count = cat_accessories.count()
        if accessory_count:
            related_objects_list.append({
                'label': 'Accessories',
                'count': accessory_count,
                'url': f"{reverse('assets:accessory_list')}?category={category.slug}"
            })
        context['related_objects_list'] = related_objects_list
        return context

class CategoryEditView(ObjectEditView):
    queryset = Category.objects.all()
    model = Category
    model_form = forms.CategoryForm
    template_name = "generic/object_edit.html"
    default_return_url = "assets:category_list"

class CategoryDeleteView(ObjectDeleteView):
    queryset = Category.objects.all()
    model = Category
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("assets:category_list")

class AssetRequestListView(ObjectListView):
    queryset = AssetRequest.objects.select_related("requester", "asset", "asset_type").all()
    filterset = filters.AssetRequestFilterSet
    filterset_form = forms.AssetRequestFilterForm
    table = tables.AssetRequestTable
    action_buttons = ("add",)

class AssetRequestDetailView(ObjectDetailView):
    queryset = AssetRequest.objects.select_related("requester", "asset", "asset_type", "responded_by").all()

    layout = (
        (
            (Panel('info', 'Asset Request Details'),),
            (Panel('response', 'Decision & Response Details'),),
        ),
    )

class AssetRequestCreateView(ObjectEditView):
    model = AssetRequest
    model_form = forms.AssetRequestForm
    template_name = "generic/object_edit.html"
    default_return_url = "assets:assetrequest_list"

    def form_valid(self, form):
        form.instance.requester = self.request.user
        return super().form_valid(form)

class AssetRequestEditView(ObjectEditView):
    queryset = AssetRequest.objects.all()
    model = AssetRequest
    model_form = forms.AssetRequestResponseForm
    template_name = "generic/object_edit.html"

    def form_valid(self, form):
        if form.instance.status in ("approved", "denied", "fulfilled", "cancelled"):
            form.instance.response_date = timezone.now()
            form.instance.responded_by = self.request.user
        response = super().form_valid(form)
        try:
            from core.events import dispatch_event
            from core.models import Notification
            dispatch_event(AssetRequest, self.object, action='update')
            Notification.objects.create(
                user=self.object.requester,
                subject=f"Asset Request {self.object.get_status_display()}",
                message=f"Your request for {self.object} has been {self.object.get_status_display().lower()}.",
                level=Notification.LEVEL_INFO,
            )
        except Exception:
            pass
        return response

    def get_success_url(self):
        if self.object:
            return self.object.get_absolute_url()
        return reverse("assets:assetrequest_list")

class AssetRequestQueueView(ObjectListView):
    queryset = AssetRequest.objects.filter(status=AssetRequest.STATUS_PENDING).select_related("requester", "asset", "asset_type")
    filterset = filters.AssetRequestFilterSet
    filterset_form = forms.AssetRequestFilterForm
    table = tables.AssetRequestTable
    action_buttons = ()
    template_name = 'generic/object_list.html'

class AssetRequestDeleteView(ObjectDeleteView):
    queryset = AssetRequest.objects.all()
    model = AssetRequest
    template_name = "generic/object_confirm_delete.html"
    success_url = reverse_lazy("assets:assetrequest_list")


# --- Import Views ---
from .forms.import_forms import (
    AssetBulkImportForm, AssetTypeBulkImportForm, ManufacturerBulkImportForm,
    AccessoryBulkImportForm, ConsumableBulkImportForm,
)


class AssetImportView(ObjectImportView):
    model_form = AssetBulkImportForm


class AssetTypeImportView(ObjectImportView):
    model_form = AssetTypeBulkImportForm


class ManufacturerImportView(ObjectImportView):
    model_form = ManufacturerBulkImportForm


class AccessoryImportView(ObjectImportView):
    model_form = AccessoryBulkImportForm


class ConsumableImportView(ObjectImportView):
    model_form = ConsumableBulkImportForm


class AssetTagSequenceListView(ObjectListView):
    queryset = AssetTagSequence.objects.all()
    filterset = filters.AssetTagSequenceFilterSet
    filterset_form = forms.AssetTagSequenceFilterForm
    table = tables.AssetTagSequenceTable
    action_buttons = ('add',)


class AssetTagSequenceDetailView(ObjectDetailView):
    queryset = AssetTagSequence.objects.all()

    layout = (
        ((Panel('info', 'Asset Tag Sequence Details'),),),
    )


class AssetTagSequenceEditView(ObjectEditView):
    queryset = AssetTagSequence.objects.all()
    model = AssetTagSequence
    model_form = forms.AssetTagSequenceForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:assettagsequence_list'


class AssetTagSequenceDeleteView(ObjectDeleteView):
    queryset = AssetTagSequence.objects.all()
    model = AssetTagSequence
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('assets:assettagsequence_list')
