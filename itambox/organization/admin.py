from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from .models import (
    Region, SiteGroup, Tenant, Location, TenantGroup, Site,
    Contact, ContactRole, ContactAssignment,
    Membership, Role, RoleAssignment, CostCenter, TenantResourceGrant,
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
    list_display = ('name', 'slug', 'group', 'managed_by', 'is_provider')
    list_filter = ('group', 'managed_by', 'is_provider')
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
    list_display = ('user', 'tenant', 'is_active', 'joined_at')
    list_filter = ('tenant', 'is_active')
    search_fields = ('user__username', 'user__email', 'tenant__name')


class RoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ('membership', 'role', 'reach', 'managed_scope', 'granted_by', 'granted_at')
    list_filter = ('reach', 'managed_scope')
    search_fields = ('membership__user__username', 'membership__tenant__name', 'role__name')
    raw_id_fields = ('membership', 'role', 'granted_by')


class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'tenant', 'shared_with_managed')
    list_filter = ('tenant', 'shared_with_managed')
    search_fields = ('name', 'tenant__name', 'description')
    prepopulated_fields = {'slug': ('name',)}


class TenantResourceGrantAdmin(admin.ModelAdmin):
    list_display = ('tenant', 'grantee_tenant', 'grantee_tenant_group',
                    'resource_type', 'resource_id', 'access_level',
                    'granted_by', 'created_at', 'deleted_at')
    list_filter = ('access_level', 'resource_type')
    search_fields = ('tenant__name', 'grantee_tenant__name',
                     'grantee_tenant_group__name', 'reason')
    raw_id_fields = ('tenant', 'grantee_tenant', 'grantee_tenant_group', 'granted_by')

    def get_queryset(self, request):
        # Include revoked (soft-deleted) grants — the admin is the operator's
        # audit surface. _base_manager: the model deliberately defines no
        # all_objects (see the model docstring).
        return TenantResourceGrant._base_manager.all()


class CostCenterAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'tenant', 'parent', 'is_active')
    list_filter = ('tenant', 'is_active')
    search_fields = ('name', 'code', 'description')
    prepopulated_fields = {'slug': ('name',)}
    raw_id_fields = ('parent',)


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
admin.site.register(RoleAssignment, RoleAssignmentAdmin)
admin.site.register(Role, RoleAdmin)
admin.site.register(CostCenter, CostCenterAdmin)
admin.site.register(TenantResourceGrant, TenantResourceGrantAdmin)
