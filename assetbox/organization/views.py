from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST # Keep this if needed for other views
from django.urls import reverse # Add this import

# Import models from the organization app
from .models import Site, Region, SiteGroup, Tenant, Location, TenantGroup, AssetHolder, AssetHolderAssignment
# Import models from the extras app
from extras.models import Tag # Added import for Tag from extras
# Import forms from the organization app
from .forms import SiteForm, RegionForm, SiteGroupForm, LocationForm, TenantGroupForm, TenantForm, AssetHolderForm
from django_tables2 import RequestConfig
from .tables import ( # Import the tables
    SiteTable, RegionTable, SiteGroupTable, LocationTable, TenantTable, TenantGroupTable,
    AssetHolderTable, AssetHolderAssignmentTable
)
from core.utils import get_paginate_count, get_model_viewname # Import the utility function and get_model_viewname
from assets.tables import AssetTable # Import AssetTable
from assets.models import Asset # Import Asset model

from django.contrib.auth import get_user_model

# Import filters
from .filters import (
    SiteFilterSet, RegionFilterSet, SiteGroupFilterSet, LocationFilterSet,
    TenantFilterSet, TenantGroupFilterSet, AssetHolderFilterSet
)

User = get_user_model()

# Create your views here.

# --- Site Views ---

@login_required
def site_list(request):
    queryset = Site.objects.all().select_related('region', 'group', 'tenant')
    
    # Apply filters
    filterset = SiteFilterSet(request.GET, queryset=queryset)
    queryset = filterset.qs

    table = SiteTable(queryset, request=request)
    RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)
    model = table.Meta.model
    model_name_str = f"{model._meta.app_label}.{model._meta.model_name}"
    context = {
        'table': table,
        'title': 'Sites',
        'object_type': 'Site',
        'create_url_name': 'organization:site_create',
        'list_url_name': 'organization:site_list',
        'model_name_str': model_name_str,
        'filter_form': filterset, # <-- Add filter form
    }
    return render(request, 'generic/object_list_base.html', context)

@login_required
def site_detail(request, pk):
    # Fetch site and prefetch related locations and tags
    site = get_object_or_404(
        Site.objects.select_related('region', 'group', 'tenant').prefetch_related(
            'locations', 'locations__tenant', # Prefetch locations and their tenant
            'tags'
        ), 
        pk=pk
    )
    
    # Prepare Locations table
    locations_table = LocationTable(site.locations.all(), request=request)
    RequestConfig(request, paginate=False).configure(locations_table) # Disable pagination for embedded table
    
    model = Site

    # --- Prepare Related Objects List ---
    related_objects_list = []
    # Locations
    location_count = site.locations.count()
    if location_count:
        related_objects_list.append({
            'label': 'Locations',
            'count': location_count,
            # TODO: Add filtering parameter later ?site={site.slug}
            'url': reverse('organization:location_list')
        })
    # Assets (at this site via locations)
    asset_count = Asset.objects.filter(location__site=site).count()
    if asset_count:
        related_objects_list.append({
            'label': 'Assets',
            'count': asset_count,
            # TODO: Add filtering parameter later ?site={site.slug}
            'url': reverse('assets:asset_list')
        })
    # --- End Related Objects List ---
    
    context = {
        'object': site, # Use generic 'object'
        'title': str(site),
        'object_type': model._meta.verbose_name.title(),
        'locations_table': locations_table, # Pass locations table for full width block
        'update_url_name': get_model_viewname(model, 'update'),
        'delete_url_name': get_model_viewname(model, 'delete'),
        'view_options': ['update', 'delete'],
        'related_objects_list': related_objects_list, # Pass related objects list for sidebar
    }
    # Render the specific template
    return render(request, 'organization/site_detail.html', context)

@login_required
def site_create(request):
    if request.method == 'POST':
        form = SiteForm(request.POST) # Use organization form
        if form.is_valid():
            # TODO: Automatically generate slug if needed
            site = form.save()
            # TODO: Add message
            # Use organization URL name
            return redirect('organization:site_detail', pk=site.pk)
    else:
        form = SiteForm() # Use organization form
    context = {'form': form, 'title': 'Create Site', 'return_url': 'organization:site_list'}
    return render(request, 'generic/object_edit.html', context)

