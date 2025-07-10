# --- START OF FILE views.py ---

from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseBadRequest
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse, reverse_lazy
from django.views.generic import View
from django.contrib.auth import get_user_model
from django.contrib import messages


from core.views import ObjectListView, ObjectDetailView, ObjectEditView, ObjectDeleteView # Import base CBVs
from core.utils import get_paginate_count, get_model_viewname # Import the utility function and get_model_viewname
from assets.tables import AssetTable # Import AssetTable
from assets.models import Asset # Import Asset model


# Import models from the organization app
from .models import Site, Region, SiteGroup, Tenant, Location, TenantGroup, AssetHolder, AssetHolderAssignment, Contact, ContactRole, ContactAssignment
# Import models from the extras app
from extras.models import Tag # Added import for Tag from extras
# Import forms from the organization app
from .forms import (
    SiteForm, RegionForm, SiteGroupForm, LocationForm, TenantGroupForm, TenantForm, AssetHolderForm,
    SiteFilterForm, RegionFilterForm, SiteGroupFilterForm, LocationFilterForm, TenantFilterForm,
    TenantGroupFilterForm, AssetHolderFilterForm, ContactForm, ContactRoleForm, ContactAssignmentForm,
    ContactFilterForm, ContactRoleFilterForm
)
from django_tables2 import RequestConfig
from .tables import ( # Import the tables
    SiteTable, RegionTable, SiteGroupTable, LocationTable, TenantTable, TenantGroupTable,
    AssetHolderTable, AssetHolderAssignmentTable, ContactTable, ContactRoleTable, ContactAssignmentTable
)


# Import filters
from .filters import (
    SiteFilterSet, RegionFilterSet, SiteGroupFilterSet, LocationFilterSet,
    TenantFilterSet, TenantGroupFilterSet, AssetHolderFilterSet, ContactFilterSet, ContactRoleFilterSet
)
from users.models import UserPreference # Import UserPreference

# Import Count from django.db.models
from django.db.models import Count
from django.contrib.contenttypes.models import ContentType

User = get_user_model()


# Create your views here.

# --- Site Views ---

class SiteListView(ObjectListView):
    queryset = Site.objects.select_related('region', 'group', 'tenant').prefetch_related('tags').annotate(
        location_count=Count('locations'),
        asset_count=Count('locations__assets'),
    )
    filterset = SiteFilterSet
    filterset_form = SiteFilterForm # Use the dedicated form
    table = SiteTable
    action_buttons = ('add',)

class SiteDetailView(ObjectDetailView):
    queryset = Site.objects.select_related('region', 'group', 'tenant').prefetch_related(
            'locations', 'locations__tenant', # Prefetch locations and their tenant
            'tags'
    )
    # template_name = 'organization/sites/site_detail.html' # Can be inferred

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        site = self.get_object()

        # Prepare Locations table
        locations_table = LocationTable(site.locations.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(locations_table)

        # Prepare Assets table for Site
        site_assets = Asset.objects.filter(location__site=site).select_related('asset_role', 'asset_type', 'asset_type__manufacturer', 'location')
        assets_table = AssetTable(site_assets, request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assets_table)

        # Prepare Related Objects List
        related_objects_list = []
        location_count = site.locations.count()
        if location_count:
            related_objects_list.append({
                'label': 'Locations',
                'count': location_count,
                'url': f"{reverse('organization:location_list')}?site={site.slug}" # Filter link
            })
        asset_count = site_assets.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?site={site.slug}" # Filter link
            })

        # *** INDENTATION FIX START ***
        context['locations_table'] = locations_table
        context['assets_table'] = assets_table
        context['related_objects_list'] = related_objects_list
        # Base view already adds common context like title, object_type, etc.
        # Base view provides edit/delete URLs in context['action_urls'] based on get_model_viewname
        return context
        # *** INDENTATION FIX END ***

class SiteEditView(ObjectEditView):
    queryset = Site.objects.all() # Required by base view
    model = Site
    model_form = SiteForm
    template_name = 'generic/object_edit.html'
    # success_url is handled by base view's get_success_url -> object.get_absolute_url()
    # Success messages handled by base view's form_valid

