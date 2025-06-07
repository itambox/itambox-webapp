from django.contrib import admin
from .models import Asset, AssetRole, Manufacturer, ActivityLog

@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ('name', 'asset_tag', 'serial_number', 'status', 'asset_role', 'manufacturer', 'location', 'purchase_date', 'warranty_end_date')
    list_filter = ('status', 'asset_role', 'manufacturer', 'location', 'purchase_date')
    search_fields = ('name', 'asset_tag', 'serial_number')

@admin.register(AssetRole)
class AssetRoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'description')
    prepopulated_fields = {'slug': ('name',)}

@admin.register(Manufacturer)
class ManufacturerAdmin(admin.ModelAdmin):
    pass

@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('asset', 'user', 'action', 'timestamp', 'notes')
    list_filter = ('action', 'user')
    search_fields = ('asset__name', 'notes', 'user__username') 