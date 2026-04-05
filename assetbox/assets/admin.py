from django.contrib import admin
# Import models from this app
from .models import (
    Asset, AssetRole, Manufacturer, AssetType, InstalledSoftware
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
    list_filter = ('manufacturer',)
    search_fields = ('manufacturer__name', 'model', 'slug', 'part_number')
    prepopulated_fields = {"slug": ("manufacturer", "model",)}

@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ('name', 'asset_tag', 'status', 'tenant', 'manufacturer', 'model', 'asset_role', 'location')
    list_filter = ('status', 'asset_role', 'asset_type__manufacturer', 'location', 'asset_type', 'tenant')
    search_fields = ('name', 'asset_tag', 'serial_number', 'asset_type__model', 'tenant__name')

@admin.register(InstalledSoftware)
class InstalledSoftwareAdmin(admin.ModelAdmin):
    list_display = ('asset', 'software', 'version_detected', 'last_seen_date', 'discovered_by_agent')
    list_filter = ('software__manufacturer', 'software', 'discovered_by_agent', 'asset__location')
    search_fields = ('asset__name', 'asset__asset_tag', 'software__name', 'version_detected', 'notes')
    date_hierarchy = 'last_seen_date'
    raw_id_fields = ('asset', 'software')

# Registrations for Site, Region, SiteGroup, Tenant, Tag, Location moved to organization/admin.py
