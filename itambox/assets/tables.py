# itambox/assets/tables.py
import django_tables2 as tables
from django_tables2.utils import A  # Alias for Accessor
from .models import Asset, AssetRole, Manufacturer, AssetType, StatusLabel, Depreciation, Supplier, Category, AssetRequest, AssetTagSequence, AssetMaintenance, AssetDisposal, Warranty, AssetReservation
from compliance.models import AssetAudit
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
    audit_due_date = tables.Column(
        verbose_name='Audit Due',
        orderable=False,
        empty_values=(),
    )
    actions = tables.Column(
        verbose_name='',
        orderable=False,
        empty_values=(),
        attrs={
            # Wider variant: the actions cell now also holds the check-out/in button.
            'th': {'class': 'col-actions-wide text-nowrap'},
            'td': {'class': 'text-end text-nowrap noprint p-1 col-actions-wide'}
        },
    )

    class Meta(BaseTable.Meta): # Inherit Meta from BaseTable
        model = Asset
        fields = (
            'pk', 'name', 'asset_tag', 'serial_number', 'asset_type', 'asset_role',
            'status', 'assignee', 'tenant', 'location', 'purchase_date', 'purchase_cost', 'salvage_value', 'order_number', 'supplier', 'tags', 'requestable', 'audit_due_date', 'actions',
        )
        default_columns = (
            'pk', 'name', 'asset_tag', 'serial_number', 'asset_type', 'asset_role',
            'status', 'assignee', 'tenant', 'location', 'purchase_date', 'purchase_cost', 'supplier', 'requestable', 'tags', 'actions',
        )
        order_by = ('name',)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def render_serial_number(self, value):
        return value or "—"
        
    def render_asset_role(self, value):
        return value.name if value else "—"

    def render_status(self, value):
        # .badge-status derives fill/text/border from --status-color and adds
        # the leading dot (light/dark variants handled in _components.scss).
        if value:
            return format_html(
                '<span class="badge badge-status" style="--status-color: #{};">'
                '<span class="badge-status-dot"></span>{}</span>',
                value.color or '6c757d', value.name
            )
        return "—"

    def render_audit_due_date(self, record):
        due = record.audit_due_date
        if due is None:
            return "—"
        date_str = due.strftime("%Y-%m-%d")
        if record.audit_overdue:
            return format_html('<span class="text-danger fw-semibold" title="Overdue">{}</span>', date_str)
        return date_str

    def render_salvage_value(self, value):
        if value is not None:
            return f"${value:,.2f}"
        return "—"

    def value_purchase_date(self, value):
        # Format date if it exists
        return value.strftime("%Y-%m-%d") if value else "—"

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
                f'<a class="btn btn-sm btn-soft-success me-1" href="{restore_url}" '
                f'hx-post="{restore_url}" hx-target="#object-list-dynamic-content" '
                f'hx-confirm="{restore_confirm}" '
                f'title="{restore_title}" aria-label="{restore_title}">'
                f'<i class="mdi mdi-backup-restore"></i></a>'
            )
            purge_btn = (
                f'<a class="btn btn-sm btn-soft-danger" href="{purge_url}" '
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

        if can_edit:
            # Check-out (filled green) / Check-in (outline green), merged into the
            # actions group. Labeled for clarity; HTMX drives the click (CSP-safe).
            # The one colored action in the row, both in the soft family:
            # check-out = soft filled tint (give out), check-in = soft
            # outline (take back). Equal width via .check-action.
            if record.active_assignment:
                checkin_url = reverse('assets:asset_checkin', kwargs={'pk': record.pk})
                html += (
                    '<a class="btn btn-sm btn-soft-outline-success check-action" role="button" style="cursor: pointer" '
                    f'hx-get="{checkin_url}" hx-target="#modal-placeholder" hx-swap="innerHTML" '
                    'title="Check-in" aria-label="Check-in"><i class="mdi mdi-login me-1"></i>Check-in</a>'
                )
            else:
                checkout_url = reverse('assets:asset_checkout_modal', kwargs={'pk': record.pk})
                html += (
                    '<a class="btn btn-sm btn-soft-success check-action" role="button" style="cursor: pointer" '
                    f'hx-get="{checkout_url}" hx-target="#modal-placeholder" hx-swap="innerHTML" '
                    'title="Check-out" aria-label="Check-out"><i class="mdi mdi-logout me-1"></i>Check-out</a>'
                )

        if can_clone:
            clone_url = reverse('assets:asset_clone', kwargs={'pk': record.pk})
            html += f'<a class="btn btn-sm btn-action" href="{clone_url}" title="Copy/Clone"><i class="mdi mdi-content-copy"></i></a>'
            
        changelog_url = reverse('assets:asset_detail', kwargs={'pk': record.pk}) + '?tab=changelog'
        changelog_li = (
            f'<li><a class="dropdown-item" href="{changelog_url}">'
            f'<i class="mdi mdi-history me-1"></i>Changelog</a></li>'
        )

        if can_edit and can_delete:
            edit_url = reverse('assets:asset_update', kwargs={'pk': record.pk})
            del_url = reverse('assets:asset_delete', kwargs={'pk': record.pk})
            html += (
                f'<span class="btn-group dropdown">'
                f'<a class="btn btn-sm btn-action" href="{edit_url}" title="Edit Details"><i class="mdi mdi-pencil-outline"></i></a>'
                f'<a class="btn btn-sm btn-action dropdown-toggle dropdown-toggle-split" type="button" data-bs-toggle="dropdown" aria-expanded="false">'
                f'</a>'
                f'<ul class="dropdown-menu dropdown-menu-end">'
                f'{changelog_li}'
                f'<li><hr class="dropdown-divider"></li>'
                f'<li><a class="dropdown-item text-danger" href="{del_url}"><i class="mdi mdi-trash-can-outline me-1"></i>Delete</a></li>'
                f'</ul></span>'
            )
        elif can_edit:
            edit_url = reverse('assets:asset_update', kwargs={'pk': record.pk})
            html += (
                f'<span class="btn-group dropdown">'
                f'<a class="btn btn-sm btn-action" href="{edit_url}" title="Edit Details"><i class="mdi mdi-pencil-outline"></i></a>'
                f'<a class="btn btn-sm btn-action dropdown-toggle dropdown-toggle-split" type="button" data-bs-toggle="dropdown" aria-expanded="false">'
                f'</a>'
                f'<ul class="dropdown-menu dropdown-menu-end">'
                f'{changelog_li}'
                f'</ul></span>'
            )
        elif can_delete:
            del_url = reverse('assets:asset_delete', kwargs={'pk': record.pk})
            html += f'<a class="btn btn-sm btn-action btn-action-danger" href="{del_url}" title="Delete"><i class="mdi mdi-trash-can-outline"></i></a>'
            
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
class AssetMaintenanceTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    asset = tables.LinkColumn('assets:asset_detail', args=[A('asset__pk')], accessor='asset', verbose_name='Asset')
    title = tables.LinkColumn('assets:assetmaintenance_detail', args=[A('pk')], verbose_name='Title')
    maintenance_type = tables.Column(verbose_name='Type')
    status = tables.Column(verbose_name='Status')
    supplier = tables.Column(accessor='supplier__name', verbose_name='Supplier')
    cost = tables.Column(verbose_name='Cost')
    start_date = tables.DateColumn(format="Y-m-d", verbose_name='Start Date')
    completion_date = tables.DateColumn(format="Y-m-d", verbose_name='Completion Date')
    downtime_days = tables.Column(accessor='downtime_days', verbose_name='Downtime (Days)', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = AssetMaintenance
        fields = ('pk', 'asset', 'title', 'maintenance_type', 'status', 'supplier', 'cost', 'start_date', 'completion_date', 'downtime_days', 'actions')
        default_columns = ('pk', 'asset', 'title', 'maintenance_type', 'status', 'supplier', 'cost', 'start_date', 'completion_date', 'downtime_days', 'actions')

    def render_maintenance_type(self, record):
        return record.get_maintenance_type_display()

    def render_status(self, record):
        return record.get_status_display()

    def render_cost(self, value):
        if value is not None:
            return f"${value:,.2f}"
        return "—"

    def render_downtime_days(self, value):
        if value is not None:
            if value == 0:
                return "Same day"
            return f"{value} day{'s' if value != 1 else ''}"
        return "—"

    def render_supplier(self, value):
        return value or "—"


class AssetDisposalTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    asset = tables.LinkColumn('assets:asset_detail', args=[A('asset__pk')], accessor='asset', verbose_name='Asset')
    disposal_method = tables.Column(verbose_name='Method')
    disposal_date = tables.DateColumn(format="Y-m-d", verbose_name='Disposal Date')
    data_sanitization_method = tables.Column(verbose_name='Sanitization')
    recipient = tables.Column(verbose_name='Recipient')
    proceeds = tables.Column(verbose_name='Proceeds')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = AssetDisposal
        fields = ('pk', 'asset', 'disposal_method', 'disposal_date', 'data_sanitization_method', 'recipient', 'proceeds', 'actions')
        default_columns = ('pk', 'asset', 'disposal_method', 'disposal_date', 'data_sanitization_method', 'recipient', 'proceeds', 'actions')

    def render_disposal_method(self, record):
        return record.get_disposal_method_display()

    def render_data_sanitization_method(self, record):
        return record.get_data_sanitization_method_display()

    def render_recipient(self, value):
        return value or "—"

    def render_proceeds(self, value, record):
        if value is None:
            return "—"
        from extras.templatetags.money import money
        return money(value, record)


from extras.tables import CustomFieldTable, CustomFieldsetTable
from inventory.tables import AccessoryTable, ConsumableTable, KitTable, ComponentAllocationTable

class DepreciationTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('assets:depreciation_detail', args=[A('pk')], verbose_name='Name')
    months = tables.Column(verbose_name='Lifespan (Months)')
    method = tables.Column(verbose_name='Method')
    convention = tables.Column(verbose_name='Convention')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Depreciation
        fields = ('pk', 'name', 'months', 'method', 'convention', 'actions')
        default_columns = ('pk', 'name', 'months', 'method', 'actions')





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
    request_id = tables.LinkColumn('assets:assetrequest_detail', args=[A('pk')], accessor='pk', verbose_name='Req #')
    requester = tables.Column(accessor='requester.username', verbose_name='Requester')
    item = tables.Column(verbose_name='Requested Item', empty_values=(), orderable=False)
    requested_for = tables.Column(verbose_name='Request For', orderable=False, empty_values=())
    status = tables.Column(verbose_name='Status')
    request_date = tables.Column(verbose_name='Request Date')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = AssetRequest
        fields = ('pk', 'request_id', 'requester', 'item', 'requested_for', 'status', 'request_date', 'notes', 'actions')
        default_columns = ('pk', 'request_id', 'requester', 'item', 'requested_for', 'status', 'request_date', 'actions')

    def render_item(self, record):
        if record.asset:
            url = reverse('assets:asset_detail', args=[record.asset_id])
            return format_html('<a href="{}">{} (Asset)</a>', url, record.asset)
        elif record.asset_type:
            url = reverse('assets:assettype_detail', args=[record.asset_type_id])
            if getattr(record, 'is_group', False) or getattr(record, 'qty', 1) > 1:
                return format_html('<a href="{}">{}x {} (Asset Type)</a>', url, record.qty, record.asset_type)
            return format_html('<a href="{}">{} (Asset Type)</a>', url, record.asset_type)
        elif record.component:
            url = reverse('inventory:component_detail', args=[record.component_id])
            return format_html('<a href="{}">{} (Component, x{})</a>', url, record.component, record.qty)
        elif record.accessory:
            url = reverse('inventory:accessory_detail', args=[record.accessory_id])
            return format_html('<a href="{}">{} (Accessory, x{})</a>', url, record.accessory, record.qty)
        elif record.consumable:
            url = reverse('inventory:consumable_detail', args=[record.consumable_id])
            return format_html('<a href="{}">{} (Consumable, x{})</a>', url, record.consumable, record.qty)
        return "—"

    def render_requested_for(self, value, record):
        target = record.assigned_target
        if not target:
            return "Myself"
        return str(target)

    def render_status(self, value):
        status_classes = {
            'pending': 'bg-warning text-warning-fg',
            'approved': 'bg-info text-info-fg',
            'fulfilled': 'bg-success text-success-fg',
            'denied': 'bg-danger text-danger-fg',
            'cancelled': 'bg-secondary text-secondary-fg',
        }
        badge_class = status_classes.get(value, 'bg-secondary text-secondary-fg')
        display = value.title()
        return format_html('<span class="badge {}">{}</span>', badge_class, display)


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
    session = tables.LinkColumn('compliance:auditsession_detail', args=[A('session_id')], verbose_name='Campaign')
    auditor = tables.Column(accessor='auditor.username', verbose_name='Auditor')
    location = tables.LinkColumn('organization:location_detail', args=[A('location_id')], accessor='location.name', verbose_name='Location')
    status = tables.Column(verbose_name='Status')
    verification_method = tables.Column(verbose_name='Method')
    notes = tables.Column(verbose_name='Notes')

    class Meta(BaseTable.Meta):
        model = AssetAudit
        fields = ('pk', 'timestamp', 'session', 'auditor', 'location', 'status', 'verification_method', 'notes')
        default_columns = ('pk', 'timestamp', 'session', 'auditor', 'location', 'status', 'verification_method', 'notes')


class WarrantyTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    asset = tables.LinkColumn('assets:asset_detail', args=[A('asset__pk')], accessor='asset', verbose_name='Asset')
    warranty_type = tables.Column(verbose_name='Type')
    provider = tables.Column(verbose_name='Provider')
    start_date = tables.DateColumn(format="Y-m-d", verbose_name='Start Date')
    end_date = tables.DateColumn(format="Y-m-d", verbose_name='End Date')
    cost = tables.Column(verbose_name='Cost')
    is_active = tables.BooleanColumn(verbose_name='Active', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Warranty
        fields = ('pk', 'asset', 'warranty_type', 'provider', 'start_date', 'end_date', 'cost', 'is_active', 'actions')
        default_columns = ('pk', 'asset', 'warranty_type', 'provider', 'start_date', 'end_date', 'cost', 'is_active', 'actions')

    def render_warranty_type(self, record):
        return record.get_warranty_type_display()

    def render_cost(self, value, record):
        if value is None:
            return '—'
        try:
            from extras.templatetags.money import money
            return money(value, record)
        except Exception:
            return f'{value}'

    def render_is_active(self, record):
        return record.is_active


class AssetReservationTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    asset = tables.LinkColumn('assets:asset_detail', args=[A('asset__pk')], accessor='asset', verbose_name='Asset')
    reserved_for = tables.Column(verbose_name='Reserved For')
    start_date = tables.DateColumn(format="Y-m-d", verbose_name='Start Date')
    end_date = tables.DateColumn(format="Y-m-d", verbose_name='End Date')
    status = tables.Column(verbose_name='Status')
    purpose = tables.Column(verbose_name='Purpose')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = AssetReservation
        fields = ('pk', 'asset', 'reserved_for', 'start_date', 'end_date', 'status', 'purpose', 'actions')
        default_columns = ('pk', 'asset', 'reserved_for', 'start_date', 'end_date', 'status', 'purpose', 'actions')

    def render_status(self, record):
        return record.get_status_display()

    def render_reserved_for(self, value):
        return value or '—'


