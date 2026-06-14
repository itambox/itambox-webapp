from django.contrib import admin
from .models import PurchaseOrder, PurchaseOrderLine, FulfillmentLink, Contract

class PurchaseOrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 1
    fields = ('asset_type', 'component', 'accessory', 'consumable', 'qty_ordered', 'qty_received', 'unit_price')

@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'supplier', 'status', 'order_date', 'expected_delivery_date', 'destination_location', 'created_by')
    list_filter = ('status', 'supplier', 'destination_location', 'order_date')
    search_fields = ('order_number', 'notes')
    inlines = [PurchaseOrderLineInline]

@admin.register(PurchaseOrderLine)
class PurchaseOrderLineAdmin(admin.ModelAdmin):
    list_display = ('purchase_order', 'qty_ordered', 'qty_received', 'unit_price', 'asset_type', 'component', 'accessory', 'consumable')
    list_filter = ('purchase_order__status', 'purchase_order__supplier')

@admin.register(FulfillmentLink)
class FulfillmentLinkAdmin(admin.ModelAdmin):
    list_display = ('asset_request', 'purchase_order_line', 'qty_allocated')


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    list_display = (
        'contract_number', 'name', 'contract_type', 'status',
        'supplier', 'start_date', 'end_date', 'auto_renew', 'tenant',
    )
    list_filter = ('status', 'contract_type', 'supplier', 'auto_renew', 'tenant')
    search_fields = ('contract_number', 'name', 'notes', 'sla_terms')
    filter_horizontal = ('assets',)
    fieldsets = (
        ('Identity', {
            'fields': ('name', 'contract_number', 'contract_type', 'status', 'tenant'),
        }),
        ('Vendor / Commercial', {
            'fields': ('supplier', 'cost', 'currency', 'billing_cycle', 'purchase_order'),
        }),
        ('Dates', {
            'fields': ('start_date', 'end_date', 'renewal_date', 'auto_renew'),
        }),
        ('SLA', {
            'fields': ('sla_response_time', 'sla_resolution_time', 'coverage_hours', 'sla_terms'),
        }),
        ('Coverage', {
            'fields': ('assets',),
        }),
        ('Notes', {
            'fields': ('notes',),
        }),
    )
