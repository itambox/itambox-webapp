from django.contrib import admin
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _

from users.models import GroupMembership

try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass

# Membership now lives in organization.models — registered there.

User = get_user_model()


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Stock Django UserAdmin extended to surface the custom ``can_login`` flag."""
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_active', 'can_login', 'is_staff', 'is_superuser')
    list_filter = BaseUserAdmin.list_filter + ('can_login',)
    fieldsets = BaseUserAdmin.fieldsets + (
        (_('Login capability'), {
            'fields': ('can_login',),
            'description': _("Whether this user may perform interactive login (password or SSO). "
                             "Independent of 'active' status and API-token access."),
        }),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        (_('Login capability'), {'fields': ('can_login',)}),
    )


@admin.register(GroupMembership)
class GroupMembershipAdmin(admin.ModelAdmin):
    list_display = ('user_group', 'membership', 'source', 'external_id', 'added_at')
    list_filter = ('source', 'user_group__tenant')
    search_fields = (
        'user_group__name', 'membership__user__username',
        'membership__tenant__name', 'external_id',
    )
    raw_id_fields = ('user_group', 'membership', 'added_by')

    def has_add_permission(self, request):
        return settings.RBAC_RESOLVER_MODE == 'new' and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return settings.RBAC_RESOLVER_MODE == 'new' and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return settings.RBAC_RESOLVER_MODE == 'new' and super().has_delete_permission(request, obj)
