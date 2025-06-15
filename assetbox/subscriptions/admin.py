from django.contrib import admin
from .models import Provider, Subscription, SubscriptionAssignment

@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'account_id', 'portal_url')
    search_fields = ('name', 'account_id', 'admin_notes')
    filter_horizontal = ('tags',)

@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider', 'type', 'renewal_date', 'renewal_cost')
    list_filter = ('provider', 'type', 'tags')
    search_fields = ('name', 'provider__name', 'description', 'notes')
    filter_horizontal = ('tags',)
    date_hierarchy = 'renewal_date'

@admin.register(SubscriptionAssignment)
class SubscriptionAssignmentAdmin(admin.ModelAdmin):
    list_display = ('subscription', 'assigned_object', 'assigned_date')
    list_filter = ('content_type', 'subscription__provider')
    search_fields = ('subscription__name', 'notes')
    # GFKs are not directly searchable/filterable in list_filter by default
    # Consider adding custom filters or displaying content_type/object_id