@login_required
def site_update(request, pk):
    site = get_object_or_404(Site, pk=pk) # Use organization model
    if request.method == 'POST':
        form = SiteForm(request.POST, instance=site) # Use organization form
        if form.is_valid():
            form.save()
            # TODO: Add message
            # Use organization URL name
            return redirect('organization:site_detail', pk=site.pk)
    else:
        form = SiteForm(instance=site) # Use organization form
    context = {'form': form, 'object': site, 'title': f'Update Site: {site.name}', 'return_url': 'organization:site_list'}
    return render(request, 'generic/object_edit.html', context)

@login_required
def site_delete(request, pk):
    site = get_object_or_404(Site, pk=pk) # Use organization model
    related_count = site.locations.count() + site.assets.count()
    if request.method == 'POST':
        if related_count > 0:
            # TODO: Error message
            return redirect('organization:site_list')
        site.delete()
        # TODO: Message
        return redirect('organization:site_list')
    context = {'object': site, 'related_objects_count': related_count, 'list_url_name': 'organization:site_list'}
    return render(request, 'generic/object_confirm_delete.html', context)

# --- Region Views ---

@login_required
def region_list(request):
    queryset = Region.objects.all().prefetch_related('sites') # Prefetch for count

    # Apply filters
    filterset = RegionFilterSet(request.GET, queryset=queryset)
    queryset = filterset.qs

    table = RegionTable(queryset, request=request)
    RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)
    model = table.Meta.model
    model_name_str = f"{model._meta.app_label}.{model._meta.model_name}"
    context = {
        'table': table,
        'title': 'Regions',
        'object_type': 'Region',
        'create_url_name': 'organization:region_create',
        'list_url_name': 'organization:region_list',
        'model_name_str': model_name_str,
        'filter_form': filterset, # <-- Add filter form
    }
    return render(request, 'generic/object_list_base.html', context)

@login_required
def region_detail(request, pk):
    # Fetch region and prefetch related sites and tags
    region = get_object_or_404(
        Region.objects.prefetch_related('children', 'tags', 'sites__tenant', 'sites__group'), # Include tenant/group for site table links
        pk=pk
    )
    
    # Prepare Sites table
    sites_table = SiteTable(region.sites.all(), request=request)
    RequestConfig(request, paginate=False).configure(sites_table) # Disable pagination
    
    model = Region

    # --- Prepare Related Objects List ---
    related_objects_list = []
    # Sites
    site_count = region.sites.count()
    if site_count:
        related_objects_list.append({
            'label': 'Sites',
            'count': site_count,
            # TODO: Add filtering parameter later ?region={region.slug}
            'url': reverse('organization:site_list') 
        })
    # Child Regions
    child_count = region.children.count()
    if child_count:
         related_objects_list.append({
            'label': 'Child Regions',
            'count': child_count,
            # TODO: Add filtering parameter later ?parent={region.slug}
            'url': reverse('organization:region_list')
        })
    # --- End Related Objects List ---

    context = {
        'object': region, # Use generic 'object'
        'title': str(region),
        'object_type': model._meta.verbose_name.title(),
        'sites_table': sites_table, # Pass sites table for full width block
        'update_url_name': get_model_viewname(model, 'update'),
        'delete_url_name': get_model_viewname(model, 'delete'),
        'view_options': ['update', 'delete'], 
        'related_objects_list': related_objects_list, # Pass related objects list for sidebar
    }
    # Render the specific template
    return render(request, 'organization/region_detail.html', context)

@login_required
def region_create(request):
    if request.method == 'POST':
        form = RegionForm(request.POST)
        if form.is_valid():
            form.save()
            # TODO: Message
            return redirect('organization:region_list')
    else:
        form = RegionForm()
    context = {'form': form, 'title': 'Create Region', 'return_url': 'organization:region_list'}
    return render(request, 'generic/object_edit.html', context)

@login_required
def region_update(request, pk):
    region = get_object_or_404(Region, pk=pk)
    if request.method == 'POST':
        form = RegionForm(request.POST, instance=region)
        if form.is_valid():
            form.save()
            # TODO: Message
            return redirect('organization:region_list')
    else:
        form = RegionForm(instance=region)
    context = {'form': form, 'object': region, 'title': f'Update Region: {region.name}', 'return_url': 'organization:region_list'}
    return render(request, 'generic/object_edit.html', context)

