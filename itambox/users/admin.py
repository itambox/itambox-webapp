from django.contrib import admin
from django.contrib.auth.models import Group

from .models import ProviderMembership

try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass


class ProviderMembershipAdmin(admin.ModelAdmin):
    list_display = ('user', 'provider', 'provider_role', 'tenant_scope', 'is_active')
    list_filter = ('provider', 'tenant_scope', 'is_active')
    search_fields = ('user__username', 'user__email', 'provider__name')


admin.site.register(ProviderMembership, ProviderMembershipAdmin)
