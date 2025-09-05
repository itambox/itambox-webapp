from django.contrib import admin
from .models import Accessory, AccessoryStock, AccessoryAssignment, Consumable, ConsumableStock, ConsumableAssignment, Kit, KitItem

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