@login_required
def region_delete(request, pk):
    region = get_object_or_404(Region, pk=pk)
    site_count = region.sites.count()
    if request.method == 'POST':
        if site_count > 0:
            # TODO: Error message
            return redirect('organization:region_list')
        region.delete()
        # TODO: Message
        return redirect('organization:region_list')
    context = {'object': region, 'related_objects_count': site_count, 'list_url_name': 'organization:region_list'}
    return render(request, 'generic/object_confirm_delete.html', context)

# --- Site Group Views ---

@login_required
def sitegroup_list(request):
    queryset = SiteGroup.objects.all().prefetch_related('sites') # Prefetch for count

    # Apply filters
    filterset = SiteGroupFilterSet(request.GET, queryset=queryset)
    queryset = filterset.qs

    table = SiteGroupTable(queryset, request=request)
    RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)
    model = table.Meta.model
    model_name_str = f"{model._meta.app_label}.{model._meta.model_name}"
    context = {
        'table': table,
        'title': 'Site Groups',
        'object_type': 'Site Group',
        'create_url_name': 'organization:sitegroup_create',
        'list_url_name': 'organization:sitegroup_list',
        'model_name_str': model_name_str,
        'filter_form': filterset, # <-- Add filter form
    }
    return render(request, 'generic/object_list_base.html', context)

@login_required
def sitegroup_detail(request, pk):
    # Fetch sitegroup and prefetch related sites (with their tenant/region), children, tags
    sitegroup = get_object_or_404(
        SiteGroup.objects.prefetch_related('children', 'tags', 'sites__tenant', 'sites__region'), 
        pk=pk
    )
    
    # Prepare Sites table
    sites_table = SiteTable(sitegroup.sites.all(), request=request)
    RequestConfig(request, paginate=False).configure(sites_table) # Disable pagination
    
    model = SiteGroup
    
    related_objects_list = [] # Use a different name to avoid context clash
    # Sites
    site_count = sitegroup.sites.count()
    if site_count:
        related_objects_list.append({
            'label': 'Sites',
            'count': site_count,
            # TODO: Add filtering parameter later ?group={sitegroup.slug}
            'url': reverse('organization:site_list')
        })
    # Child Groups
    child_count = sitegroup.children.count()
    if child_count:
         related_objects_list.append({
            'label': 'Child Groups',
            'count': child_count,
            # TODO: Add filtering parameter later ?parent={sitegroup.slug}
            'url': reverse('organization:sitegroup_list')
        })
         
    context = {
        'object': sitegroup, # Use generic 'object'
        'title': str(sitegroup),
        'object_type': model._meta.verbose_name.title(),
        'sites_table': sites_table, # Pass sites table
        'update_url_name': get_model_viewname(model, 'update'),
        'delete_url_name': get_model_viewname(model, 'delete'),
        'view_options': ['update', 'delete'], 
        'related_objects_list': related_objects_list,
    }
    # Render the specific template
    return render(request, 'organization/sitegroup_detail.html', context)

@login_required
def sitegroup_create(request):
    if request.method == 'POST':
        form = SiteGroupForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('organization:sitegroup_list')
    else:
        form = SiteGroupForm()
    context = {'form': form, 'title': 'Create Site Group', 'return_url': 'organization:sitegroup_list'}
    return render(request, 'generic/object_edit.html', context)

@login_required
def sitegroup_update(request, pk):
    sitegroup = get_object_or_404(SiteGroup, pk=pk)
    if request.method == 'POST':
        form = SiteGroupForm(request.POST, instance=sitegroup)
        if form.is_valid():
            form.save()
            return redirect('organization:sitegroup_list')
    else:
        form = SiteGroupForm(instance=sitegroup)
    context = {'form': form, 'object': sitegroup, 'title': f'Update Site Group: {sitegroup.name}', 'return_url': 'organization:sitegroup_list'}
    return render(request, 'generic/object_edit.html', context)

@login_required
def sitegroup_delete(request, pk):
    sitegroup = get_object_or_404(SiteGroup, pk=pk)
    site_count = sitegroup.sites.count()
    if request.method == 'POST':
        if site_count > 0:
            return redirect('organization:sitegroup_list')
        sitegroup.delete()
        return redirect('organization:sitegroup_list')
    context = {'object': sitegroup, 'related_objects_count': site_count, 'list_url_name': 'organization:sitegroup_list'}
    return render(request, 'generic/object_confirm_delete.html', context)

# --- Location Views ---

