# itambox/assets/tables.py
import django_tables2 as tables
from django_tables2.utils import A  # Alias for Accessor
from .models import Asset, AssetRole, Manufacturer, AssetType, StatusLabel, Depreciation, Supplier, Category, AssetRequest, AssetTagSequence, AssetAudit
from core.tables import ActionsColumn, AssigneeColumn, BaseTable, ToggleColumn
from extras.tables import TagColumn # Import TagColumn
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils.html import format_html

class AssetTable(BaseTable): # Inherit from BaseTable
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('assets:asset_detail', args=[A('pk')], verbose_name='Name')
    manufacturer = tables.Column(accessor='asset_type.manufacturer', linkify=True, verbose_name='Manufacturer')
    model = tables.Column(accessor='asset_type.model', linkify=True, verbose_name='Model')
    asset_type = tables.LinkColumn('assets:assettype_detail', args=[A('asset_type_id')], verbose_name='Asset Type')
    assignee = AssigneeColumn(
        location_field='location',
        assignment_model_path='assets.AssetAssignment',
    )
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant_id')], accessor='tenant.name', verbose_name='Tenant')
    location = tables.LinkColumn('organization:location_detail', args=[A('location_id')], accessor='location.name', verbose_name='Location')
    supplier = tables.LinkColumn('assets:supplier_detail', args=[A('supplier_id')], accessor='supplier.name', verbose_name='Supplier')

    tags = TagColumn(url_name='assets:asset_list')
    requestable = tables.BooleanColumn(verbose_name='Requestable', yesno='Yes,No')
    checkout_checkin = tables.Column(
        verbose_name='',
        orderable=False,
        empty_values=(),
        attrs={
            'th': {'class': 'col-checkout text-nowrap'},
            'td': {'class': 'text-center text-nowrap noprint p-1 col-checkout'}
        },
    )
    actions = tables.Column(
        verbose_name='',
        orderable=False,
        empty_values=(),
        attrs={
            'th': {'class': 'col-actions text-nowrap'},
            'td': {'class': 'text-end text-nowrap noprint p-1 col-actions'}
        },
    )

    class Meta(BaseTable.Meta): # Inherit Meta from BaseTable
        model = Asset
        fields = (
            'pk', 'name', 'asset_tag', 'serial_number', 'asset_type', 'asset_role', 
            'status', 'assignee', 'tenant', 'location', 'purchase_date', 'purchase_cost', 'salvage_value', 'order_number', 'supplier', 'tags', 'requestable', 'checkout_checkin', 'actions',
        )
        default_columns = (
            'pk', 'name', 'asset_tag', 'serial_number', 'asset_type', 'asset_role', 
            'status', 'assignee', 'tenant', 'location', 'purchase_date', 'purchase_cost', 'supplier', 'requestable', 'tags', 'checkout_checkin', 'actions',
        )
        order_by = ('name',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def render_serial_number(self, value):
        return value or "—"
        
    def render_asset_role(self, value):
        return value.name if value else "—"

    def render_status(self, value):
        if value:
            return format_html(
                '<span class="badge" style="background-color: #{}1a; color: #{}; border: 1px solid #{}33;">{}</span>',
                value.color or '6c757d', value.color or '6c757d', value.color or '6c757d', value.name
            )
        return "—"

    def render_salvage_value(self, value):
        if value is not None:
            return f"${value:,.2f}"
        return "—"

    def value_purchase_date(self, value):
        # Format date if it exists
        return value.strftime("%Y-%m-%d") if value else "—"

    def render_checkout_checkin(self, record):
        if getattr(record, 'deleted_at', None) is not None:
            return mark_safe('<span class="text-muted small">—</span>')
        request = getattr(self, 'request', None)
        if not request or not self.has_perm(request.user, 'assets.change_asset', record):
            return mark_safe('<span class="text-muted small">—</span>')
        
        if record.active_assignment:
            url = reverse('assets:asset_checkin', kwargs={'pk': record.pk})
            return format_html(
                '<div class="d-inline-block"><a class="btn btn-sm btn-success" hx-get="{}" hx-target="#modal-placeholder" hx-swap="innerHTML" href="javascript:void(0)">'
                '<i class="mdi mdi-keyboard-return"></i> Check-in</a></div>', url
            )
        else:
            url = reverse('assets:asset_checkout_modal', kwargs={'pk': record.pk})
            return format_html(
                '<div class="d-inline-block"><a class="btn btn-sm btn-primary" hx-get="{}" hx-target="#modal-placeholder" hx-swap="innerHTML" href="javascript:void(0)">'
                '<i class="mdi mdi-keyboard-tab-reverse"></i> Check-out</a></div>', url
            )

    def render_actions(self, record):
        if getattr(record, 'deleted_at', None) is not None:
            from django.contrib.contenttypes.models import ContentType
            from django.utils.translation import gettext as _
            ct = ContentType.objects.get_for_model(record)
            
            restore_url = reverse('object_restore', kwargs={'content_type_id': ct.pk, 'object_id': record.pk})
            purge_url = reverse('object_purge', kwargs={'content_type_id': ct.pk, 'object_id': record.pk})
            
            restore_title = _("Restore")
            purge_title = _("Delete Permanently")
            
            restore_confirm = _("Are you sure you want to restore this asset?")
            purge_confirm = _("Are you sure you want to PERMANENTLY delete this asset? This action cannot be undone!")

            restore_btn = (
                f'<a class="btn btn-sm btn-success me-1" href="{restore_url}" '
                f'hx-post="{restore_url}" hx-target="#object-list-dynamic-content" '
                f'hx-confirm="{restore_confirm}" '
                f'title="{restore_title}" aria-label="{restore_title}">'
                f'<i class="mdi mdi-backup-restore"></i></a>'
            )
            purge_btn = (
                f'<a class="btn btn-sm btn-danger" href="{purge_url}" '
                f'hx-post="{purge_url}" hx-target="#object-list-dynamic-content" '
                f'hx-confirm="{purge_confirm}" '
                f'title="{purge_title}" aria-label="{purge_title}">'
                f'<i class="mdi mdi-delete-forever"></i></a>'
            )
            
            return mark_safe(restore_btn + purge_btn)

        request = getattr(self, 'request', None)
        if not request:
            return ""

        can_edit = self.has_perm(request.user, 'assets.change_asset', record)
        can_delete = self.has_perm(request.user, 'assets.delete_asset', record)
        can_clone = self.has_perm(request.user, 'assets.add_asset', record)
        
        if not can_edit and not can_delete and not can_clone:
            return ""

        html = '<div class="d-flex align-items-center gap-1 justify-content-end">'
        
        if can_clone:
            clone_url = reverse('assets:asset_clone', kwargs={'pk': record.pk})
            html += f'<a class="btn btn-sm btn-warning" href="{clone_url}" title="Copy/Clone"><i class="mdi mdi-content-copy"></i></a>'
            
        if can_edit and can_delete:
            edit_url = reverse('assets:asset_update', kwargs={'pk': record.pk})
            del_url = reverse('assets:asset_delete', kwargs={'pk': record.pk})
            html += (
                f'<span class="btn-group dropdown">'
                f'<a class="btn btn-sm btn-primary" href="{edit_url}" title="Edit Details"><i class="mdi mdi-pencil-outline"></i></a>'
                f'<a class="btn btn-sm btn-primary dropdown-toggle dropdown-toggle-split" type="button" data-bs-toggle="dropdown" aria-expanded="false">'
                f'</a>'
                f'<ul class="dropdown-menu dropdown-menu-end">'
                f'<li><a class="dropdown-item text-danger" href="{del_url}"><i class="mdi mdi-trash-can-outline me-1"></i>Delete</a></li>'
                f'</ul></span>'
            )
        elif can_edit:
            edit_url = reverse('assets:asset_update', kwargs={'pk': record.pk})
            html += f'<a class="btn btn-sm btn-primary" href="{edit_url}" title="Edit Details"><i class="mdi mdi-pencil-outline"></i></a>'
        elif can_delete:
            del_url = reverse('assets:asset_delete', kwargs={'pk': record.pk})
            html += f'<a class="btn btn-sm btn-danger" href="{del_url}" title="Delete"><i class="mdi mdi-trash-can-outline"></i></a>'
            
        html += '</div>'
        return mark_safe(html)

class StatusLabelTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('assets:statuslabel_detail', args=[A('pk')], verbose_name='Name')
    type = tables.Column(verbose_name='Meta Type')
    color = tables.Column(verbose_name='Color', orderable=False)
    asset_count = tables.Column(verbose_name='Asset Count', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = StatusLabel
        fields = ('pk', 'name', 'type', 'color', 'description', 'asset_count', 'actions')
        default_columns = ('pk', 'name', 'type', 'color', 'asset_count', 'description', 'actions')

    def render_color(self, value):
        if value:
            return format_html(
                '<span class="badge" style="background-color: #{};">&nbsp;</span> #{}',
                value, value
            )
        return "—"

    def render_type(self, value):
        return value.title() if value else "—"

    def render_asset_count(self, value, record=None):
        return value or 0

class AssetRoleTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('assets:assetrole_detail', args=[A('pk')], verbose_name='Name')
    color = tables.Column(verbose_name='Color', orderable=False)
    asset_count = tables.Column(verbose_name='Asset Count', orderable=False)
    tags = TagColumn(url_name='assets:assetrole_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta): # Inherit Meta
        model = AssetRole
        fields = ('pk', 'name', 'color', 'description', 'asset_count', 'tags', 'actions')
        default_columns = ('pk', 'name', 'color', 'asset_count', 'description', 'tags', 'actions')

    def render_asset_count(self, value, record=None):
        return value or 0
        
    def render_color(self, value):
        if value:
            return mark_safe(f'<span class="badge" style="background-color: #{value};">&nbsp;</span> #{value}')
        return "—"

class ManufacturerTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn()
    asset_type_count = tables.Column(
        verbose_name='Asset Types',
        linkify=True,
        accessor='asset_type_count'
    )
    asset_count = tables.Column(
        verbose_name='Assets'
    )
    # description = tables.Column()
    # slug = tables.Column()
    tags = TagColumn(url_name='assets:manufacturer_list')
    actions = ActionsColumn()

    def render_asset_count(self, value, record=None):
        return value or 0

    def render_asset_type_count(self, value, record):
        # Customize the link for asset_type_count
        url = reverse('assets:assettype_list') + f'?manufacturer_id={record.pk}'
        return format_html('<a href="{}">{}</a>', url, value)

    class Meta(BaseTable.Meta):
        model = Manufacturer
        fields = (
            'pk', 'name', 'asset_type_count', 'asset_count', 'description', 'tags', 'actions'
        )
        default_columns = (
            'pk', 'name', 'asset_type_count', 'asset_count', 'description', 'tags', 'actions'
        )

class AssetTypeTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    manufacturer = tables.Column(linkify=True) # Linkify using default get_absolute_url
    model = tables.LinkColumn('assets:assettype_detail', args=[A('pk')], verbose_name='Model')
    eol_months = tables.Column(verbose_name='EOL (Months)')
    created_at = tables.DateTimeColumn(format="Y-m-d") # Explicitly add 'created'
    last_updated = tables.DateTimeColumn(format="Y-m-d H:i") # Explicitly add 'last_updated'
    tags = TagColumn(url_name='assets:assettype_list')
    requestable = tables.BooleanColumn(verbose_name='Requestable', yesno='Yes,No')
    actions = ActionsColumn() # Add actions column

    class Meta(BaseTable.Meta):
        model = AssetType
        # Add 'created' and 'last_updated' to fields
        fields = ('pk', 'manufacturer', 'model', 'part_number', 'eol_months', 'created', 'last_updated', 'tags', 'requestable', 'actions') 
        # Keep default columns as before, or add created/last_updated if desired
        default_columns = ('pk', 'manufacturer', 'model', 'part_number', 'eol_months', 'created', 'last_updated', 'requestable', 'tags', 'actions')
        order_by = ('manufacturer', 'model')

    def render_eol_months(self, value):
        if value is not None:
            return f"{value} month{'s' if value != 1 else ''}"
        return "—"
from compliance.tables import AssetMaintenanceTable
from extras.tables import CustomFieldTable, CustomFieldsetTable
from inventory.tables import AccessoryTable, ConsumableTable, KitTable, ComponentAllocationTable

class DepreciationTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('assets:depreciation_detail', args=[A('pk')], verbose_name='Name')
    months = tables.Column(verbose_name='Lifespan (Months)')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Depreciation
        fields = ('pk', 'name', 'months', 'actions')
        default_columns = ('pk', 'name', 'months', 'actions')





class SupplierTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('assets:supplier_detail', args=[A('pk')], verbose_name='Name')
    tags = TagColumn(url_name='assets:supplier_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Supplier
        fields = ('pk', 'name', 'website', 'contact_email', 'contact_phone', 'contact_name', 'tags', 'actions')
        default_columns = ('pk', 'name', 'website', 'contact_email', 'contact_phone', 'contact_name', 'tags', 'actions')


class CategoryTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('assets:category_detail', args=[A('pk')], verbose_name='Name')
    color = tables.Column(verbose_name='Color', orderable=False)
    tags = TagColumn(url_name='assets:category_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Category
        fields = ('pk', 'name', 'color', 'tags', 'actions')
        default_columns = ('pk', 'name', 'color', 'tags', 'actions')

    def render_color(self, value):
        if value:
            return mark_safe(f'<span class="badge" style="background-color: #{value};">&nbsp;</span> #{value}')
        return "—"


class AssetRequestTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    requester = tables.Column(accessor='requester.username', verbose_name='Requester')
    asset = tables.LinkColumn('assets:asset_detail', args=[A('asset_id')], verbose_name='Asset')

    asset_type = tables.Column(accessor='asset_type.model', verbose_name='Asset Type')
    status = tables.Column(verbose_name='Status')
    request_date = tables.Column(verbose_name='Request Date')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = AssetRequest
        fields = ('pk', 'requester', 'asset', 'asset_type', 'status', 'request_date', 'notes', 'actions')
        default_columns = ('pk', 'requester', 'asset', 'asset_type', 'status', 'request_date', 'actions')


class AssetTagSequenceTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    prefix = tables.LinkColumn('assets:assettagsequence_detail', args=[A('pk')], verbose_name='Prefix')
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant_id')], accessor='tenant.name', verbose_name='Tenant')
    category = tables.LinkColumn('assets:category_detail', args=[A('category_id')], accessor='category.name', verbose_name='Category')
    next_value = tables.Column(verbose_name='Next Value')
    zero_padding = tables.Column(verbose_name='Zero Padding')
    is_active = tables.BooleanColumn(verbose_name='Active')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = AssetTagSequence
        fields = ('pk', 'prefix', 'tenant', 'category', 'next_value', 'zero_padding', 'is_active', 'actions')
        default_columns = ('pk', 'prefix', 'tenant', 'category', 'next_value', 'zero_padding', 'is_active', 'actions')


class AssetAuditTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    timestamp = tables.DateTimeColumn(format="Y-m-d H:i", verbose_name="Timestamp")
    session = tables.LinkColumn('assets:auditsession_detail', args=[A('session_id')], verbose_name='Campaign')
    auditor = tables.Column(accessor='auditor.username', verbose_name='Auditor')
    location = tables.LinkColumn('organization:location_detail', args=[A('location_id')], accessor='location.name', verbose_name='Location')
    status = tables.Column(verbose_name='Status')
    verification_method = tables.Column(verbose_name='Method')
    notes = tables.Column(verbose_name='Notes')

    class Meta(BaseTable.Meta):
        model = AssetAudit
        fields = ('pk', 'timestamp', 'session', 'auditor', 'location', 'status', 'verification_method', 'notes')
        default_columns = ('pk', 'timestamp', 'session', 'auditor', 'location', 'status', 'verification_method', 'notes')


