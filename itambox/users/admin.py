from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _

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