@login_required
def location_list(request):
    queryset = Location.objects.all().select_related('site', 'tenant')

    # Apply filters
    filterset = LocationFilterSet(request.GET, queryset=queryset)
    queryset = filterset.qs

    table = LocationTable(queryset, request=request)
    RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)
    model = table.Meta.model
    model_name_str = f"{model._meta.app_label}.{model._meta.model_name}"
    context = {
        'table': table,
        'title': 'Locations',
        'object_type': 'Location',
        'create_url_name': 'organization:location_create',
        'list_url_name': 'organization:location_list',
        'model_name_str': model_name_str,
        'filter_form': filterset, # <-- Add filter form
    }
    return render(request, 'generic/object_list_base.html', context)

@login_required
def location_detail(request, pk):
    location = get_object_or_404(
        Location.objects.select_related('site', 'parent', 'tenant').prefetch_related('children', 'tags', 'assets'), # Changed asset_set to assets
        pk=pk
    )

    # Prepare Assets table
    assets_table = AssetTable(location.assets.all(), request=request)
    RequestConfig(request, paginate=False).configure(assets_table)

    model = Location

    # --- Prepare Related Objects List ---
    related_objects_list = []
    # Assets
    asset_count = location.assets.count()
    if asset_count:
        related_objects_list.append({
            'label': 'Assets',
            'count': asset_count,
            # TODO: Add filtering parameter later ?location={location.slug}
            'url': reverse('assets:asset_list')
        })
    # Child Locations
    child_count = location.children.count()
    if child_count:
         related_objects_list.append({
            'label': 'Child Locations',
            'count': child_count,
            # TODO: Add filtering parameter later ?parent={location.slug}
            'url': reverse('organization:location_list')
        })
    # --- End Related Objects List ---

    context = {
        'object': location, # Use generic 'object'
        'title': str(location),
        'object_type': model._meta.verbose_name.title(),
        'assets_table': assets_table, # Pass assets table
        'update_url_name': get_model_viewname(model, 'update'),
        'delete_url_name': get_model_viewname(model, 'delete'),
        'view_options': ['update', 'delete'],
        'related_objects_list': related_objects_list, # Pass related objects list
    }
    return render(request, 'organization/location_detail.html', context)

@login_required
def location_create(request):
    if request.method == 'POST':
        form = LocationForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('organization:location_list')
    else:
        form = LocationForm()
    context = {'form': form, 'title': 'Create Location', 'return_url': 'organization:location_list'}
    return render(request, 'generic/object_edit.html', context)

@login_required
def location_update(request, pk):
    location = get_object_or_404(Location, pk=pk)
    if request.method == 'POST':
        form = LocationForm(request.POST, instance=location)
        if form.is_valid():
            form.save()
            return redirect('organization:location_list')
    else:
        form = LocationForm(instance=location)
    context = {'form': form, 'object': location, 'title': f'Update Location: {location.name}', 'return_url': 'organization:location_list'}
    return render(request, 'generic/object_edit.html', context)

@login_required
def location_delete(request, pk):
    location = get_object_or_404(Location, pk=pk)
    related_count = location.assets.count()
    if request.method == 'POST':
        if related_count > 0:
            return redirect('organization:location_list')
        location.delete()
        return redirect('organization:location_list')
    context = {'object': location, 'related_objects_count': related_count, 'list_url_name': 'organization:location_list'}
    return render(request, 'generic/object_confirm_delete.html', context)

# --- Tenant Group Views ---

@login_required
def tenantgroup_list(request):
    queryset = TenantGroup.objects.all().prefetch_related('tenants') # Prefetch for count

    # Apply filters
    filterset = TenantGroupFilterSet(request.GET, queryset=queryset)
    queryset = filterset.qs

    table = TenantGroupTable(queryset, request=request)
    RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)
    model = table.Meta.model
    model_name_str = f"{model._meta.app_label}.{model._meta.model_name}"
    context = {
        'table': table,
        'title': 'Tenant Groups',
        'object_type': 'Tenant Group',
        'create_url_name': 'organization:tenantgroup_create',
        'list_url_name': 'organization:tenantgroup_list',
        'model_name_str': model_name_str,
        'filter_form': filterset, # <-- Add filter form
    }
    return render(request, 'generic/object_list_base.html', context)

@login_required
def tenantgroup_detail(request, pk):
    tenantgroup = get_object_or_404(
        TenantGroup.objects.prefetch_related('children', 'tags', 'tenants'),
        pk=pk
    )
    context = {'tenantgroup': tenantgroup}
    return render(request, 'organization/tenantgroup_detail.html', context)

