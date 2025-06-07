from django.contrib import admin
# Import models from this app
from .models import (
    Asset, AssetRole, Manufacturer, ActivityLog
)

# Register your models here.

@admin.register(AssetRole)
class AssetRoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    # The model's verbose_name will be used by default

# Basic registration for others (can customize later)
@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ('name', 'asset_tag', 'model', 'manufacturer', 'asset_role', 'status', 'location')
    list_filter = ('status', 'asset_role', 'manufacturer', 'location')
    search_fields = ('name', 'asset_tag', 'serial_number', 'model')

admin.site.register(Manufacturer)
admin.site.register(ActivityLog)

# Registrations for Site, Region, SiteGroup, Tenant, Tag, Location moved to organization/admin.py