class SiteDeleteView(ObjectDeleteView):
    queryset = Site.objects.all()
    model = Site
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:site_list')

    def post(self, request, *args, **kwargs):
        site = self.get_object()
        # Correctly check related locations and assets via locations
        location_count = site.locations.count()
        asset_count = Asset.objects.filter(location__site=site).count()

        # *** INDENTATION FIX START ***
        if location_count > 0 or asset_count > 0:
            related_object_details = []
            if location_count > 0:
                related_object_details.append(f"{location_count} location{'s' if location_count != 1 else ''}")
            if asset_count > 0:
                related_object_details.append(f"{asset_count} asset{'s' if asset_count != 1 else ''}")

            messages.error(
                request,
                f"Cannot delete site '{site.name}': It is associated with {', '.join(related_object_details)}."
            )
            # Redirect back to the site detail page or list page
            return redirect(site.get_absolute_url())

        # If no related objects, proceed with deletion using superclass method
        return super().post(request, *args, **kwargs)
        # *** INDENTATION FIX END ***

# --- Region Views ---

class RegionListView(ObjectListView):
    queryset = Region.objects.annotate(
        site_count=Count('sites')
    ).prefetch_related('tags')
    filterset = RegionFilterSet
    filterset_form = RegionFilterForm # Use the dedicated form
    table = RegionTable
    action_buttons = ('add',)

