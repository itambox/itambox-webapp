# assetbox/organization/tables.py
import django_tables2 as tables
from django_tables2.utils import A
from .models import Site, Region, SiteGroup, Location, Tenant, TenantGroup, AssetHolder, AssetHolderAssignment, Contact, ContactRole, ContactAssignment
from core.tables import ActionsColumn, BaseTable, ToggleColumn
from extras.tables import TagColumn

from assets.models import Asset
from django.urls import reverse

class RegionTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:region_detail', args=[A('pk')], verbose_name='Name')
    site_count = tables.Column(verbose_name='Sites', orderable=False)
    tags = TagColumn(url_name='organization:region_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Region
        fields = ('pk', 'name', 'slug', 'description', 'site_count', 'tags', 'actions')
        default_columns = ('pk', 'name', 'site_count', 'description', 'tags', 'actions')

    def render_site_count(self, value, record=None):
        return value or 0

class SiteGroupTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:sitegroup_detail', args=[A('pk')], verbose_name='Name')
    site_count = tables.Column(verbose_name='Sites', orderable=False)
    tags = TagColumn(url_name='organization:sitegroup_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = SiteGroup
        fields = ('pk', 'name', 'site_count', 'description', 'tags', 'actions')
        default_columns = ('pk', 'name', 'site_count', 'description', 'tags', 'actions')

    def render_site_count(self, value, record=None):
        return value or 0

class SiteTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:site_detail', args=[A('pk')], verbose_name='Name')
    region = tables.LinkColumn('organization:region_detail', args=[A('region.pk')], accessor='region')
    group = tables.LinkColumn('organization:sitegroup_detail', args=[A('group.pk')], accessor='group')
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant.pk')], accessor='tenant')
    location_count = tables.Column(verbose_name='Locations', orderable=False)
    asset_count = tables.Column(verbose_name='Assets', orderable=False)
    tags = TagColumn(url_name='organization:site_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Site
        fields = ('pk', 'name', 'slug', 'status', 'region', 'group', 'tenant', 'description', 'location_count', 'asset_count', 'tags', 'actions')
        default_columns = ('pk', 'name', 'status', 'region', 'group', 'tenant', 'location_count', 'asset_count', 'tags', 'actions')

    def render_location_count(self, value, record=None):
        return value or 0

    def render_asset_count(self, value, record=None):
        return value or 0

class LocationTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:location_detail', args=[A('pk')], verbose_name='Name')
    site = tables.LinkColumn('organization:site_detail', args=[A('site.pk')], accessor='site')
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant.pk')], accessor='tenant')
    asset_count = tables.Column(verbose_name='Assets', orderable=False)
    tags = TagColumn(url_name='organization:location_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Location
        fields = ('pk', 'name', 'slug', 'status', 'site', 'tenant', 'description', 'asset_count', 'tags', 'actions')
        default_columns = ('pk', 'name', 'status', 'site', 'tenant', 'asset_count', 'tags', 'actions')

    def render_asset_count(self, value, record=None):
        return value or 0

class TenantGroupTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:tenantgroup_detail', args=[A('pk')], verbose_name='Name')
    tenant_count = tables.Column(verbose_name='Tenants', orderable=False)
    tags = TagColumn(url_name='organization:tenantgroup_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = TenantGroup
        fields = ('pk', 'name', 'slug', 'description', 'tenant_count', 'tags', 'actions')
        default_columns = ('pk', 'name', 'tenant_count', 'description', 'tags', 'actions')

    def render_tenant_count(self, value, record=None):
        return value or 0

class TenantTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:tenant_detail', args=[A('pk')], verbose_name='Name')
    group = tables.LinkColumn('organization:tenantgroup_detail', args=[A('group.pk')], accessor='group')
    site_count = tables.Column(verbose_name='Sites', orderable=False)
    location_count = tables.Column(verbose_name='Locations', orderable=False)
    tags = TagColumn(url_name='organization:tenant_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Tenant
        fields = ('pk', 'name', 'slug', 'group', 'description', 'site_count', 'location_count', 'tags', 'actions')
        default_columns = ('pk', 'name', 'group', 'site_count', 'location_count', 'tags', 'actions')

    def render_site_count(self, value, record=None):
        return value or 0

    def render_location_count(self, value, record=None):
        return value or 0


# --- AssetHolder Table ---
class AssetHolderTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    upn = tables.LinkColumn('organization:assetholder_detail', args=[A('pk')], verbose_name='UPN')
    first_name = tables.Column()
    last_name = tables.Column()
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant.pk')], accessor='tenant', verbose_name='Tenant')
    assignment_count = tables.Column(verbose_name='Assignments', orderable=False, accessor='assignment_count')
    tags = TagColumn(url_name='organization:assetholder_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = AssetHolder
        fields = ('pk', 'upn', 'first_name', 'last_name', 'email', 'tenant', 'assignment_count', 'description', 'tags', 'actions')
        default_columns = ('pk', 'upn', 'first_name', 'last_name', 'tenant', 'assignment_count', 'tags', 'actions')

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


# --- Contact Table ---
class ContactTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:contact_detail', args=[A('pk')], verbose_name='Name')
    title = tables.Column()
    phone = tables.Column()
    email = tables.EmailColumn()
    tags = TagColumn(url_name='organization:contact_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Contact
        fields = ('pk', 'name', 'title', 'phone', 'email', 'web_url', 'description', 'tags', 'actions')
        default_columns = ('pk', 'name', 'title', 'phone', 'email', 'tags', 'actions')


# --- ContactRole Table ---
class ContactRoleTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:contactrole_detail', args=[A('pk')], verbose_name='Name')
    slug = tables.Column()
    description = tables.Column()
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = ContactRole
        fields = ('pk', 'name', 'slug', 'description', 'actions')
        default_columns = ('pk', 'name', 'slug', 'description', 'actions')


# --- ContactAssignment Table ---
class ContactAssignmentTable(BaseTable):
    contact = tables.LinkColumn('organization:contact_detail', args=[A('contact.pk')], accessor='contact')
    role = tables.Column()
    assigned_object_type = tables.Column(accessor='content_type', verbose_name='Object Type')
    assigned_object = tables.Column(linkify=True, verbose_name='Assigned Object')
    priority = tables.Column()
    actions = tables.TemplateColumn(
        template_code='''
        <a href="{% url 'organization:contactassignment_delete' record.pk %}?return_url={{ request.path }}" class="btn btn-sm btn-danger px-2" title="Delete">
            <i class="mdi mdi-trash-can-outline m-0"></i>
        </a>
        ''',
        verbose_name='Actions',
        orderable=False
    )

    class Meta(BaseTable.Meta):
        model = ContactAssignment
        fields = ('contact', 'role', 'assigned_object_type', 'assigned_object', 'priority', 'actions')
        default_columns = ('contact', 'role', 'assigned_object_type', 'assigned_object', 'priority', 'actions')

    def render_assigned_object_type(self, record):
        return record.content_type.model_class()._meta.verbose_name.title()
 