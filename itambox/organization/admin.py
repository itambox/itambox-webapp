from django.contrib import admin
from .models import (
    Region, SiteGroup, Tenant, Location, TenantGroup, Site,
    Contact, ContactRole, ContactAssignment,
    Membership, TenantInvitation, Role, CostCenter, Provider,
)


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
    list_display = ('name', 'slug', 'group', 'provider')
    list_filter = ('group', 'provider')
    search_fields = ('name', 'slug', 'description', 'comments')
    prepopulated_fields = {"slug": ("name",)}


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


class MembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'person_type', 'tenant', 'provider', 'is_active', 'joined_at')
    list_filter = ('person_type', 'tenant', 'provider', 'is_active', 'roles')
    search_fields = ('user__username', 'user__email', 'tenant__name', 'provider__name')


class TenantInvitationAdmin(admin.ModelAdmin):
    list_display = ('email', 'tenant', 'role', 'created_at', 'expires_at', 'accepted_at')
    list_filter = ('tenant', 'role', 'accepted_at')
    search_fields = ('email', 'tenant__name')


class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'scope', 'tenant', 'provider', 'is_default')
    list_filter = ('scope', 'tenant', 'provider', 'is_default')
    search_fields = ('name', 'tenant__name', 'provider__name', 'description')
    prepopulated_fields = {'slug': ('name',)}


class CostCenterAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'tenant', 'parent', 'is_active')
    list_filter = ('tenant', 'is_active')
    search_fields = ('name', 'code', 'description')
    prepopulated_fields = {'slug': ('name',)}
    raw_id_fields = ('parent',)


class ProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'internal_tenant')
    search_fields = ('name', 'slug', 'description', 'comments')
    prepopulated_fields = {'slug': ('name',)}


admin.site.register(Site, SiteAdmin)
admin.site.register(Region, RegionAdmin)
admin.site.register(SiteGroup, SiteGroupAdmin)
admin.site.register(TenantGroup, TenantGroupAdmin)
admin.site.register(Tenant, TenantAdmin)
admin.site.register(Location, LocationAdmin)
admin.site.register(Contact, ContactAdmin)
admin.site.register(ContactRole, ContactRoleAdmin)
admin.site.register(ContactAssignment, ContactAssignmentAdmin)
admin.site.register(Membership, MembershipAdmin)
admin.site.register(TenantInvitation, TenantInvitationAdmin)
admin.site.register(Role, RoleAdmin)
admin.site.register(CostCenter, CostCenterAdmin)
admin.site.register(Provider, ProviderAdmin)
