from django.contrib import admin
from .models import (
    Region, SiteGroup, Tenant, Location, TenantGroup, Site, Contact, ContactRole, ContactAssignment
)


# Define ModelAdmin classes first
class SiteAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'status', 'region', 'group', 'tenant')
    list_filter = ('status', 'region', 'group', 'tenant')
    prepopulated_fields = {"slug": ("name",)}

class RegionAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'description')
    prepopulated_fields = {"slug": ("name",)}

class SiteGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent', 'description')
    prepopulated_fields = {"slug": ("name",)}

class TenantGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent')
    prepopulated_fields = {"slug": ("name",)}

class TenantAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'group')
    list_filter = ('group',)
    search_fields = ('name', 'slug', 'description', 'comments')
    prepopulated_fields = {"slug": ("name",)}

# TagAdmin moved to extras/admin.py

class LocationAdmin(admin.ModelAdmin):
    list_display = ('name', 'site', 'status', 'parent', 'facility')
    list_filter = ('site', 'status', 'parent')
    search_fields = ('name', 'slug', 'facility', 'description')
    prepopulated_fields = {"slug": ("name",)}

class ContactAdmin(admin.ModelAdmin):
    list_display = ('name', 'title', 'phone', 'email', 'web_url')
    search_fields = ('name', 'title', 'phone', 'email', 'description')

class ContactRoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'description')
    prepopulated_fields = {"slug": ("name",)}

class ContactAssignmentAdmin(admin.ModelAdmin):
    list_display = ('contact', 'role', 'content_type', 'object_id', 'priority')
    list_filter = ('role', 'priority')

# Now register models using admin.site.register
admin.site.register(Site, SiteAdmin)
admin.site.register(Region, RegionAdmin)
admin.site.register(SiteGroup, SiteGroupAdmin)
admin.site.register(TenantGroup, TenantGroupAdmin)
admin.site.register(Tenant, TenantAdmin)
admin.site.register(Location, LocationAdmin)
admin.site.register(Contact, ContactAdmin)
admin.site.register(ContactRole, ContactRoleAdmin)
admin.site.register(ContactAssignment, ContactAssignmentAdmin)

