from django.contrib import admin
from .models import (
    Region, SiteGroup, Tenant, Location, TenantGroup, Site
)

# Register your models here.

# Define ModelAdmin classes first
class SiteAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'status', 'region', 'group', 'tenant')
    list_filter = ('status', 'region', 'group', 'tenant')
    prepopulated_fields = {"slug": ("name",)}

class RegionAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}
    # TODO: Consider list_display for parent, description, tags?

class SiteGroupAdmin(admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}
    # TODO: Consider list_display for parent, description, tags?

class TenantGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent')
    prepopulated_fields = {"slug": ("name",)}

class TenantAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'group')
    list_filter = ('group',)
    search_fields = ('name', 'slug', 'description', 'comments')
    prepopulated_fields = {"slug": ("name",)}
    # TODO: Consider list_display for description, tags?

# TagAdmin moved to extras/admin.py

class LocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'site', 'status', 'parent', 'facility')
    list_filter = ('site', 'status', 'parent')
    search_fields = ('name', 'slug', 'facility', 'description')
    prepopulated_fields = {"slug": ("name",)}

# Now register models using admin.site.register
admin.site.register(Site, SiteAdmin)
admin.site.register(Region, RegionAdmin)
admin.site.register(SiteGroup, SiteGroupAdmin)
admin.site.register(TenantGroup, TenantGroupAdmin)
admin.site.register(Tenant, TenantAdmin)
admin.site.register(Location, LocationAdmin)

# Register any other organization-specific models here