class RegionDetailView(ObjectDetailView):
    queryset = Region.objects.prefetch_related(
        'children', 'tags', 'sites__tenant', 'sites__group' # Include tenant/group for site table links
    )
    # template_name = 'organization/regions/region_detail.html' # Can be inferred

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        region = self.get_object()

        # Prepare Sites table
        sites_table = SiteTable(region.sites.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(sites_table)

        # Prepare Related Objects List
        related_objects_list = []
        site_count = region.sites.count()
        if site_count:
            related_objects_list.append({
                'label': 'Sites',
                'count': site_count,
                'url': f"{reverse('organization:site_list')}?region={region.slug}" # Filter link
            })
        child_count = region.children.count()
        if child_count:
             related_objects_list.append({
                'label': 'Child Regions',
                'count': child_count,
                'url': f"{reverse('organization:region_list')}?parent={region.slug}" # Filter link
            })

        # *** INDENTATION FIX START ***
        context['sites_table'] = sites_table
        context['related_objects_list'] = related_objects_list
        return context
        # *** INDENTATION FIX END ***

class RegionEditView(ObjectEditView):
    queryset = Region.objects.all()
    model = Region
    model_form = RegionForm
    template_name = 'generic/object_edit.html'
    # Default success_url goes to object detail view

class RegionDeleteView(ObjectDeleteView):
    queryset = Region.objects.all()
    model = Region
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:region_list')

    def post(self, request, *args, **kwargs):
        region = self.get_object()
        site_count = region.sites.count()

        # *** INDENTATION FIX START ***
        if site_count > 0:
            messages.error(
                request,
                f"Cannot delete region '{region.name}': It is associated with {site_count} site{'s' if site_count != 1 else ''}."
            )
            return redirect(region.get_absolute_url())

        return super().post(request, *args, **kwargs)
        # *** INDENTATION FIX END ***

# --- Site Group Views ---

class SiteGroupListView(ObjectListView):
    queryset = SiteGroup.objects.annotate(
        site_count=Count('sites')
    ).prefetch_related('tags')
    filterset = SiteGroupFilterSet
    filterset_form = SiteGroupFilterForm # Use the dedicated form
    table = SiteGroupTable
    action_buttons = ('add',)

class SiteGroupDetailView(ObjectDetailView):
    queryset = SiteGroup.objects.prefetch_related(
        'children', 'tags', 'sites__tenant', 'sites__region' # Prefetch related for SiteTable links
    )
    # template_name = 'organization/sitegroups/sitegroup_detail.html' # Can be inferred

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sitegroup = self.get_object()

        # Prepare Sites table
        sites_table = SiteTable(sitegroup.sites.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(sites_table)

        # Prepare Related Objects List
        related_objects_list = []
        site_count = sitegroup.sites.count()
        if site_count:
            related_objects_list.append({
                'label': 'Sites',
                'count': site_count,
                'url': f"{reverse('organization:site_list')}?group={sitegroup.slug}" # Filter link
            })
        child_count = sitegroup.children.count()
        if child_count:
             related_objects_list.append({
                'label': 'Child Groups',
                'count': child_count,
                'url': f"{reverse('organization:sitegroup_list')}?parent={sitegroup.slug}" # Filter link
            })

        # *** INDENTATION FIX START ***
        context['sites_table'] = sites_table
        context['related_objects_list'] = related_objects_list
        return context
        # *** INDENTATION FIX END ***

class SiteGroupEditView(ObjectEditView):
    queryset = SiteGroup.objects.all()
    model = SiteGroup
    model_form = SiteGroupForm
    template_name = 'generic/object_edit.html'
    # Default success_url goes to object detail view

class SiteGroupDeleteView(ObjectDeleteView):
    queryset = SiteGroup.objects.all()
    model = SiteGroup
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:sitegroup_list')

    def post(self, request, *args, **kwargs):
        sitegroup = self.get_object()
        site_count = sitegroup.sites.count()

        # *** INDENTATION FIX START ***
        if site_count > 0:
            messages.error(
                request,
                f"Cannot delete site group '{sitegroup.name}': It is associated with {site_count} site{'s' if site_count != 1 else ''}."
            )
            return redirect(sitegroup.get_absolute_url())

        return super().post(request, *args, **kwargs)
        # *** INDENTATION FIX END ***

# --- Location Views ---

class LocationListView(ObjectListView):
    queryset = Location.objects.select_related('site', 'site__region', 'tenant').prefetch_related('tags').annotate(
        asset_count=Count('assets'),
    )
    filterset = LocationFilterSet
    filterset_form = LocationFilterForm # Use the dedicated form
    table = LocationTable
    action_buttons = ('add',)

class LocationDetailView(ObjectDetailView):
    queryset = Location.objects.select_related(
        'site', 'parent', 'tenant'
    ).prefetch_related(
        'children', 'tags', 'assets' # Changed asset_set to assets
    )
    # template_name = 'organization/locations/location_detail.html' # Can be inferred

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        location = self.get_object()

        # Prepare Assets table
        assets_table = AssetTable(location.assets.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assets_table)

        # Prepare Related Objects List
        related_objects_list = []
        asset_count = location.assets.count()
        if asset_count:
            related_objects_list.append({
                'label': 'Assets',
                'count': asset_count,
                'url': f"{reverse('assets:asset_list')}?location={location.slug}" # Filter link
            })
        child_count = location.children.count()
        if child_count:
             related_objects_list.append({
                'label': 'Child Locations',
                'count': child_count,
                'url': f"{reverse('organization:location_list')}?parent={location.slug}" # Filter link
            })

        # *** INDENTATION FIX START ***
        context['assets_table'] = assets_table
        context['related_objects_list'] = related_objects_list
        return context
        # *** INDENTATION FIX END ***

class LocationEditView(ObjectEditView):
    queryset = Location.objects.all()
    model = Location
    model_form = LocationForm
    template_name = 'generic/object_edit.html'
    # Default success_url goes to object detail view

class LocationDeleteView(ObjectDeleteView):
    queryset = Location.objects.all()
    model = Location
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:location_list')

    def post(self, request, *args, **kwargs):
        location = self.get_object()
        asset_count = location.assets.count()

        # *** INDENTATION FIX START ***
        if asset_count > 0:
            messages.error(
                request,
                f"Cannot delete location '{location.name}': It is associated with {asset_count} asset{'s' if asset_count != 1 else ''}."
            )
            return redirect(location.get_absolute_url())

        return super().post(request, *args, **kwargs)
        # *** INDENTATION FIX END ***

# --- Tenant Group Views ---

class TenantGroupListView(ObjectListView):
    queryset = TenantGroup.objects.annotate(
        tenant_count=Count('tenants')
    ).prefetch_related('tags')
    filterset = TenantGroupFilterSet
    filterset_form = TenantGroupFilterForm # Use the dedicated form
    table = TenantGroupTable
    action_buttons = ('add',)

class TenantGroupDetailView(ObjectDetailView):
    queryset = TenantGroup.objects.prefetch_related(
        'children', 'tags', 'tenants' # Removed 'tenants__group' as it's self-referential
    )
    # template_name = 'organization/tenantgroups/tenantgroup_detail.html' # Can be inferred

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenantgroup = self.get_object()

        # Prepare Tenants table
        tenants_table = TenantTable(tenantgroup.tenants.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(tenants_table)

        # Prepare Related Objects List
        related_objects_list = []
        tenant_count = tenantgroup.tenants.count()
        if tenant_count:
            related_objects_list.append({
                'label': 'Tenants',
                'count': tenant_count,
                'url': f"{reverse('organization:tenant_list')}?group={tenantgroup.slug}" # Filter link
            })
        child_count = tenantgroup.children.count()
        if child_count:
            related_objects_list.append({
                'label': 'Child Groups',
                'count': child_count,
                'url': f"{reverse('organization:tenantgroup_list')}?parent={tenantgroup.slug}" # Filter link
            })

        # *** INDENTATION FIX START ***
        context['tenants_table'] = tenants_table
        context['related_objects_list'] = related_objects_list
        return context
        # *** INDENTATION FIX END ***

class TenantGroupEditView(ObjectEditView):
    queryset = TenantGroup.objects.all()
    model = TenantGroup
    model_form = TenantGroupForm
    template_name = 'generic/object_edit.html'
    # Default success_url goes to object detail view

class TenantGroupDeleteView(ObjectDeleteView):
    queryset = TenantGroup.objects.all()
    model = TenantGroup
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:tenantgroup_list')

    def post(self, request, *args, **kwargs):
        tenantgroup = self.get_object()
        tenant_count = tenantgroup.tenants.count()

        # *** INDENTATION FIX START ***
        if tenant_count > 0:
            messages.error(
                request,
                f"Cannot delete tenant group '{tenantgroup.name}': It is associated with {tenant_count} tenant{'s' if tenant_count != 1 else ''}."
            )
            return redirect(tenantgroup.get_absolute_url())

        return super().post(request, *args, **kwargs)
        # *** INDENTATION FIX END ***

# --- Tenant Views ---

class TenantListView(ObjectListView):
    queryset = Tenant.objects.select_related('group').prefetch_related('tags').annotate(
        site_count=Count('sites'),
        location_count=Count('locations'),
    )
    filterset = TenantFilterSet
    filterset_form = TenantFilterForm # Use the dedicated form
    table = TenantTable
    action_buttons = ('add',)

class TenantDetailView(ObjectDetailView):
    queryset = Tenant.objects.select_related('group').prefetch_related(
        'tags', 'sites__region', 'locations__site' # Prefetch for related tables
    )
    # template_name = 'organization/tenants/tenant_detail.html' # Can be inferred

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tenant = self.get_object()

        # Prepare Sites table
        sites_table = SiteTable(tenant.sites.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(sites_table)

        # Prepare Locations table
        locations_table = LocationTable(tenant.locations.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(locations_table)

        # Prepare AssetHolders table
        assetholders_table = AssetHolderTable(tenant.asset_holders.all(), request=self.request) # Use related_name
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assetholders_table)

        # Prepare Related Objects List
        related_objects_list = []
        site_count = tenant.sites.count()
        if site_count:
            related_objects_list.append({
                'label': 'Sites',
                'count': site_count,
                'url': f"{reverse('organization:site_list')}?tenant={tenant.slug}" # Filter link
            })
        location_count = tenant.locations.count()
        if location_count:
            related_objects_list.append({
                'label': 'Locations',
                'count': location_count,
                'url': f"{reverse('organization:location_list')}?tenant={tenant.slug}" # Filter link
            })
        assetholder_count = tenant.asset_holders.count()
        if assetholder_count:
            related_objects_list.append({
                'label': 'Asset Holders',
                'count': assetholder_count,
                'url': f"{reverse('organization:assetholder_list')}?tenant={tenant.slug}" # Filter link
            })

        # *** INDENTATION FIX START ***
        context['sites_table'] = sites_table
        context['locations_table'] = locations_table
        context['assetholders_table'] = assetholders_table
        context['related_objects_list'] = related_objects_list
        return context
        # *** INDENTATION FIX END ***

class TenantEditView(ObjectEditView):
    queryset = Tenant.objects.all()
    model = Tenant
    model_form = TenantForm
    template_name = 'generic/object_edit.html'
    # Default success_url goes to object detail view

class TenantDeleteView(ObjectDeleteView):
    queryset = Tenant.objects.all()
    model = Tenant
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:tenant_list')

    def post(self, request, *args, **kwargs):
        tenant = self.get_object()
        related_count = tenant.sites.count() + tenant.locations.count() + tenant.asset_holders.count()

        # *** INDENTATION FIX START ***
        if related_count > 0:
            related_details = []
            if tenant.sites.exists(): related_details.append(f"{tenant.sites.count()} sites")
            if tenant.locations.exists(): related_details.append(f"{tenant.locations.count()} locations")
            if tenant.asset_holders.exists(): related_details.append(f"{tenant.asset_holders.count()} asset holders")
            messages.error(
                request,
                f"Cannot delete tenant '{tenant.name}': It is associated with {', '.join(related_details)}."
            )
            return redirect(tenant.get_absolute_url())

        return super().post(request, *args, **kwargs)
        # *** INDENTATION FIX END ***

# TODO: Add views for Tag

# --- AssetHolder Views ---

class AssetHolderListView(ObjectListView):
    queryset = AssetHolder.objects.prefetch_related('tags').annotate(
        assignment_count=Count('assignments'),
    )
    filterset = AssetHolderFilterSet
    filterset_form = AssetHolderFilterForm # Use the dedicated form
    table = AssetHolderTable
    action_buttons = ('add',)

class AssetHolderDetailView(ObjectDetailView):
    queryset = AssetHolder.objects.select_related('tenant', 'user').prefetch_related(
        'assignments__assigned_object', 'assignments__content_type', 'tags' # Added content_type prefetch
    )
    # template_name = 'organization/assetholders/assetholder_detail.html' # Can be inferred

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        assetholder = self.get_object()

        # Prepare Assignments table
        assignments_table = AssetHolderAssignmentTable(assetholder.assignments.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assignments_table)

        # Prepare Related Objects List
        related_objects_list = []
        assignment_count = assetholder.assignments.count()
        if assignment_count:
            related_objects_list.append({
                'label': 'Assignments',
                'count': assignment_count,
                # Link to the filtered list view for assignments related to this holder
                'url': f"{reverse('organization:assetholderassignment_list')}?asset_holder={assetholder.pk}" # Filter link
            })

        # *** INDENTATION FIX START ***
        context['assignments_table'] = assignments_table
        context['related_objects_list'] = related_objects_list
        return context
        # *** INDENTATION FIX END ***

class AssetHolderEditView(ObjectEditView):
    queryset = AssetHolder.objects.all()
    model = AssetHolder
    model_form = AssetHolderForm
    template_name = 'generic/object_edit.html'
    # Default success_url goes to object detail view

class AssetHolderDeleteView(ObjectDeleteView):
    queryset = AssetHolder.objects.all()
    model = AssetHolder
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:assetholder_list')

    def post(self, request, *args, **kwargs):
        assetholder = self.get_object()
        assignment_count = assetholder.assignments.count()

        # *** INDENTATION FIX START ***
        if assignment_count > 0:
            messages.error(
                request,
                f"Cannot delete asset holder '{assetholder}': It has {assignment_count} assignment{'s' if assignment_count != 1 else ''}."
            )
            return redirect(assetholder.get_absolute_url())

        return super().post(request, *args, **kwargs)
        # *** INDENTATION FIX END ***

# --- AssetHolderAssignment Views ---

class AssetHolderAssignmentListView(ObjectListView):
    queryset = AssetHolderAssignment.objects.select_related('asset_holder', 'content_type')
    # TODO: Add filterset and filterset_form if filtering becomes necessary
    # filterset = filters.AssetHolderAssignmentFilterSet
    # filterset_form = forms.AssetHolderAssignmentFilterForm
    table = AssetHolderAssignmentTable # Use 'table' attribute for ObjectListView
    action_buttons = () # Read-only view

    # Add breadcrumbs
    def get_breadcrumbs(self):
        return [
            (reverse('dashboard'), 'Dashboard'),
            (reverse('organization:assetholder_list'), 'Asset Holders'), # Link to parent list
            (None, 'Assignments') # Current page
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Asset Holder Assignments' # Set specific title
        # Base class handles table, filter_form, model_name_str, table_config_key etc.
        return context


# --- Contact Views ---

class ContactListView(ObjectListView):
    queryset = Contact.objects.prefetch_related('tags')
    filterset = ContactFilterSet
    filterset_form = ContactFilterForm
    table = ContactTable
    action_buttons = ('add',)


class ContactDetailView(ObjectDetailView):
    queryset = Contact.objects.prefetch_related('tags', 'assignments')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        contact = self.get_object()

        # Prepare Assignments table for this contact
        assignments_table = ContactAssignmentTable(contact.assignments.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assignments_table)

        context['assignments_table'] = assignments_table
        return context


class ContactEditView(ObjectEditView):
    queryset = Contact.objects.all()
    model = Contact
    model_form = ContactForm
    template_name = 'generic/object_edit.html'


class ContactDeleteView(ObjectDeleteView):
    queryset = Contact.objects.all()
    model = Contact
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:contact_list')

    def post(self, request, *args, **kwargs):
        contact = self.get_object()
        assignment_count = contact.assignments.count()

        if assignment_count > 0:
            messages.error(
                request,
                f"Cannot delete contact '{contact}': It has {assignment_count} assignment{'s' if assignment_count != 1 else ''}."
            )
            return redirect(contact.get_absolute_url())

        return super().post(request, *args, **kwargs)


# --- ContactRole Views ---

class ContactRoleListView(ObjectListView):
    queryset = ContactRole.objects.all()
    filterset = ContactRoleFilterSet
    filterset_form = ContactRoleFilterForm
    table = ContactRoleTable
    action_buttons = ('add',)


class ContactRoleDetailView(ObjectDetailView):
    queryset = ContactRole.objects.prefetch_related('assignments')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        role = self.get_object()

        # Prepare Assignments table for this role
        assignments_table = ContactAssignmentTable(role.assignments.all(), request=self.request)
        RequestConfig(self.request, paginate={'per_page': get_paginate_count(self.request)}).configure(assignments_table)

        context['assignments_table'] = assignments_table
        return context


class ContactRoleEditView(ObjectEditView):
    queryset = ContactRole.objects.all()
    model = ContactRole
    model_form = ContactRoleForm
    template_name = 'generic/object_edit.html'


class ContactRoleDeleteView(ObjectDeleteView):
    queryset = ContactRole.objects.all()
    model = ContactRole
    template_name = 'generic/object_confirm_delete.html'
    success_url = reverse_lazy('organization:contactrole_list')

    def post(self, request, *args, **kwargs):
        role = self.get_object()
        assignment_count = role.assignments.count()

        if assignment_count > 0:
            messages.error(
                request,
                f"Cannot delete role '{role}': It is associated with {assignment_count} contact assignment{'s' if assignment_count != 1 else ''}."
            )
            return redirect(role.get_absolute_url())

        return super().post(request, *args, **kwargs)


# --- ContactAssignment Views ---

class ContactAssignmentCreateView(LoginRequiredMixin, View):
    """View to handle dynamic creation of ContactAssignments via a modal form."""
    template_name = 'organization/contactassignments/contactassignment_form.html'

    def get(self, request, *args, **kwargs):
        content_type_id = request.GET.get('content_type')
        object_id = request.GET.get('object_id')
        
        if not content_type_id or not object_id:
            return HttpResponseBadRequest("Missing content_type or object_id")
            
        content_type = get_object_or_404(ContentType, id=content_type_id)
        target_obj = get_object_or_404(content_type.model_class(), id=object_id)
        
        form = ContactAssignmentForm(content_type=content_type, object_id=object_id)
        context = {
            'form': form,
            'target_obj': target_obj,
            'content_type': content_type,
            'object_id': object_id,
        }
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        content_type_id = request.POST.get('content_type') or request.GET.get('content_type')
        object_id = request.POST.get('object_id') or request.GET.get('object_id')
        
        if not content_type_id or not object_id:
            return HttpResponseBadRequest("Missing content_type or object_id")
            
        content_type = get_object_or_404(ContentType, id=content_type_id)
        target_obj = get_object_or_404(content_type.model_class(), id=object_id)
        
        form = ContactAssignmentForm(request.POST, content_type=content_type, object_id=object_id)
        if form.is_valid():
            form.save()
            messages.success(request, f"Assigned contact successfully to {target_obj}.")
            return redirect(target_obj.get_absolute_url())
            
        context = {
            'form': form,
            'target_obj': target_obj,
            'content_type': content_type,
            'object_id': object_id,
        }
        return render(request, self.template_name, context)


class ContactAssignmentDeleteView(ObjectDeleteView):
    queryset = ContactAssignment.objects.all()
    model = ContactAssignment
    template_name = 'generic/object_confirm_delete.html'

    def get_success_url(self):
        # Redirect back to the assigned object after deletion
        return_url = self.request.GET.get('return_url') or self.request.POST.get('return_url')
        if return_url:
            return return_url
        obj = self.object
        if obj and obj.assigned_object and hasattr(obj.assigned_object, 'get_absolute_url'):
            return obj.assigned_object.get_absolute_url()
        return reverse('dashboard')