@login_required
def tenantgroup_create(request):
    if request.method == 'POST':
        form = TenantGroupForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('organization:tenantgroup_list')
    else:
        form = TenantGroupForm()
    context = {'form': form, 'title': 'Create Tenant Group', 'return_url': 'organization:tenantgroup_list'}
    return render(request, 'generic/object_edit.html', context)

@login_required
def tenantgroup_update(request, pk):
    tenantgroup = get_object_or_404(TenantGroup, pk=pk)
    if request.method == 'POST':
        form = TenantGroupForm(request.POST, instance=tenantgroup)
        if form.is_valid():
            form.save()
            return redirect('organization:tenantgroup_list')
    else:
        form = TenantGroupForm(instance=tenantgroup)
    context = {'form': form, 'object': tenantgroup, 'title': f'Update Tenant Group: {tenantgroup.name}', 'return_url': 'organization:tenantgroup_list'}
    return render(request, 'generic/object_edit.html', context)

@login_required
def tenantgroup_delete(request, pk):
    tenantgroup = get_object_or_404(TenantGroup, pk=pk)
    tenant_count = tenantgroup.tenants.count()
    if request.method == 'POST':
        if tenant_count > 0:
            return redirect('organization:tenantgroup_list')
        tenantgroup.delete()
        return redirect('organization:tenantgroup_list')
    context = {'object': tenantgroup, 'related_objects_count': tenant_count, 'list_url_name': 'organization:tenantgroup_list'}
    return render(request, 'generic/object_confirm_delete.html', context)

# --- Tenant Views ---

@login_required
def tenant_list(request):
    queryset = Tenant.objects.all().select_related('group')

    # Apply filters
    filterset = TenantFilterSet(request.GET, queryset=queryset)
    queryset = filterset.qs

    table = TenantTable(queryset, request=request)
    RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)
    model = table.Meta.model
    model_name_str = f"{model._meta.app_label}.{model._meta.model_name}"
    context = {
        'table': table,
        'title': 'Tenants',
        'object_type': 'Tenant',
        'create_url_name': 'organization:tenant_create',
        'list_url_name': 'organization:tenant_list',
        'model_name_str': model_name_str,
        'filter_form': filterset, # <-- Add filter form
    }
    return render(request, 'generic/object_list_base.html', context)

@login_required
def tenant_detail(request, pk):
    tenant = get_object_or_404(
        Tenant.objects.select_related('group').prefetch_related('tags', 'sites', 'locations'), # Added sites/locations
        pk=pk
    )
    context = {'tenant': tenant}
    return render(request, 'organization/tenant_detail.html', context)

@login_required
def tenant_create(request):
    if request.method == 'POST':
        form = TenantForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('organization:tenant_list')
    else:
        form = TenantForm()
    context = {'form': form, 'title': 'Create Tenant', 'return_url': 'organization:tenant_list'}
    return render(request, 'generic/object_edit.html', context)

@login_required
def tenant_update(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    if request.method == 'POST':
        form = TenantForm(request.POST, instance=tenant)
        if form.is_valid():
            form.save()
            return redirect('organization:tenant_list')
    else:
        form = TenantForm(instance=tenant)
    context = {'form': form, 'object': tenant, 'title': f'Update Tenant: {tenant.name}', 'return_url': 'organization:tenant_list'}
    return render(request, 'generic/object_edit.html', context)

@login_required
def tenant_delete(request, pk):
    tenant = get_object_or_404(Tenant, pk=pk)
    related_count = tenant.sites.count() + tenant.locations.count() # Add other counts if needed
    if request.method == 'POST':
        if related_count > 0:
            return redirect('organization:tenant_list')
        tenant.delete()
        return redirect('organization:tenant_list')
    context = {'object': tenant, 'related_objects_count': related_count, 'list_url_name': 'organization:tenant_list'}
    return render(request, 'generic/object_confirm_delete.html', context)

# TODO: Add views for Tag

# --- AssetHolder Views ---

@login_required
def assetholder_list(request):
    # Annotate count or prefetch?
    # Prefetching might be better if displaying assignments on list (not default)
    queryset = AssetHolder.objects.select_related('tenant').prefetch_related('assignments')

    # Apply filters
    filterset = AssetHolderFilterSet(request.GET, queryset=queryset)
    queryset = filterset.qs

    table = AssetHolderTable(queryset, request=request)
    RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)
    model = table.Meta.model
    model_name_str = f"{model._meta.app_label}.{model._meta.model_name}"
    context = {
        'table': table,
        'title': 'Asset Holders',
        'object_type': 'Asset Holder',
        'create_url_name': 'organization:assetholder_create',
        'list_url_name': 'organization:assetholder_list',
        'model_name_str': model_name_str,
        'filter_form': filterset, # <-- Add filter form
    }
    return render(request, 'generic/object_list_base.html', context)

