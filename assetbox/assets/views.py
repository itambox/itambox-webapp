from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.utils import timezone
from .models import Asset, AssetRole, Manufacturer, AssetType, InstalledSoftware, StatusLabel, Depreciation, ActivityLog, Supplier, Category, AssetRequest, AssetTagSequence
from .forms import AssetForm, AssetRoleForm, ManufacturerForm, AssetCheckOutForm, AssetTypeForm # Keep only Asset forms
from inventory.models import Accessory
from django.contrib.auth import get_user_model
from django_tables2 import RequestConfig
from .tables import AssetTable, AssetRoleTable, ManufacturerTable, AssetTypeTable
from software.tables import InstalledSoftwareTable
from .filters import AssetRoleFilterSet, ManufacturerFilterSet, AssetTypeFilterSet 
# --- Add imports needed for CBVs --- 
from . import filters
from . import forms
from . import tables
# --- End imports ---
from core.utils import get_paginate_count
from core.panels import Panel
from django.http import HttpResponse
from django.contrib.contenttypes.models import ContentType
from organization.models import AssetHolderAssignment, AssetHolder
from django.urls import reverse, reverse_lazy
from django.contrib import messages # <--- Add this import
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from core.views import ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView, ObjectImportView, BaseHTMXView, ObjectBulkEditView, ObjectBulkDeleteView, ObjectCloneView
from core.quick_add import QuickAddMixin
from django.db.models import Count
import json
from django.utils import timezone

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
    ).prefetch_related('tags', 'maintenances')

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

class AssetCloneView(ObjectCloneView):
    model = Asset
    model_form = forms.AssetForm
    template_name = 'generic/object_edit.html'

    def pre_save_clone(self, original, cloned):
        cloned.asset_tag = ''


class AssetTypeCloneView(ObjectCloneView):
    model = AssetType
    model_form = forms.AssetTypeForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:assettype_list'



class SupplierCloneView(ObjectCloneView):
    model = Supplier
    model_form = forms.SupplierForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:supplier_list'


class CategoryCloneView(ObjectCloneView):
    model = Category
    model_form = forms.CategoryForm
    template_name = 'generic/object_edit.html'
    default_return_url = 'assets:category_list'

@login_required
def asset_checkout_modal(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    
    # Ensure asset is available before proceeding
    if not asset.status or asset.status.slug != 'available':
        return HttpResponse("Asset is not available for assignment.", status=403)

    if request.method == 'POST':
        form = forms.AssetCheckOutForm(request.POST)
        if form.is_valid():
            from .services import checkout_asset
            selected_holder = form.cleaned_data.get('asset_holder')
            selected_location = form.cleaned_data.get('location')
            
            try:
                assignee = checkout_asset(
                    asset,
                    holder=selected_holder,
                    location=selected_location,
                    user=request.user,
                    request=request
                )
                messages.success(request, f"Asset '{asset}' checked out successfully to {assignee}.")

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
            except Exception as e:
                form.add_error(None, str(e))
                context = {'form': form, 'asset': asset, 'request': request}
                return render(request, "assets/includes/asset_checkout_modal.html#checkout-modal-form", context)
        else:
            # --- HTMX Response for Validation Error ---
            # Re-render only the partial form body inside the modal
            context = {'form': form, 'asset': asset, 'request': request}
            return render(request, "assets/includes/asset_checkout_modal.html#checkout-modal-form", context)
    else: # GET request
        form = forms.AssetCheckOutForm()

    # Initial GET still renders the whole modal template via the placeholder swap
    context = {'form': form, 'asset': asset}
    return render(request, 'assets/includes/asset_checkout_modal.html', context)

@login_required
@require_POST
def asset_checkin(request, pk):
    asset = get_object_or_404(Asset, pk=pk)
    from .services import checkin_asset
    
    msg = checkin_asset(asset, user=request.user)
    if msg:
        messages.success(request, f"Asset '{asset}' successfully {msg.lower()}.")
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

# --- Phase 4 views ---
import segno

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


# =============================================================================
# Custom Fields, Fieldsets, Depreciation & Onboarding Kits Views
# =============================================================================

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
)


class AssetImportView(ObjectImportView):
    model_form = AssetBulkImportForm


class AssetTypeImportView(ObjectImportView):
    model_form = AssetTypeBulkImportForm


class ManufacturerImportView(ObjectImportView):
    model_form = ManufacturerBulkImportForm





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


class AssetBulkEditView(ObjectBulkEditView):
    queryset = Asset.objects.all()
    form_class = forms.AssetBulkEditForm


class AssetBulkDeleteView(ObjectBulkDeleteView):
    queryset = Asset.objects.all()

