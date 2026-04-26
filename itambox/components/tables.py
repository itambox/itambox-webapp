import django_tables2 as tables
from django_tables2.utils import A
from django.utils.html import format_html
from django.urls import reverse
from core.tables import ActionsColumn, BaseTable, ToggleColumn
from extras.tables import TagColumn
from .models import Component, ComponentStock, ComponentAllocation


class ComponentTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('components:component_detail', args=[A('pk')], verbose_name='Name')
    manufacturer = tables.Column(linkify=True)
    category = tables.Column(accessor='category.name', verbose_name='Category')
    part_number = tables.Column(verbose_name='Part Number')
    total_stock = tables.Column(verbose_name='Total Stock', orderable=False)
    available_stock = tables.Column(verbose_name='Available', orderable=False)
    min_stock_level = tables.Column(verbose_name='Min Stock Level')
    tenant = tables.Column(linkify=True)
    tags = TagColumn(url_name='components:component_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = Component
        fields = ('pk', 'name', 'manufacturer', 'category', 'part_number', 'total_stock', 'available_stock', 'min_stock_level', 'tenant', 'tags', 'actions')
        default_columns = ('pk', 'name', 'manufacturer', 'category', 'part_number', 'total_stock', 'available_stock', 'min_stock_level', 'tenant', 'tags', 'actions')


class ComponentStockTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    component = tables.LinkColumn('components:component_detail', args=[A('component.pk')], verbose_name='Component')
    location = tables.LinkColumn('organization:location_detail', args=[A('location.pk')], verbose_name='Location')
    qty = tables.Column(verbose_name='Quantity')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = ComponentStock
        fields = ('pk', 'component', 'location', 'qty', 'actions')
        default_columns = ('pk', 'component', 'location', 'qty', 'actions')

    def render_qty(self, value, record):
        return format_html(
            '<div class="d-flex align-items-center justify-content-start">'
            '  <button class="btn btn-sm btn-icon btn-outline-secondary me-2 px-1 py-0 lh-1" '
            '          hx-post="{}" hx-swap="outerHTML" hx-target="closest div" style="height: 1.5rem; width: 1.5rem;">'
            '    <i class="mdi mdi-minus" style="font-size: 0.75rem;"></i>'
            '  </button>'
            '  <span class="badge bg-blue-lt text-blue font-weight-bold px-2 py-1" style="font-size: 0.85rem;">{}</span>'
            '  <button class="btn btn-sm btn-icon btn-outline-secondary ms-2 px-1 py-0 lh-1" '
            '          hx-post="{}" hx-swap="outerHTML" hx-target="closest div" style="height: 1.5rem; width: 1.5rem;">'
            '    <i class="mdi mdi-plus" style="font-size: 0.75rem;"></i>'
            '  </button>'
            '</div>',
            reverse('components:componentstock_adjust', kwargs={'pk': record.pk}) + '?action=decrement',
            value,
            reverse('components:componentstock_adjust', kwargs={'pk': record.pk}) + '?action=increment'
        )


class ComponentAllocationTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    component = tables.LinkColumn('components:component_detail', args=[A('component.pk')], verbose_name='Component')
    asset = tables.LinkColumn('assets:asset_detail', args=[A('asset.pk')], verbose_name='Asset')
    from_location = tables.LinkColumn('organization:location_detail', args=[A('from_location.pk')], verbose_name='From Location')
    qty_allocated = tables.Column(verbose_name='Qty Allocated')
    allocated_at = tables.DateTimeColumn(format='Y-m-d H:i', verbose_name='Allocated')
    tags = TagColumn(url_name='components:componentallocation_list')
    actions = ActionsColumn()

    class Meta(BaseTable.Meta):
        model = ComponentAllocation
        fields = ('pk', 'component', 'asset', 'from_location', 'qty_allocated', 'allocated_at', 'notes', 'tags', 'actions')
        default_columns = ('pk', 'component', 'asset', 'from_location', 'qty_allocated', 'allocated_at', 'tags', 'actions')

