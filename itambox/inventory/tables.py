import django_tables2 as tables
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from django_tables2.utils import A

from core.tables import BaseTable, ToggleColumn, ActionsColumn
from extras.tables import TagColumn
from .models import Accessory, AccessoryAssignment, AccessoryStock, Consumable, ConsumableAssignment, ConsumableStock, Kit, Component, ComponentStock, ComponentAllocation


from .mixins import CheckableInventoryTableMixin


class AccessoryTable(CheckableInventoryTableMixin, BaseTable):
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
    actions = tables.Column(
        verbose_name='',
        orderable=False,
        empty_values=(),
        attrs={
            'th': {'class': 'col-actions-wide text-nowrap'},
            'td': {'class': 'text-end text-nowrap noprint p-1 col-actions-wide'}
        }
    )

    class Meta(BaseTable.Meta):
        model = AccessoryStock
        fields = ('pk', 'accessory', 'location', 'qty', 'actions')
        default_columns = ('pk', 'accessory', 'location', 'qty', 'actions')

    def render_actions(self, record):
        request = getattr(self, 'request', None)
        if not request or not self.has_perm(request.user, 'inventory.change_accessory', record.accessory):
            return mark_safe('<span class="text-muted small">—</span>')
        
        checkout_url = reverse('inventory:accessory_checkout', kwargs={'pk': record.accessory.pk})
        delete_url = reverse('inventory:accessorystock_delete', kwargs={'pk': record.pk})
        add_stock_url = reverse('inventory:accessory_add_stock', kwargs={'pk': record.accessory.pk})
        checkout_title = _('Check-out')
        
        add_stock_html = ''
        if self.has_perm(request.user, 'inventory.change_accessorystock', record.accessory):
            add_stock_html = format_html(
                '  <button type="button" class="btn btn-sm btn-action d-flex align-items-center" '
                '          hx-get="{}?location={}" hx-target="#modal-placeholder" hx-swap="innerHTML" title="Add Stock">'
                '    <i class="mdi mdi-plus me-1"></i> Add Stock'
                '  </button>',
                add_stock_url,
                record.location.pk
            )
        
        return format_html(
            '<div class="d-flex gap-1 justify-content-end">'
            '  {}'
            '  <a class="btn btn-sm btn-soft-success check-action d-flex align-items-center" role="button" style="cursor: pointer" '
            '     hx-get="{}?from_location={}" hx-target="#modal-placeholder" hx-swap="innerHTML" '
            '     title="{}" aria-label="{}">'
            '    <i class="mdi mdi-logout me-1"></i> {}'
            '  </a>'
            '  <a class="btn btn-sm btn-action btn-action-danger px-2 d-flex align-items-center" href="{}" title="Delete">'
            '    <i class="mdi mdi-trash-can-outline m-0"></i>'
            '  </a>'
            '</div>',
            add_stock_html,
            checkout_url,
            record.location.pk,
            checkout_title,
            checkout_title,
            checkout_title,
            delete_url
        )

    def render_qty(self, value, record):
        return format_html(
            '<span class="badge bg-blue-lt text-blue font-weight-bold px-2 py-1" style="font-size: 0.85rem;">{}</span>',
            value
        )


class AccessoryAssignmentTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    accessory = tables.LinkColumn('inventory:accessory_detail', args=[A('accessory__pk')], verbose_name='Accessory')
    assigned_to = tables.Column(verbose_name='Assigned To', orderable=False, empty_values=())
    qty = tables.Column(verbose_name='Qty')
    assigned_date = tables.DateTimeColumn(format="Y-m-d H:i", verbose_name='Date')
    actions = tables.Column(
        verbose_name='',
        orderable=False,
        empty_values=(),
        attrs={
            'th': {'class': 'col-actions text-nowrap'},
            'td': {'class': 'text-end text-nowrap noprint p-1 col-actions'}
        }
    )

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
        elif record.assigned_asset:
            url = reverse('assets:asset_detail', kwargs={'pk': record.assigned_asset.pk})
            return format_html('<a href="{}">Asset: {}</a>', url, record.assigned_asset)
        return "—"

    def render_actions(self, record):
        request = getattr(self, 'request', None)
        if not request or not self.has_perm(request.user, 'inventory.change_accessory', record.accessory):
            return mark_safe('<span class="text-muted small">—</span>')
        
        url = reverse('inventory:accessory_checkin', kwargs={'pk': record.pk})
        confirm_msg = _("Are you sure you want to check in this accessory assignment?")
        return format_html(
            '<div class="d-flex gap-1 justify-content-end">'
            '  <button hx-post="{}" hx-confirm="{}" '
            '          class="btn btn-sm btn-soft-outline-success check-action d-flex align-items-center" title="Check-in">'
            '    <i class="mdi mdi-keyboard-return me-1"></i> {}'
            '  </button>'
            '</div>',
            url,
            confirm_msg,
            _("Check-in")
        )


