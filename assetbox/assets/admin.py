from django.contrib import admin
# Import models from this app
from .models import (
    Asset, AssetRole, Manufacturer, AssetType, ActivityLog
)

# Register your models here.

@admin.register(AssetRole)
class AssetRoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'asset_count')
    prepopulated_fields = {"slug": ("name",)}

    def asset_count(self, obj):
        return obj.asset_set.count()
    asset_count.short_description = 'Assets'

@admin.register(Manufacturer)
class ManufacturerAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'asset_count')
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ('name',)

    def asset_count(self, obj):
        return obj.assets.count()
    asset_count.short_description = 'Assets'

@admin.register(AssetType)
class AssetTypeAdmin(admin.ModelAdmin):
    list_display = ('manufacturer', 'model', 'slug', 'part_number')
    list_filter = ('manufacturer', 'storage_type')
    search_fields = ('manufacturer__name', 'model', 'slug', 'part_number')
    prepopulated_fields = {"slug": ("manufacturer", "model",)}

@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ('name', 'asset_tag', 'status', 'manufacturer', 'model', 'asset_role', 'location', 'updated_at')
    list_filter = ('status', 'asset_role', 'asset_type__manufacturer', 'location', 'asset_type')
    search_fields = ('name', 'asset_tag', 'serial_number', 'asset_type__model')
    readonly_fields = ('created_at', 'updated_at')

@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('asset', 'action', 'user', 'timestamp')
    list_filter = ('action', 'user')
    search_fields = ('asset__name', 'asset__asset_tag', 'notes')
    readonly_fields = ('asset', 'user', 'action', 'timestamp')

# Registrations for Site, Region, SiteGroup, Tenant, Tag, Location moved to organization/admin.py
