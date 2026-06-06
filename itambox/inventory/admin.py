from django.contrib import admin
from .models import Accessory, AccessoryStock, AccessoryAssignment, Consumable, ConsumableStock, ConsumableAssignment, Kit, KitItem, Component, ComponentStock, ComponentAllocation

@admin.register(Accessory)
class AccessoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'manufacturer', 'category', 'min_qty')
    search_fields = ('name', 'part_number')

@admin.register(AccessoryStock)
class AccessoryStockAdmin(admin.ModelAdmin):
    list_display = ('accessory', 'location', 'qty')

@admin.register(AccessoryAssignment)
class AccessoryAssignmentAdmin(admin.ModelAdmin):
    list_display = ('accessory', 'assigned_holder', 'assigned_location', 'from_location', 'qty', 'assigned_date')

@admin.register(Consumable)
class ConsumableAdmin(admin.ModelAdmin):
    list_display = ('name', 'manufacturer', 'category', 'min_qty')
    search_fields = ('name', 'part_number')

@admin.register(ConsumableStock)
class ConsumableStockAdmin(admin.ModelAdmin):
    list_display = ('consumable', 'location', 'qty')

@admin.register(ConsumableAssignment)
class ConsumableAssignmentAdmin(admin.ModelAdmin):
    list_display = ('consumable', 'assigned_holder', 'assigned_location', 'from_location', 'qty', 'assigned_date')

@admin.register(Kit)
class KitAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    search_fields = ('name',)

@admin.register(KitItem)
class KitItemAdmin(admin.ModelAdmin):
    list_display = ('kit', 'asset_type', 'accessory', 'license', 'qty')

@admin.register(Component)
class ComponentAdmin(admin.ModelAdmin):
    list_display = ('manufacturer', 'name', 'category', 'part_number', 'min_qty')
    list_filter = ('manufacturer', 'category')
    search_fields = ('name', 'part_number', 'notes')
    prepopulated_fields = {"slug": ("manufacturer", "name",)}

@admin.register(ComponentStock)
class ComponentStockAdmin(admin.ModelAdmin):
    list_display = ('component', 'location', 'qty')
    list_filter = ('component__manufacturer', 'component', 'location')
    search_fields = ('component__name', 'location__name')

@admin.register(ComponentAllocation)
class ComponentAllocationAdmin(admin.ModelAdmin):
    list_display = ('component', 'assigned_asset', 'qty', 'assigned_date')
    list_filter = ('component__manufacturer', 'component', 'assigned_asset')
    search_fields = ('component__name', 'assigned_asset__name', 'notes')
