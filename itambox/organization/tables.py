# itambox/organization/tables.py
import django_tables2 as tables
from django_tables2.utils import A
from .models import (
    Site, Region, SiteGroup, Location, Tenant, TenantGroup,
    AssetHolder, Contact, ContactRole, ContactAssignment,
    Role, Membership, CostCenter, Provider,
)
from core.tables import ActionsColumn, BaseTable, CountLinkColumn, ToggleColumn
from extras.tables import TagColumn

from assets.models import Asset, AssetAssignment
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _


class RegionTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:region_detail', args=[A('pk')], verbose_name=_('Name'))
    site_count = CountLinkColumn('organization:site_list', 'region', verbose_name=_('Sites'), orderable=False)
    tags = TagColumn(url_name='organization:region_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Region
        fields = ('pk', 'name', 'slug', 'description', 'site_count', 'tags', 'actions')
        default_columns = ('pk', 'name', 'site_count', 'description', 'tags', 'actions')

class SiteGroupTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:sitegroup_detail', args=[A('pk')], verbose_name=_('Name'))
    site_count = CountLinkColumn('organization:site_list', 'group', verbose_name=_('Sites'), orderable=False)
    tags = TagColumn(url_name='organization:sitegroup_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = SiteGroup
        fields = ('pk', 'name', 'site_count', 'description', 'tags', 'actions')
        default_columns = ('pk', 'name', 'site_count', 'description', 'tags', 'actions')

class SiteTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:site_detail', args=[A('pk')], verbose_name=_('Name'))
    region = tables.LinkColumn('organization:region_detail', args=[A('region_id')], accessor='region')
    group = tables.LinkColumn('organization:sitegroup_detail', args=[A('group_id')], accessor='group')
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant_id')], accessor='tenant')
    location_count = CountLinkColumn('organization:location_list', 'site', verbose_name=_('Locations'), orderable=False)
    asset_count = CountLinkColumn('assets:asset_list', 'site', verbose_name=_('Assets'), orderable=False)
    tags = TagColumn(url_name='organization:site_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Site
        fields = ('pk', 'name', 'slug', 'status', 'region', 'group', 'tenant', 'description', 'location_count', 'asset_count', 'tags', 'actions')
        default_columns = ('pk', 'name', 'status', 'region', 'group', 'tenant', 'location_count', 'asset_count', 'tags', 'actions')

    def render_status(self, value, record):
        if record and record.status:
            from itambox.utils import get_status_color
            color = get_status_color(record.status)
            display = record.get_status_display()
            return format_html(
                '<span class="badge badge-status" style="--status-color: #{};">'
                '<span class="badge-status-dot"></span>{}</span>',
                color, display
            )
        return "—"

class LocationTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:location_detail', args=[A('pk')], verbose_name=_('Name'))
    site = tables.LinkColumn('organization:site_detail', args=[A('site_id')], accessor='site')
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant_id')], accessor='tenant')
    asset_count = CountLinkColumn('assets:asset_list', 'location', verbose_name=_('Assets'), orderable=False)
    tags = TagColumn(url_name='organization:location_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Location
        fields = ('pk', 'name', 'slug', 'status', 'site', 'tenant', 'description', 'asset_count', 'tags', 'actions')
        default_columns = ('pk', 'name', 'status', 'site', 'tenant', 'asset_count', 'tags', 'actions')

    def render_status(self, value, record):
        if record and record.status:
            from itambox.utils import get_status_color
            color = get_status_color(record.status)
            display = record.get_status_display()
            return format_html(
                '<span class="badge badge-status" style="--status-color: #{};">'
                '<span class="badge-status-dot"></span>{}</span>',
                color, display
            )
        return "—"

class TenantGroupTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:tenantgroup_detail', args=[A('pk')], verbose_name=_('Name'))
    tenant_count = CountLinkColumn('organization:tenant_list', 'group', verbose_name=_('Tenants'), orderable=False)
    tags = TagColumn(url_name='organization:tenantgroup_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = TenantGroup
        fields = ('pk', 'name', 'slug', 'description', 'tenant_count', 'tags', 'actions')
        default_columns = ('pk', 'name', 'tenant_count', 'description', 'tags', 'actions')

class TenantTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:tenant_detail', args=[A('pk')], verbose_name=_('Name'))
    group = tables.LinkColumn('organization:tenantgroup_detail', args=[A('group_id')], accessor='group')
    provider = tables.LinkColumn('organization:provider_detail', args=[A('provider_id')], accessor='provider', verbose_name=_('Provider'))
    site_count = CountLinkColumn('organization:site_list', 'tenant', verbose_name=_('Sites'), orderable=False)
    location_count = CountLinkColumn('organization:location_list', 'tenant', verbose_name=_('Locations'), orderable=False)
    tags = TagColumn(url_name='organization:tenant_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Tenant
        fields = ('pk', 'name', 'slug', 'group', 'provider', 'description', 'site_count', 'location_count', 'tags', 'actions')
        default_columns = ('pk', 'name', 'group', 'provider', 'site_count', 'location_count', 'tags', 'actions')


# --- AssetHolder Table ---
class AssetHolderTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    upn = tables.LinkColumn('organization:assetholder_detail', args=[A('pk')], verbose_name=_('UPN'))
    first_name = tables.Column()
    last_name = tables.Column()
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant_id')], accessor='tenant', verbose_name=_('Tenant'))
    assignment_count = CountLinkColumn('assets:asset_list', 'assigned_to', verbose_name=_('Assignments'), orderable=False, accessor='assignment_count')
    tags = TagColumn(url_name='organization:assetholder_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = AssetHolder
        fields = ('pk', 'upn', 'first_name', 'last_name', 'email', 'tenant', 'assignment_count', 'description', 'tags', 'actions')
        default_columns = ('pk', 'upn', 'first_name', 'last_name', 'tenant', 'assignment_count', 'tags', 'actions')

# --- AssetAssignment Table ---
class AssetAssignmentTable(BaseTable):
    asset = tables.LinkColumn('assets:asset_detail', args=[A('asset_id')], verbose_name=_('Asset'))

    asset_tag = tables.Column(accessor='asset.asset_tag', verbose_name=_('Asset Tag'))
    asset_role = tables.Column(accessor='asset.asset_role', verbose_name=_('Role'))
    checked_out_at = tables.DateTimeColumn(format="Y-m-d H:i", verbose_name=_('Checked Out'))
    expected_checkin_date = tables.DateColumn(format="Y-m-d", verbose_name=_('Expected Check-in'))
    checkin_btn = tables.Column(
        verbose_name='',
        orderable=False,
        empty_values=(),
        attrs={
            'th': {'class': 'col-checkout text-nowrap'},
            'td': {'class': 'text-center text-nowrap noprint p-1 col-checkout'}
        },
    )

    class Meta(BaseTable.Meta):
        model = AssetAssignment
        fields = ('asset', 'asset_tag', 'asset_role', 'checked_out_at', 'expected_checkin_date', 'checkin_btn')
        default_columns = ('asset', 'asset_tag', 'asset_role', 'checked_out_at', 'expected_checkin_date', 'checkin_btn')

    def render_asset_role(self, value):
        return value.name if value else "—"

    def render_checkin_btn(self, record):
        request = getattr(self, 'request', None)
        if not request or not self.has_perm(request.user, 'assets.change_asset', record.asset):
            return mark_safe('<span class="text-muted small">—</span>')
        
        url = reverse('assets:asset_checkin', kwargs={'pk': record.asset.pk})
        return format_html(
            '<div class="d-inline-block"><button type="button" class="btn btn-sm btn-outline-success text-success" hx-post="{}" hx-swap="none">'
            '<i class="mdi mdi-keyboard-return"></i> Check-in</button></div>', url
        )


# --- Contact Table ---
class ContactTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:contact_detail', args=[A('pk')], verbose_name=_('Name'))
    title = tables.Column()
    phone = tables.Column()
    email = tables.EmailColumn()
    tenant = tables.Column(verbose_name=_('Tenant'), default=_('Global'))
    tags = TagColumn(url_name='organization:contact_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Contact
        fields = ('pk', 'name', 'title', 'phone', 'email', 'web_url', 'tenant', 'description', 'tags', 'actions')
        default_columns = ('pk', 'name', 'title', 'phone', 'email', 'tenant', 'tags', 'actions')


# --- ContactRole Table ---
class ContactRoleTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:contactrole_detail', args=[A('pk')], verbose_name=_('Name'))
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
    assigned_object_type = tables.Column(accessor='content_type', verbose_name=_('Object Type'))
    assigned_object = tables.Column(linkify=True, verbose_name=_('Assigned Object'))
    priority = tables.Column()
    actions = tables.TemplateColumn(
        template_code='''
        <a href="{% url 'organization:contactassignment_delete' record.pk %}?return_url={{ request.path }}" class="btn btn-sm btn-action btn-action-danger px-2" title="Delete">
            <i class="mdi mdi-trash-can-outline m-0"></i>
        </a>
        ''',
        verbose_name=_('Actions'),
        orderable=False
    )

    class Meta(BaseTable.Meta):
        model = ContactAssignment
        fields = ('contact', 'role', 'assigned_object_type', 'assigned_object', 'priority', 'actions')
        default_columns = ('contact', 'role', 'assigned_object_type', 'assigned_object', 'priority', 'actions')

    def render_assigned_object_type(self, record):
        return record.content_type.model_class()._meta.verbose_name.title()


class RoleTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:role_detail', args=[A('pk')], verbose_name=_('Name'))
    kind = tables.Column(verbose_name=_('Kind'), accessor='scope', orderable=True, empty_values=())
    container = tables.Column(verbose_name=_('Owner'), accessor='owner', orderable=False)
    description = tables.Column()
    member_count = tables.Column(verbose_name=_('Members'), orderable=True, empty_values=[])
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Role
        fields = ('pk', 'name', 'kind', 'container', 'description', 'member_count', 'actions')
        default_columns = ('pk', 'name', 'kind', 'container', 'description', 'member_count', 'actions')

    def render_kind(self, record):
        if record.scope == Role.SCOPE_PROVIDER:
            return format_html('<span class="badge bg-purple-lt text-purple">{}</span>', _('Provider role'))
        return format_html('<span class="badge bg-blue-lt text-blue">{}</span>', _('Tenant role'))

    def render_container(self, value, record):
        owner = record.owner
        if owner is None:
            return '—'
        return format_html('<a href="{}">{}</a>', owner.get_absolute_url(), owner)

    def render_member_count(self, value, record):
        count = getattr(record, 'member_count', 0) or 0
        url = f"{reverse('organization:membership_list')}?role={record.pk}"
        return format_html('<a href="{}">{}</a>', url, count)


class MembershipTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    user = tables.LinkColumn('users:user_detail', args=[A('user.pk')], verbose_name=_('User'))
    kind = tables.Column(verbose_name=_('Kind'), accessor='kind', orderable=False, empty_values=())
    container = tables.Column(verbose_name=_('Container'), accessor='container', orderable=False)
    roles = tables.Column(verbose_name=_('Roles'), orderable=False)
    is_active = tables.BooleanColumn(verbose_name=_('Active'))
    joined_at = tables.DateTimeColumn(format="Y-m-d H:i", verbose_name=_('Joined'))
    actions = ActionsColumn(actions=('edit', 'delete'))

    class Meta(BaseTable.Meta):
        model = Membership
        fields = ('pk', 'user', 'kind', 'container', 'roles', 'is_active', 'joined_at', 'actions')
        default_columns = ('pk', 'user', 'kind', 'container', 'roles', 'is_active', 'joined_at', 'actions')

    def render_kind(self, record):
        if record.provider_id:
            return format_html('<span class="badge bg-purple-lt text-purple">{}</span>', _('Provider staff'))
        return format_html('<span class="badge bg-blue-lt text-blue">{}</span>', _('Tenant member'))

    def render_container(self, value, record):
        owner = record.container
        if owner is None:
            return '—'
        return format_html('<a href="{}">{}</a>', owner.get_absolute_url(), owner)

    def render_roles(self, value, record):
        links = []
        for role in record.roles.all():
            url = reverse('organization:role_detail', kwargs={'pk': role.pk})
            links.append(format_html('<a href="{}">{}</a>', url, role.name))
        return mark_safe(', '.join(links)) if links else _('(none)')


class CostCenterTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:costcenter_detail', args=[A('pk')], verbose_name=_('Name'))
    code = tables.Column(verbose_name=_('Code'))
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant_id')], accessor='tenant', verbose_name=_('Tenant'))
    parent = tables.LinkColumn('organization:costcenter_detail', args=[A('parent_id')], accessor='parent', verbose_name=_('Parent'))
    child_count = CountLinkColumn('organization:costcenter_list', 'parent', verbose_name=_('Sub-units'), orderable=False)
    is_active = tables.BooleanColumn(verbose_name=_('Active'))
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = CostCenter
        fields = ('pk', 'code', 'name', 'tenant', 'parent', 'description', 'child_count', 'is_active', 'actions')
        default_columns = ('pk', 'code', 'name', 'tenant', 'parent', 'child_count', 'is_active', 'actions')


# --- Provider (MSP) Table ---
class ProviderTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('organization:provider_detail', args=[A('pk')], verbose_name=_('Name'))
    slug = tables.Column(verbose_name=_('Slug'))
    description = tables.Column(verbose_name=_('Description'))
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Provider
        fields = ('pk', 'name', 'slug', 'description', 'actions')
        default_columns = ('pk', 'name', 'slug', 'description', 'actions')


