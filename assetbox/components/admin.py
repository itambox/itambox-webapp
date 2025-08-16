from django.contrib import admin
from .models import ComponentType, ComponentInstance

@admin.register(ComponentType)
class ComponentTypeAdmin(admin.ModelAdmin):
    list_display = ('manufacturer', 'name', 'category', 'part_number', 'specs')
    list_filter = ('manufacturer', 'category')
    search_fields = ('name', 'part_number', 'specs', 'description')
    prepopulated_fields = {"slug": ("manufacturer", "name",)}

@admin.register(ComponentInstance)
class ComponentInstanceAdmin(admin.ModelAdmin):
    list_display = ('component_type', 'serial_number', 'parent_asset', 'status', 'purchase_date', 'purchase_cost')
    list_filter = ('status', 'component_type__manufacturer', 'component_type')
    search_fields = ('serial_number', 'notes', 'parent_asset__name')
    raw_id_fields = ('parent_asset',)
