import django_tables2 as tables
from django.urls import reverse
from django.utils.html import format_html
from django_tables2.utils import A

from core.tables import BaseTable, ToggleColumn, ActionsColumn
from extras.tables import TagColumn
from .models import Accessory, AccessoryAssignment, AccessoryStock, Consumable, ConsumableAssignment, ConsumableStock, Kit


class AccessoryTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('inventory:accessory_detail', args=[A('pk')], verbose_name='Name')
    manufacturer = tables.Column(linkify=True)
    category = tables.Column(verbose_name='Category')
    part_number = tables.Column(verbose_name='Part Number')
    total_stock = tables.Column(accessor='total_stock', verbose_name='Total Stock')
    checked_out_qty = tables.Column(accessor='checked_out_qty', verbose_name='Checked Out')
    available = tables.Column(accessor='available', verbose_name='Available')
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant.pk')], accessor='tenant.name', verbose_name='Tenant')
    tags = TagColumn(url_name='inventory:accessory_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Accessory
        fields = ('pk', 'name', 'manufacturer', 'tenant', 'category', 'part_number', 'total_stock', 'checked_out_qty', 'available', 'tags', 'actions')
        default_columns = ('pk', 'name', 'manufacturer', 'tenant', 'category', 'total_stock', 'checked_out_qty', 'available', 'tags', 'actions')

    def render_available(self, value, record):
        if value <= 0:
            return format_html('<span class="badge bg-danger-lt text-danger font-weight-bold">0 (Empty)</span>')
        elif value < record.min_qty:
            return format_html('<span class="badge bg-warning-lt text-warning font-weight-bold">{} (Low)</span>', value)
        return value


class AccessoryStockTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    accessory = tables.LinkColumn('inventory:accessory_detail', args=[A('accessory.pk')], verbose_name='Accessory')
    location = tables.LinkColumn('organization:location_detail', args=[A('location.pk')], verbose_name='Location')
    qty = tables.Column(verbose_name='Quantity')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = AccessoryStock
        fields = ('pk', 'accessory', 'location', 'qty', 'actions')
        default_columns = ('pk', 'accessory', 'location', 'qty', 'actions')


class AccessoryAssignmentTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    accessory = tables.LinkColumn('inventory:accessory_detail', args=[A('accessory__pk')], verbose_name='Accessory')
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
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('inventory:consumable_detail', args=[A('pk')], verbose_name='Name')
    manufacturer = tables.Column(linkify=True)
    category = tables.Column(verbose_name='Category')
    part_number = tables.Column(verbose_name='Part Number')
    total_stock = tables.Column(accessor='total_stock', verbose_name='Total Qty')
    consumed_qty = tables.Column(accessor='consumed_qty', verbose_name='Consumed')
    available = tables.Column(accessor='available', verbose_name='Available')
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant.pk')], accessor='tenant.name', verbose_name='Tenant')
    tags = TagColumn(url_name='inventory:consumable_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Consumable
        fields = ('pk', 'name', 'manufacturer', 'tenant', 'category', 'part_number', 'total_stock', 'consumed_qty', 'available', 'tags', 'actions')
        default_columns = ('pk', 'name', 'manufacturer', 'tenant', 'category', 'total_stock', 'consumed_qty', 'available', 'tags', 'actions')

    def render_available(self, value, record):
        if value <= 0:
            return format_html('<span class="badge bg-danger-lt text-danger font-weight-bold">0 (Out of Stock)</span>')
        elif value < record.min_qty:
            return format_html('<span class="badge bg-warning-lt text-warning font-weight-bold">{} (Low Stock)</span>', value)
        return value


class ConsumableStockTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    consumable = tables.LinkColumn('inventory:consumable_detail', args=[A('consumable.pk')], verbose_name='Consumable')
    location = tables.LinkColumn('organization:location_detail', args=[A('location.pk')], verbose_name='Location')
    qty = tables.Column(verbose_name='Quantity')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = ConsumableStock
        fields = ('pk', 'consumable', 'location', 'qty', 'actions')
        default_columns = ('pk', 'consumable', 'location', 'qty', 'actions')


class ConsumableAssignmentTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    consumable = tables.LinkColumn('inventory:consumable_detail', args=[A('consumable__pk')], verbose_name='Consumable')
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


class KitTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('inventory:kit_detail', args=[A('pk')], verbose_name='Name')
    description = tables.Column(verbose_name='Description')
    item_count = tables.Column(accessor='item_count', verbose_name='Items Count', orderable=False)
    tenant = tables.LinkColumn('organization:tenant_detail', args=[A('tenant.pk')], accessor='tenant.name', verbose_name='Tenant')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Kit
        fields = ('pk', 'name', 'tenant', 'description', 'item_count', 'actions')
        default_columns = ('pk', 'name', 'tenant', 'description', 'item_count', 'actions')
