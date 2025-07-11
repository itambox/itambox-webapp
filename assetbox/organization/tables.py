# assetbox/organization/tables.py
import django_tables2 as tables
from django_tables2.utils import A
from .models import Site, Region, SiteGroup, Location, Tenant, TenantGroup, AssetHolder, AssetHolderAssignment, Contact, ContactRole, ContactAssignment
from core.tables import ActionsColumn, BaseTable

from assets.models import Asset
from django.urls import reverse

class RegionTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('organization:region_detail', args=[A('pk')], verbose_name='Name')
    site_count = tables.Column(verbose_name='Sites', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Region
        fields = ('pk', 'name', 'slug', 'description', 'site_count', 'actions')
        default_columns = ('pk', 'name', 'site_count', 'description', 'actions')

    def render_site_count(self, value, record=None):
        return value or 0

class SiteGroupTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('organization:sitegroup_detail', args=[A('pk')], verbose_name='Name')
    site_count = tables.Column(verbose_name='Sites', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = SiteGroup
        fields = ('pk', 'name', 'site_count', 'description', 'actions')
        default_columns = ('pk', 'name', 'site_count', 'description', 'actions')

    def render_site_count(self, value, record=None):
        return value or 0

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

    def render_location_count(self, value, record=None):
        return value or 0

    def render_asset_count(self, value, record=None):
        return value or 0

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

    def render_asset_count(self, value, record=None):
        return value or 0

class TenantGroupTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('organization:tenantgroup_update', args=[A('pk')], verbose_name='Name')
    tenant_count = tables.Column(verbose_name='Tenants', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = TenantGroup
        fields = ('pk', 'name', 'slug', 'description', 'tenant_count', 'actions')
        default_columns = ('pk', 'name', 'tenant_count', 'description', 'actions')

    def render_tenant_count(self, value, record=None):
        return value or 0

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

    def render_site_count(self, value, record=None):
        return value or 0

    def render_location_count(self, value, record=None):
        return value or 0


# --- AssetHolder Table ---
class AssetHolderTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    upn = tables.LinkColumn('organization:assetholder_detail', args=[A('pk')], verbose_name='UPN')
    first_name = tables.Column()
    last_name = tables.Column()
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant.pk')], accessor='tenant', verbose_name='Tenant')
    assignment_count = tables.Column(verbose_name='Assignments', orderable=False, accessor='assignment_count')
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


# --- Contact Table ---
class ContactTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('organization:contact_detail', args=[A('pk')], verbose_name='Name')
    title = tables.Column()
    phone = tables.Column()
    email = tables.EmailColumn()
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Contact
        fields = ('pk', 'name', 'title', 'phone', 'email', 'web_url', 'description', 'actions')
        default_columns = ('pk', 'name', 'title', 'phone', 'email', 'actions')


# --- ContactRole Table ---
class ContactRoleTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
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
            <svg xmlns="http://www.w3.org/2000/svg" class="icon icon-tabler icon-tabler-trash m-0" width="16" height="16" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor" fill="none" stroke-linecap="round" stroke-linejoin="round">
                <path stroke="none" d="M0 0h24v24H0z" fill="none"></path>
                <path d="M4 7l16 0"></path>
                <path d="M10 11l0 6"></path>
                <path d="M14 11l0 6"></path>
                <path d="M5 7l1 12a2 2 0 0 0 2 2h8a2 2 0 0 0 2 -2l1 -12"></path>
                <path d="M9 7v-3a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v3"></path>
            </svg>
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
 