from django.contrib import admin

from .models import (
    Contact,
    ContactAssignment,
    ContactRole,
    CostCenter,
    Location,
    Membership,
    Region,
    Role,
    RoleGrant,
    RoleGrantScope,
    Site,
    SiteGroup,
    Tenant,
    TenantGroup,
    TenantResourceGrant,
    TenantResourceGrantExpiryRevocation,
    TenantResourceGrantExpiryRun,
)


class SiteAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "status", "region", "group", "tenant")
    list_filter = ("status", "region", "group", "tenant")
    prepopulated_fields = {"slug": ("name",)}


class RegionAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "description")
    prepopulated_fields = {"slug": ("name",)}


class SiteGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "description")
    prepopulated_fields = {"slug": ("name",)}


class TenantGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "parent")
    prepopulated_fields = {"slug": ("name",)}


class TenantAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "group", "managed_by", "is_provider")
    list_filter = ("group", "managed_by", "is_provider")
    search_fields = ("name", "slug", "description", "comments")
    prepopulated_fields = {"slug": ("name",)}


class LocationAdmin(admin.ModelAdmin):
    list_display = ("name", "site", "status", "parent", "facility")
    list_filter = ("site", "status", "parent")
    search_fields = ("name", "slug", "facility", "description")
    prepopulated_fields = {"slug": ("name",)}


class ContactAdmin(admin.ModelAdmin):
    list_display = ("name", "title", "phone", "email", "web_url")
    search_fields = ("name", "title", "phone", "email", "description")


class ContactRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}


class ContactAssignmentAdmin(admin.ModelAdmin):
    list_display = ("contact", "role", "content_type", "object_id", "priority")
    list_filter = ("role", "priority")


class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "tenant", "is_active", "joined_at")
    list_filter = ("tenant", "is_active")
    search_fields = ("user__username", "user__email", "tenant__name")


class RoleGrantScopeInline(admin.TabularInline):
    model = RoleGrantScope
    extra = 0
    raw_id_fields = ("tenant", "tenant_group")


class RoleGrantAdmin(admin.ModelAdmin):
    list_display = (
        "role",
        "membership",
        "user_group",
        "valid_until",
        "granted_by",
        "granted_at",
    )
    list_filter = ("role__tenant", "valid_until")
    search_fields = (
        "role__name",
        "membership__user__username",
        "membership__tenant__name",
        "user_group__name",
        "reason",
    )
    raw_id_fields = ("membership", "user_group", "role", "granted_by")
    inlines = (RoleGrantScopeInline,)


class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "shared_with_managed")
    list_filter = ("tenant", "shared_with_managed")
    search_fields = ("name", "tenant__name", "description")
    prepopulated_fields = {"slug": ("name",)}


class TenantResourceGrantAdmin(admin.ModelAdmin):
    list_display = (
        "tenant",
        "grantee_tenant",
        "grantee_tenant_group",
        "resource_type",
        "resource_id",
        "access_level",
        "granted_by",
        "valid_until",
        "created_at",
        "deleted_at",
    )
    list_filter = ("access_level", "resource_type")
    search_fields = ("tenant__name", "grantee_tenant__name", "grantee_tenant_group__name", "reason")
    raw_id_fields = ("tenant", "grantee_tenant", "grantee_tenant_group", "granted_by")

    def get_queryset(self, request):
        # Include revoked (soft-deleted) grants — the admin is the operator's
        # audit surface. _base_manager: the model deliberately defines no
        # all_objects (see the model docstring).
        return TenantResourceGrant._base_manager.all()


class _ReadOnlyExpiryAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return bool(request.user and request.user.is_superuser)

    def has_view_permission(self, request, obj=None):
        return bool(request.user and request.user.is_superuser)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class TenantResourceGrantExpiryRunAdmin(_ReadOnlyExpiryAdmin):
    list_display = (
        "tenant",
        "schedule_slot",
        "state",
        "outcome",
        "attempt_count",
        "revoked_count",
        "remaining_due_count",
        "invalid_count",
        "finished_at",
    )
    readonly_fields = [field.name for field in TenantResourceGrantExpiryRun._meta.fields]


class TenantResourceGrantExpiryRevocationAdmin(_ReadOnlyExpiryAdmin):
    list_display = ("run", "grant", "triggering_valid_until", "revoked_at", "object_change", "request_id")
    readonly_fields = [field.name for field in TenantResourceGrantExpiryRevocation._meta.fields]

    def get_queryset(self, request):
        return TenantResourceGrantExpiryRevocation._base_manager.integrity_valid().select_related(
            "run", "grant", "object_change"
        )


class CostCenterAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "tenant", "parent", "is_active")
    list_filter = ("tenant", "is_active")
    search_fields = ("name", "code", "description")
    prepopulated_fields = {"slug": ("name",)}
    raw_id_fields = ("parent",)


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
admin.site.register(RoleGrant, RoleGrantAdmin)
admin.site.register(Role, RoleAdmin)
admin.site.register(CostCenter, CostCenterAdmin)
admin.site.register(TenantResourceGrant, TenantResourceGrantAdmin)
admin.site.register(TenantResourceGrantExpiryRun, TenantResourceGrantExpiryRunAdmin)
admin.site.register(TenantResourceGrantExpiryRevocation, TenantResourceGrantExpiryRevocationAdmin)
