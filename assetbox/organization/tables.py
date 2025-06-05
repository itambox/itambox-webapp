# assetbox/organization/tables.py
import django_tables2 as tables
from django_tables2.utils import A
from .models import Site, Region, SiteGroup, Location, Tenant, TenantGroup, AssetHolder, AssetHolderAssignment
from core.tables.columns import ActionsColumn
from core.tables.base import BaseTable
from assets.models import Asset

class RegionTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('organization:region_detail', args=[A('pk')], verbose_name='Name')
    site_count = tables.Column(verbose_name='Sites', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Region
        fields = ('pk', 'name', 'slug', 'description', 'site_count', 'actions')
        default_columns = ('pk', 'name', 'site_count', 'description', 'actions')

    def render_site_count(self, record):
        return record.sites.count()

class SiteGroupTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('organization:sitegroup_detail', args=[A('pk')], verbose_name='Name')
    site_count = tables.Column(verbose_name='Sites', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = SiteGroup
        fields = ('pk', 'name', 'slug', 'description', 'site_count', 'actions')
        default_columns = ('pk', 'name', 'site_count', 'description', 'actions')

    def render_site_count(self, record):
        return record.sites.count()

class SiteTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('organization:site_detail', args=[A('pk')], verbose_name='Name')
    region = tables.LinkColumn('organization:region_detail', args=[A('region.pk')], accessor='region')
    group = tables.LinkColumn('organization:sitegroup_detail', args=[A('group.pk')], accessor='group')
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant.pk')], accessor='tenant')
    location_count = tables.Column(verbose_name='Locations', orderable=False)
    asset_count = tables.Column(verbose_name='Assets', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Site
        fields = ('pk', 'name', 'slug', 'status', 'region', 'group', 'tenant', 'description', 'location_count', 'asset_count', 'actions')
        default_columns = ('pk', 'name', 'status', 'region', 'group', 'tenant', 'location_count', 'asset_count', 'actions')

    def render_location_count(self, record):
        return record.locations.count()

    def render_asset_count(self, record):
        # Might be slow, consider annotating in view
        return Asset.objects.filter(location__site=record).count()

class LocationTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('organization:location_detail', args=[A('pk')], verbose_name='Name')
    site = tables.LinkColumn('organization:site_detail', args=[A('site.pk')], accessor='site')
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant.pk')], accessor='tenant')
    asset_count = tables.Column(verbose_name='Assets', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Location
        fields = ('pk', 'name', 'slug', 'status', 'site', 'tenant', 'description', 'asset_count', 'actions')
        default_columns = ('pk', 'name', 'status', 'site', 'tenant', 'asset_count', 'actions')

    def render_asset_count(self, record):
        return record.assets.count() # Uses related name

class TenantGroupTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('organization:tenantgroup_update', args=[A('pk')], verbose_name='Name')
    tenant_count = tables.Column(verbose_name='Tenants', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = TenantGroup
        fields = ('pk', 'name', 'slug', 'description', 'tenant_count', 'actions')
        default_columns = ('pk', 'name', 'tenant_count', 'description', 'actions')

    def render_tenant_count(self, record):
        return record.tenants.count()

class TenantTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('organization:tenant_detail', args=[A('pk')], verbose_name='Name')
    group = tables.LinkColumn('organization:tenantgroup_detail', args=[A('group.pk')], accessor='group')
    site_count = tables.Column(verbose_name='Sites', orderable=False)
    location_count = tables.Column(verbose_name='Locations', orderable=False)
    # Consider adding asset count if needed, might be complex query
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Tenant
        fields = ('pk', 'name', 'slug', 'group', 'description', 'site_count', 'location_count', 'actions')
        default_columns = ('pk', 'name', 'group', 'site_count', 'location_count', 'actions')

    def render_site_count(self, record):
        return record.sites.count()

    def render_location_count(self, record):
        return record.locations.count()

# We need Asset imported for SiteTable.render_asset_count
from assets.models import Asset

# --- AssetHolder Table ---
class AssetHolderTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    upn = tables.LinkColumn('organization:assetholder_detail', args=[A('pk')], verbose_name='UPN')
    first_name = tables.Column()
    last_name = tables.Column()
    tenant = tables.LinkColumn('organization:tenant_update', args=[A('tenant.pk')], accessor='tenant')
    assignment_count = tables.Column(verbose_name='Assignments', orderable=False, accessor='assignments.count')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = AssetHolder
        fields = ('pk', 'upn', 'first_name', 'last_name', 'email', 'tenant', 'assignment_count', 'description', 'actions')
        default_columns = ('pk', 'upn', 'first_name', 'last_name', 'tenant', 'assignment_count', 'actions')

# --- AssetHolderAssignment Table ---
class AssetHolderAssignmentTable(BaseTable):
    # No Checkbox or Actions needed for read-only
    asset_holder = tables.LinkColumn('organization:assetholder_update', args=[A('asset_holder.pk')], accessor='asset_holder')
    assigned_object_type = tables.Column(accessor='content_type', verbose_name='Object Type')
    assigned_object = tables.Column(linkify=True, verbose_name='Assigned Object') # Linkify uses get_absolute_url

    class Meta(BaseTable.Meta):
        model = AssetHolderAssignment
        fields = ('asset_holder', 'assigned_object_type', 'assigned_object', 'created_at')
        default_columns = ('asset_holder', 'assigned_object_type', 'assigned_object')

    def render_assigned_object_type(self, record):
        return record.content_type.model_class()._meta.verbose_name.title() 