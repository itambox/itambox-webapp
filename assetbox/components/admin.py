from django.contrib import admin
from .models import Component, ComponentStock, ComponentAllocation


@admin.register(Component)
class ComponentAdmin(admin.ModelAdmin):
    list_display = ('manufacturer', 'name', 'category', 'part_number', 'min_stock_level')
    list_filter = ('manufacturer', 'category')
    search_fields = ('name', 'part_number', 'description')
    prepopulated_fields = {"slug": ("manufacturer", "name",)}


@admin.register(ComponentStock)
class ComponentStockAdmin(admin.ModelAdmin):
    list_display = ('component', 'location', 'qty')
    list_filter = ('component__manufacturer', 'component', 'location')
    search_fields = ('component__name', 'location__name')


@admin.register(ComponentAllocation)
class ComponentAllocationAdmin(admin.ModelAdmin):
    list_display = ('component', 'asset', 'qty_allocated', 'allocated_at')
    list_filter = ('component__manufacturer', 'component', 'asset')
    search_fields = ('component__name', 'asset__name', 'notes')
