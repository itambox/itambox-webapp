from django.contrib import admin
# Import models from this app
from .models import (
    Asset, Category, Manufacturer, ActivityLog
)

# Register your models here.

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'description')
    # The model's verbose_name will be used by default

# Basic registration for others (can customize later)
@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ('name', 'asset_tag', 'model', 'manufacturer', 'category', 'status', 'location')
    list_filter = ('status', 'category', 'manufacturer', 'location')
    search_fields = ('name', 'asset_tag', 'serial_number', 'model')

admin.site.register(Manufacturer)
admin.site.register(ActivityLog)

# Registrations for Site, Region, SiteGroup, Tenant, Tag, Location moved to organization/admin.py