class ConsumableTable(CheckableInventoryTableMixin, BaseTable):
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
    actions = tables.Column(
        verbose_name='',
        orderable=False,
        empty_values=(),
        attrs={
            'th': {'class': 'col-actions-wide text-nowrap'},
            'td': {'class': 'text-end text-nowrap noprint p-1 col-actions-wide'}
        }
    )

    class Meta(BaseTable.Meta):
        model = ConsumableStock
        fields = ('pk', 'consumable', 'location', 'qty', 'actions')
        default_columns = ('pk', 'consumable', 'location', 'qty', 'actions')

    def render_actions(self, record):
        request = getattr(self, 'request', None)
        if not request or not self.has_perm(request.user, 'inventory.change_consumable', record.consumable):
            return mark_safe('<span class="text-muted small">—</span>')
        
        checkout_url = reverse('inventory:consumable_checkout', kwargs={'pk': record.consumable.pk})
        delete_url = reverse('inventory:consumablestock_delete', kwargs={'pk': record.pk})
        add_stock_url = reverse('inventory:consumable_add_stock', kwargs={'pk': record.consumable.pk})
        checkout_title = _('Check-out')
        
        add_stock_html = ''
        if self.has_perm(request.user, 'inventory.change_consumablestock', record.consumable):
            add_stock_html = format_html(
                '  <button type="button" class="btn btn-sm btn-action d-flex align-items-center" '
                '          hx-get="{}?location={}" hx-target="#modal-placeholder" hx-swap="innerHTML" title="Add Stock">'
                '    <i class="mdi mdi-plus me-1"></i> Add Stock'
                '  </button>',
                add_stock_url,
                record.location.pk
            )
        
        return format_html(
            '<div class="d-flex gap-1 justify-content-end">'
            '  {}'
            '  <a class="btn btn-sm btn-soft-success check-action d-flex align-items-center" role="button" style="cursor: pointer" '
            '     hx-get="{}?from_location={}" hx-target="#modal-placeholder" hx-swap="innerHTML" '
            '     title="{}" aria-label="{}">'
            '    <i class="mdi mdi-logout me-1"></i> {}'
            '  </a>'
            '  <a class="btn btn-sm btn-action btn-action-danger px-2 d-flex align-items-center" href="{}" title="Delete">'
            '    <i class="mdi mdi-trash-can-outline m-0"></i>'
            '  </a>'
            '</div>',
            add_stock_html,
            checkout_url,
            record.location.pk,
            checkout_title,
            checkout_title,
            checkout_title,
            delete_url
        )

    def render_qty(self, value, record):
        return format_html(
            '<span class="badge bg-blue-lt text-blue font-weight-bold px-2 py-1" style="font-size: 0.85rem;">{}</span>',
            value
        )


class ConsumableAssignmentTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    consumable = tables.LinkColumn('inventory:consumable_detail', args=[A('consumable__pk')], verbose_name='Consumable')
    assigned_to = tables.Column(verbose_name='Consumed By', orderable=False, empty_values=())
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
        elif record.assigned_asset:
            url = reverse('assets:asset_detail', kwargs={'pk': record.assigned_asset.pk})
            return format_html('<a href="{}">Asset: {}</a>', url, record.assigned_asset)
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


class ComponentTable(CheckableInventoryTableMixin, BaseTable):
    pk = ToggleColumn(accessor='pk')
    name = tables.LinkColumn('inventory:component_detail', args=[A('pk')], verbose_name='Name')
    manufacturer = tables.Column(linkify=True)
    category = tables.Column(accessor='category.name', verbose_name='Category')
    part_number = tables.Column(verbose_name='Part Number')
    total_stock = tables.Column(verbose_name='Total Stock', orderable=False)
    available_stock = tables.Column(verbose_name='Available', orderable=False)
    min_qty = tables.Column(verbose_name='Safety Threshold')
    tenant = tables.Column(linkify=True)
    tags = TagColumn(url_name='inventory:component_list')

    class Meta(BaseTable.Meta):
        model = Component
        fields = ('pk', 'name', 'manufacturer', 'category', 'part_number', 'total_stock', 'available_stock', 'min_qty', 'tenant', 'tags', 'actions')
        default_columns = ('pk', 'name', 'manufacturer', 'category', 'part_number', 'total_stock', 'available_stock', 'min_qty', 'tenant', 'tags', 'actions')


class ComponentStockTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    component = tables.LinkColumn('inventory:component_detail', args=[A('component.pk')], verbose_name='Component')
    location = tables.LinkColumn('organization:location_detail', args=[A('location.pk')], verbose_name='Location')
    qty = tables.Column(verbose_name='Quantity')
    actions = tables.Column(
        verbose_name='',
        orderable=False,
        empty_values=(),
        attrs={
            'th': {'class': 'col-actions-wide text-nowrap'},
            'td': {'class': 'text-end text-nowrap noprint p-1 col-actions-wide'}
        }
    )

    class Meta(BaseTable.Meta):
        model = ComponentStock
        fields = ('pk', 'component', 'location', 'qty', 'actions')
        default_columns = ('pk', 'component', 'location', 'qty', 'actions')

    def render_actions(self, record):
        request = getattr(self, 'request', None)
        if not request or not self.has_perm(request.user, 'inventory.change_component', record.component):
            return mark_safe('<span class="text-muted small">—</span>')
        
        checkout_url = reverse('inventory:component_checkout', kwargs={'pk': record.component.pk})
        delete_url = reverse('inventory:componentstock_delete', kwargs={'pk': record.pk})
        add_stock_url = reverse('inventory:component_add_stock', kwargs={'pk': record.component.pk})
        checkout_title = _('Check-out')
        
        add_stock_html = ''
        if self.has_perm(request.user, 'inventory.change_componentstock', record.component):
            add_stock_html = format_html(
                '  <button type="button" class="btn btn-sm btn-action d-flex align-items-center" '
                '          hx-get="{}?location={}" hx-target="#modal-placeholder" hx-swap="innerHTML" title="Add Stock">'
                '    <i class="mdi mdi-plus me-1"></i> Add Stock'
                '  </button>',
                add_stock_url,
                record.location.pk
            )
        
        return format_html(
            '<div class="d-flex gap-1 justify-content-end">'
            '  {}'
            '  <a class="btn btn-sm btn-soft-success check-action d-flex align-items-center" role="button" style="cursor: pointer" '
            '     hx-get="{}?from_location={}" hx-target="#modal-placeholder" hx-swap="innerHTML" '
            '     title="{}" aria-label="{}">'
            '    <i class="mdi mdi-logout me-1"></i> {}'
            '  </a>'
            '  <a class="btn btn-sm btn-action btn-action-danger px-2 d-flex align-items-center" href="{}" title="Delete">'
            '    <i class="mdi mdi-trash-can-outline m-0"></i>'
            '  </a>'
            '</div>',
            add_stock_html,
            checkout_url,
            record.location.pk,
            checkout_title,
            checkout_title,
            checkout_title,
            delete_url
        )

    def render_qty(self, value, record):
        return format_html(
            '<span class="badge bg-blue-lt text-blue font-weight-bold px-2 py-1" style="font-size: 0.85rem;">{}</span>',
            value
        )


class ComponentAllocationTable(BaseTable):
    pk = ToggleColumn(accessor='pk')
    component = tables.LinkColumn('inventory:component_detail', args=[A('component__pk')], verbose_name='Component')
    assigned_to = tables.Column(verbose_name='Assigned To', orderable=False, empty_values=())
    qty = tables.Column(verbose_name='Qty')
    assigned_date = tables.DateTimeColumn(format='Y-m-d H:i', verbose_name='Date')
    actions = tables.Column(
        verbose_name='',
        orderable=False,
        empty_values=(),
        attrs={
            'th': {'class': 'col-actions text-nowrap'},
            'td': {'class': 'text-end text-nowrap noprint p-1 col-actions'}
        }
    )

    class Meta(BaseTable.Meta):
        model = ComponentAllocation
        fields = ('pk', 'component', 'assigned_to', 'qty', 'assigned_date', 'actions')
        default_columns = ('pk', 'component', 'assigned_to', 'qty', 'assigned_date', 'actions')

    def render_assigned_to(self, record):
        if record.assigned_holder:
            url = reverse('organization:assetholder_detail', kwargs={'pk': record.assigned_holder.pk})
            return format_html('<a href="{}">Holder: {}</a>', url, record.assigned_holder)
        elif record.assigned_location:
            url = reverse('organization:location_detail', kwargs={'pk': record.assigned_location.pk})
            return format_html('<a href="{}">Location: {}</a>', url, record.assigned_location)
        elif record.assigned_asset:
            url = reverse('assets:asset_detail', kwargs={'pk': record.assigned_asset.pk})
            return format_html('<a href="{}">Asset: {}</a>', url, record.assigned_asset)
        return "—"

    def render_actions(self, record):
        request = getattr(self, 'request', None)
        if not request or not self.has_perm(request.user, 'inventory.change_component', record.component):
            return mark_safe('<span class="text-muted small">—</span>')
        
        url = reverse('inventory:component_checkin', kwargs={'pk': record.pk})
        confirm_msg = _("Are you sure you want to check in this component allocation?")
        return format_html(
            '<div class="d-flex gap-1 justify-content-end">'
            '  <button hx-post="{}" hx-confirm="{}" '
            '          class="btn btn-sm btn-soft-outline-success check-action d-flex align-items-center" title="Check-in">'
            '    <i class="mdi mdi-keyboard-return me-1"></i> {}'
            '  </button>'
            '</div>',
            url,
            confirm_msg,
            _("Check-in")
        )
