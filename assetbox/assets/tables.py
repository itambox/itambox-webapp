# assetbox/assets/tables.py
import django_tables2 as tables
from django_tables2.utils import A  # Alias for Accessor
from .models import Asset, AssetRole, Manufacturer, AssetType, ComponentType, ComponentInstance, Accessory, AccessoryAssignment, Consumable, ConsumableAssignment, StatusLabel, AssetMaintenance, CustomField, CustomFieldset, Depreciation, Kit
from core.tables import ActionsColumn, BaseTable
from extras.tables import TagColumn # Import TagColumn
from django.urls import reverse, NoReverseMatch
from django.utils.safestring import mark_safe
from django.contrib.contenttypes.models import ContentType
from organization.models import AssetHolderAssignment
from django.utils.html import format_html

class AssetTable(BaseTable): # Inherit from BaseTable
    pk = tables.CheckBoxColumn(accessor='pk', attrs = { "th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('assets:asset_detail', args=[A('pk')], verbose_name='Name')
    manufacturer = tables.Column(accessor='asset_type.manufacturer', linkify=True, verbose_name='Manufacturer')
    model = tables.Column(accessor='asset_type.model', linkify=True, verbose_name='Model')
    asset_type = tables.LinkColumn('assets:assettype_detail', args=[A('asset_type.pk')], verbose_name='Asset Type')
    assignee = tables.Column(accessor='_assignee_display', verbose_name='Assignee', orderable=False)
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant.pk')], accessor='tenant.name', verbose_name='Tenant')
    location = tables.LinkColumn('organization:location_detail', args=[A('location.pk')], accessor='location.name', verbose_name='Location')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta): # Inherit Meta from BaseTable
        model = Asset
        fields = (
            'pk', 'name', 'asset_tag', 'serial_number', 'asset_type', 'asset_role', 
            'status', 'assignee', 'tenant', 'location', 'purchase_date', 'purchase_cost', 'salvage_value', 'order_number', 'supplier', 'actions',
        )
        default_columns = (
            'pk', 'name', 'asset_tag', 'asset_type', 'asset_role', 
            'status', 'assignee', 'tenant', 'location', 'salvage_value', 'actions',
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

class StatusLabelTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
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
    pk = tables.CheckBoxColumn(accessor='pk', attrs = { "th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('assets:assetrole_detail', args=[A('pk')], verbose_name='Name')
    color = tables.Column(verbose_name='Color', orderable=False)
    asset_count = tables.Column(verbose_name='Asset Count', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta): # Inherit Meta
        model = AssetRole
        fields = ('pk', 'name', 'color', 'description', 'asset_count', 'actions')
        default_columns = ('pk', 'name', 'color', 'asset_count', 'description', 'actions')

    def render_asset_count(self, value, record=None):
        return value or 0
        
    def render_color(self, value):
        if value:
            return mark_safe(f'<span class="badge" style="background-color: #{value};">&nbsp;</span> #{value}')
        return "—"

class ManufacturerTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"onclick": "toggle(this)"}})
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
            'pk', 'name', 'asset_type_count', 'asset_count', 'description', 'actions'
        )

class AssetTypeTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk')
    manufacturer = tables.Column(linkify=True) # Linkify using default get_absolute_url
    model = tables.LinkColumn('assets:assettype_detail', args=[A('slug')], verbose_name='Model')
    eol_months = tables.Column(verbose_name='EOL (Months)')
    created_at = tables.DateTimeColumn(format="Y-m-d") # Explicitly add 'created'
    last_updated = tables.DateTimeColumn(format="Y-m-d H:i") # Explicitly add 'last_updated'
    actions = ActionsColumn() # Add actions column

    class Meta(BaseTable.Meta):
        model = AssetType
        # Add 'created' and 'last_updated' to fields
        fields = ('pk', 'manufacturer', 'model', 'part_number', 'eol_months', 'created', 'last_updated', 'actions') 
        # Keep default columns as before, or add created/last_updated if desired
        default_columns = ('pk', 'manufacturer', 'model', 'part_number', 'eol_months', 'actions')
        # *** Explicitly set default order_by ***
        order_by = ('manufacturer', 'model')

    def render_eol_months(self, value):
        if value is not None:
            return f"{value} month{'s' if value != 1 else ''}"
        return "—"

    # def render_asset_count(self, record):
    #    return Asset.objects.filter(asset_type=record).count() # Example if Asset links to AssetType
    #    return Asset.objects.filter(asset_type=record).count() # Example if Asset links to AssetType


class ComponentTypeTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('assets:componenttype_detail', args=[A('pk')], verbose_name='Name')
    manufacturer = tables.Column(linkify=True)
    category = tables.Column(verbose_name='Category')
    part_number = tables.Column(verbose_name='Part Number')
    specs = tables.Column(verbose_name='Specifications')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = ComponentType
        fields = ('pk', 'name', 'manufacturer', 'category', 'part_number', 'specs', 'actions')
        default_columns = ('pk', 'name', 'manufacturer', 'category', 'part_number', 'specs', 'actions')


class ComponentInstanceTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    component_type = tables.LinkColumn('assets:componentinstance_detail', args=[A('pk')], verbose_name='Component')
    serial_number = tables.Column(verbose_name='Serial Number')
    parent_asset = tables.LinkColumn('assets:asset_detail', args=[A('parent_asset__pk')], verbose_name='Asset Installed In')
    status = tables.Column(verbose_name='Status')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = ComponentInstance
        fields = ('pk', 'component_type', 'serial_number', 'parent_asset', 'status', 'actions')
        default_columns = ('pk', 'component_type', 'serial_number', 'parent_asset', 'status', 'actions')


class AccessoryTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('assets:accessory_detail', args=[A('pk')], verbose_name='Name')
    manufacturer = tables.Column(linkify=True)
    category = tables.Column(verbose_name='Category')
    part_number = tables.Column(verbose_name='Part Number')
    qty = tables.Column(verbose_name='Total Stock')
    checked_out_qty = tables.Column(accessor='checked_out_qty', verbose_name='Checked Out')
    remaining_qty = tables.Column(accessor='remaining_qty', verbose_name='Available')
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant.pk')], accessor='tenant.name', verbose_name='Tenant')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Accessory
        fields = ('pk', 'name', 'manufacturer', 'tenant', 'category', 'part_number', 'qty', 'checked_out_qty', 'remaining_qty', 'actions')
        default_columns = ('pk', 'name', 'manufacturer', 'tenant', 'category', 'qty', 'checked_out_qty', 'remaining_qty', 'actions')

    def render_remaining_qty(self, value, record):
        if value <= 0:
            return format_html('<span class="badge bg-danger-lt text-danger font-weight-bold">0 (Empty)</span>')
        elif value < record.min_qty:
            return format_html('<span class="badge bg-warning-lt text-warning font-weight-bold">{} (Low)</span>', value)
        return value


class AccessoryAssignmentTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk')
    accessory = tables.LinkColumn('assets:accessory_detail', args=[A('accessory__pk')], verbose_name='Accessory')
    assigned_to = tables.Column(verbose_name='Assigned To', orderable=False)
    qty = tables.Column(verbose_name='Qty')
    assigned_date = tables.DateTimeColumn(format="Y-m-d H:i", verbose_name='Date')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = AccessoryAssignment
        fields = ('pk', 'accessory', 'assigned_to', 'qty', 'assigned_date', 'actions')
        default_columns = ('pk', 'accessory', 'assigned_to', 'qty', 'assigned_date', 'actions')

    def render_assigned_to(self, record):
        if record.assigned_holder:
            url = reverse('organization:assetholder_detail', kwargs={'pk': record.assigned_holder.pk})
            return format_html('<a href="{}">Holder: {}</a>', url, record.assigned_holder)
        elif record.assigned_location:
            url = reverse('organization:location_detail', kwargs={'pk': record.assigned_location.pk})
            return format_html('<a href="{}">Location: {}</a>', url, record.assigned_location)
        return "—"


class ConsumableTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('assets:consumable_detail', args=[A('pk')], verbose_name='Name')
    manufacturer = tables.Column(linkify=True)
    category = tables.Column(verbose_name='Category')
    part_number = tables.Column(verbose_name='Part Number')
    qty = tables.Column(verbose_name='Total Qty')
    consumed_qty = tables.Column(accessor='consumed_qty', verbose_name='Consumed')
    remaining_qty = tables.Column(accessor='remaining_qty', verbose_name='Available')
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant.pk')], accessor='tenant.name', verbose_name='Tenant')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Consumable
        fields = ('pk', 'name', 'manufacturer', 'tenant', 'category', 'part_number', 'qty', 'consumed_qty', 'remaining_qty', 'actions')
        default_columns = ('pk', 'name', 'manufacturer', 'tenant', 'category', 'qty', 'consumed_qty', 'remaining_qty', 'actions')

    def render_remaining_qty(self, value, record):
        if value <= 0:
            return format_html('<span class="badge bg-danger-lt text-danger font-weight-bold">0 (Out of Stock)</span>')
        elif value < record.min_qty:
            return format_html('<span class="badge bg-warning-lt text-warning font-weight-bold">{} (Low Stock)</span>', value)
        return value


class ConsumableAssignmentTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk')
    consumable = tables.LinkColumn('assets:consumable_detail', args=[A('consumable__pk')], verbose_name='Consumable')
    assigned_to = tables.Column(verbose_name='Assigned To', orderable=False)
    qty = tables.Column(verbose_name='Qty')
    assigned_date = tables.DateTimeColumn(format="Y-m-d H:i", verbose_name='Date')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = ConsumableAssignment
        fields = ('pk', 'consumable', 'assigned_to', 'qty', 'assigned_date', 'actions')
        default_columns = ('pk', 'consumable', 'assigned_to', 'qty', 'assigned_date', 'actions')

    def render_assigned_to(self, record):
        if record.assigned_holder:
            url = reverse('organization:assetholder_detail', kwargs={'pk': record.assigned_holder.pk})
            return format_html('<a href="{}">Holder: {}</a>', url, record.assigned_holder)
        elif record.assigned_location:
            url = reverse('organization:location_detail', kwargs={'pk': record.assigned_location.pk})
            return format_html('<a href="{}">Location: {}</a>', url, record.assigned_location)
        return "—"


class AssetMaintenanceTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    asset = tables.LinkColumn('assets:asset_detail', args=[A('asset__pk')], accessor='asset', verbose_name='Asset')
    maintenance_type = tables.Column(verbose_name='Type')
    supplier = tables.Column(verbose_name='Supplier')
    cost = tables.Column(verbose_name='Cost')
    start_date = tables.DateColumn(format="Y-m-d", verbose_name='Start Date')
    completion_date = tables.DateColumn(format="Y-m-d", verbose_name='Completion Date')
    downtime_days = tables.Column(accessor='downtime_days', verbose_name='Downtime (Days)', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = AssetMaintenance
        fields = ('pk', 'asset', 'maintenance_type', 'supplier', 'cost', 'start_date', 'completion_date', 'downtime_days', 'actions')
        default_columns = ('pk', 'asset', 'maintenance_type', 'supplier', 'cost', 'start_date', 'completion_date', 'downtime_days', 'actions')

    def render_maintenance_type(self, record):
        return record.get_maintenance_type_display()

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


class CustomFieldTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('assets:customfield_detail', args=[A('pk')], verbose_name='Name')
    label = tables.Column(verbose_name='Label')
    field_type = tables.Column(verbose_name='Field Type')
    required = tables.BooleanColumn(verbose_name='Required')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = CustomField
        fields = ('pk', 'name', 'label', 'field_type', 'required', 'actions')
        default_columns = ('pk', 'name', 'label', 'field_type', 'required', 'actions')


class CustomFieldsetTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('assets:customfieldset_detail', args=[A('pk')], verbose_name='Name')
    fields_count = tables.Column(verbose_name='Fields Count', orderable=False)
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = CustomFieldset
        fields = ('pk', 'name', 'fields_count', 'actions')
        default_columns = ('pk', 'name', 'fields_count', 'actions')

    def render_fields_count(self, value, record=None):
        return value or 0


class DepreciationTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('assets:depreciation_detail', args=[A('pk')], verbose_name='Name')
    months = tables.Column(verbose_name='Lifespan (Months)')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Depreciation
        fields = ('pk', 'name', 'months', 'actions')
        default_columns = ('pk', 'name', 'months', 'actions')


class KitTable(BaseTable):
    pk = tables.CheckBoxColumn(accessor='pk', attrs={"th__input": {"title": "Select all rows"}})
    name = tables.LinkColumn('assets:kit_detail', args=[A('pk')], verbose_name='Name')
    description = tables.Column(verbose_name='Description')
    item_count = tables.Column(accessor='item_count', verbose_name='Items Count', orderable=False)
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant.pk')], accessor='tenant.name', verbose_name='Tenant')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Kit
        fields = ('pk', 'name', 'tenant', 'description', 'item_count', 'actions')
        default_columns = ('pk', 'name', 'tenant', 'description', 'item_count', 'actions')