@login_required
def assetholder_detail(request, pk):
    assetholder = get_object_or_404(
        AssetHolder.objects.select_related('tenant', 'user').prefetch_related('assignments__assigned_object', 'assignments__content_type', 'tags'), # Added content_type prefetch
        pk=pk
    )
    assignments_table = AssetHolderAssignmentTable(assetholder.assignments.all(), request=request) # Pass request for potential future features
    RequestConfig(request, paginate=False).configure(assignments_table) # Disable pagination for embedded table
    
    model = AssetHolder

    # --- Prepare Related Objects List ---
    related_objects_list = []
    # Assignments
    assignment_count = assetholder.assignments.count()
    if assignment_count:
        related_objects_list.append({
            'label': 'Assignments',
            'count': assignment_count,
            # TODO: Add filtering parameter later ?assetholder={assetholder.upn}
            'url': reverse('organization:assetholderassignment_list') 
        })
    # --- End Related Objects List ---
    
    context = {
        'object': assetholder, # Use generic 'object'
        'title': str(assetholder), # Use object string representation for title
        'object_type': model._meta.verbose_name.title(),
        'assignments_table': assignments_table, # Pass table for full width block
        'update_url_name': get_model_viewname(model, 'update'),
        'delete_url_name': get_model_viewname(model, 'delete'),
        'view_options': ['update', 'delete'], # Standard actions
        'related_objects_list': related_objects_list, # Pass related objects list for sidebar
    }
    # Render the specific detail template now
    return render(request, 'organization/assetholder_detail.html', context)

@login_required
def assetholder_create(request):
    if request.method == 'POST':
        form = AssetHolderForm(request.POST)
        if form.is_valid():
            assetholder = form.save()
            # TODO: Message
            # Redirect to list or detail view
            return redirect('organization:assetholder_list')
    else:
        form = AssetHolderForm()
    context = {
        'form': form,
        'title': 'Create Asset Holder',
        'return_url': 'organization:assetholder_list',
    }
    return render(request, 'generic/object_edit.html', context)

@login_required
def assetholder_update(request, pk):
    assetholder = get_object_or_404(AssetHolder, pk=pk)
    if request.method == 'POST':
        form = AssetHolderForm(request.POST, instance=assetholder)
        if form.is_valid():
            form.save()
            # TODO: Message
            return redirect('organization:assetholder_list')
    else:
        form = AssetHolderForm(instance=assetholder)
    context = {
        'form': form,
        'object': assetholder,
        'title': f'Update Asset Holder: {assetholder}',
        'return_url': 'organization:assetholder_list',
    }
    return render(request, 'generic/object_edit.html', context)

@login_required
def assetholder_delete(request, pk):
    assetholder = get_object_or_404(AssetHolder, pk=pk)
    related_count = assetholder.assignments.count()
    if request.method == 'POST':
        if related_count > 0:
            # TODO: Error - cannot delete holder with assignments
            return redirect('organization:assetholder_list')
        assetholder.delete()
        # TODO: Message
        return redirect('organization:assetholder_list')

    context = {
        'object': assetholder,
        'related_objects_count': related_count,
        'list_url_name': 'organization:assetholder_list'
    }
    return render(request, 'generic/object_confirm_delete.html', context)


# --- AssetHolderAssignment Views ---

@login_required
def assetholderassignment_list(request):
    queryset = AssetHolderAssignment.objects.all().select_related(
        'asset_holder', 'content_type'
    ).prefetch_related('assigned_object') # Prefetch generic relation

    table = AssetHolderAssignmentTable(queryset, request=request)
    # Assignments list is read-only, no create button or model config needed usually
    RequestConfig(request, paginate={'per_page': get_paginate_count(request)}).configure(table)

    context = {
        'table': table,
        'title': 'Asset Holder Assignments',
        'list_url_name': 'organization:assetholderassignment_list',
        # No object_type or create_url_name needed for read-only
    }
    # Use the standard list base, but without create/config buttons
    return render(request, 'generic/object_list_base.html', context)
