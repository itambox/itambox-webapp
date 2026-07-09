from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from .models import Provider, Subscription, SubscriptionAssignment


class SubscriptionAssignmentInline(admin.TabularInline):
    model = SubscriptionAssignment
    extra = 0
    readonly_fields = ('assigned_date',)
    raw_id_fields = ('assigned_by',)
    fields = ('content_type', 'object_id', 'assigned_date', 'assigned_by', 'notes')


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'is_active', 'account_id')
    list_filter = ('is_active', 'tags')
    search_fields = ('name', 'account_id', 'admin_notes')
    readonly_fields = ('created_at', 'updated_at')
    prepopulated_fields = {'slug': ('name',)}
    filter_horizontal = ('tags',)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider', 'tenant', 'status', 'type', 'renewal_date', 'renewal_cost', 'currency', 'auto_renewal')
    list_filter = ('status', 'type', 'provider', 'tenant', 'auto_renewal', 'tags', 'billing_cycle')
    search_fields = ('name', 'provider__name', 'contract_reference', 'cost_center__name', 'description', 'notes', 'tenant__name')
    readonly_fields = ('created_at', 'updated_at', 'is_expired_display', 'days_until_renewal_display')
    raw_id_fields = ('provider', 'owner', 'tenant', 'cost_center')
    filter_horizontal = ('tags',)
    inlines = [SubscriptionAssignmentInline]
    date_hierarchy = 'renewal_date'
    prepopulated_fields = {'slug': ('name',)}
    list_editable = ('status',)

    fieldsets = (
        (_('Identity'), {'fields': ('name', 'slug', 'provider', 'type', 'status', 'tenant')}),
        (_('Dates & Terms'), {'fields': ('start_date', 'renewal_date', 'term_months', 'auto_renewal', 'cancellation_date')}),
        (_('Costs'), {'fields': ('renewal_cost', 'currency', 'billing_cycle', 'cost_center')}),
        (_('Details'), {'fields': ('licensed_quantity', 'contract_reference', 'owner')}),
        (_('Notes'), {'fields': ('description', 'notes', 'tags')}),
        (_('Computed'), {'fields': ('created_at', 'updated_at', 'is_expired_display', 'days_until_renewal_display')}),
    )

    @admin.display(description=_('Expired?'), boolean=True, ordering='renewal_date')
    def is_expired_display(self, obj):
        return obj.is_expired

    @admin.display(description=_('Days Until Renewal'))
    def days_until_renewal_display(self, obj):
        days = obj.days_until_renewal
        if days is None:
            return '—'
        return f'{days} days' if days >= 0 else f'{abs(days)} days overdue'

    def get_actions(self, request):
        actions = super().get_actions(request)
        return actions

    @admin.action(description=_('Mark selected as Expired'))
    def mark_expired(self, request, queryset):
        # Save per-instance so each status change is change-logged
        # (QuerySet.update() bypasses ChangeLoggingMixin.save()).
        updated = 0
        for subscription in queryset:
            subscription.status = 'expired'
            subscription.save(update_fields=['status'])
            updated += 1
        self.message_user(request, f'{updated} subscription(s) marked as expired.')

    @admin.action(description=_('Mark selected as Cancelled'))
    def mark_cancelled(self, request, queryset):
        from django.utils import timezone
        cancellation_date = timezone.now().date()
        updated = 0
        for subscription in queryset:
            subscription.status = 'cancelled'
            subscription.cancellation_date = cancellation_date
            subscription.save(update_fields=['status', 'cancellation_date'])
            updated += 1
        self.message_user(request, f'{updated} subscription(s) cancelled.')


@admin.register(SubscriptionAssignment)
class SubscriptionAssignmentAdmin(admin.ModelAdmin):
    list_display = ('subscription', 'content_type', 'object_id', 'assigned_date', 'assigned_by')
    list_filter = ('content_type', 'subscription__provider', 'subscription__status')
    search_fields = ('subscription__name', 'notes')
    raw_id_fields = ('subscription', 'assigned_by')
    readonly_fields = ('assigned_date',)
    autocomplete_fields = ('subscription',)
