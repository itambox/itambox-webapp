from django.contrib import admin
from .models import Provider, Subscription, SubscriptionAssignment


class SubscriptionAssignmentInline(admin.TabularInline):
    model = SubscriptionAssignment
    extra = 0
    readonly_fields = ('assigned_date',)
    raw_id_fields = ('assigned_by',)
    fields = ('content_type', 'object_id', 'assigned_date', 'assigned_by', 'notes')


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_active', 'account_id', 'contact_email', 'contact_phone')
    list_filter = ('is_active', 'tags')
    search_fields = ('name', 'account_id', 'contact_email', 'admin_notes', 'support_contact')
    readonly_fields = ('created_at', 'updated_at')
    prepopulated_fields = {'slug': ('name',)}
    filter_horizontal = ('tags',)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider', 'tenant', 'status', 'type', 'renewal_date', 'renewal_cost', 'currency', 'auto_renewal')
    list_filter = ('status', 'type', 'provider', 'tenant', 'auto_renewal', 'tags', 'billing_cycle')
    search_fields = ('name', 'provider__name', 'contract_reference', 'cost_center', 'description', 'notes', 'tenant__name')
    readonly_fields = ('created_at', 'updated_at', 'is_expired_display', 'days_until_renewal_display')
    raw_id_fields = ('provider', 'owner', 'tenant')
    filter_horizontal = ('tags',)
    inlines = [SubscriptionAssignmentInline]
    date_hierarchy = 'renewal_date'
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ('status',)

    fieldsets = (
        ('Identity', {'fields': ('name', 'slug', 'provider', 'type', 'status', 'tenant')}),
        ('Dates & Terms', {'fields': ('start_date', 'renewal_date', 'term_months', 'auto_renewal', 'cancellation_date')}),
        ('Costs', {'fields': ('renewal_cost', 'currency', 'billing_cycle', 'cost_center')}),
        ('Details', {'fields': ('licensed_quantity', 'contract_reference', 'owner')}),
        ('Notes', {'fields': ('description', 'notes', 'tags')}),
        ('Computed', {'fields': ('created_at', 'updated_at', 'is_expired_display', 'days_until_renewal_display')}),
    )

    @admin.display(description='Expired?', boolean=True, ordering='renewal_date')
    def is_expired_display(self, obj):
        return obj.is_expired

    @admin.display(description='Days Until Renewal')
    def days_until_renewal_display(self, obj):
        days = obj.days_until_renewal
        if days is None:
            return '—'
        return f'{days} days' if days >= 0 else f'{abs(days)} days overdue'

    def get_actions(self, request):
        actions = super().get_actions(request)
        return actions

    @admin.action(description='Mark selected as Expired')
    def mark_expired(self, request, queryset):
        updated = queryset.update(status='expired')
        self.message_user(request, f'{updated} subscription(s) marked as expired.')

    @admin.action(description='Mark selected as Cancelled')
    def mark_cancelled(self, request, queryset):
        from django.utils import timezone
        updated = queryset.update(status='cancelled', cancellation_date=timezone.now().date())
        self.message_user(request, f'{updated} subscription(s) cancelled.')


@admin.register(SubscriptionAssignment)
class SubscriptionAssignmentAdmin(admin.ModelAdmin):
    list_display = ('subscription', 'content_type', 'object_id', 'assigned_date', 'assigned_by')
    list_filter = ('content_type', 'subscription__provider', 'subscription__status')
    search_fields = ('subscription__name', 'notes')
    raw_id_fields = ('subscription', 'assigned_by')
    readonly_fields = ('assigned_date',)
    autocomplete_fields = ('subscription',)
