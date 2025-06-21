from django.contrib import admin
from .models import License, LicenseSeatAssignment

@admin.register(License)
class LicenseAdmin(admin.ModelAdmin):
    list_display = ('name', 'software', 'tenant', 'license_type', 'seats', 'available_seats', 'get_renewal_date', 'expiration_date')
    list_filter = ('software__manufacturer', 'license_type', 'software', 'tenant')
    search_fields = ('name', 'software__name', 'product_key', 'notes', 'order_number', 'tenant__name')
    readonly_fields = ('available_seats',)
    filter_horizontal = ('tags',)

    @admin.display(description='Renewal Date', ordering='renewal_date')
    def get_renewal_date(self, obj):
        return obj.renewal_date

@admin.register(LicenseSeatAssignment)
class LicenseSeatAssignmentAdmin(admin.ModelAdmin):
    list_display = ('license', 'asset', 'assigned_holder', 'assigned_date')
    list_filter = ('license__software__manufacturer', 'license__software', 'license')
    search_fields = ('license__name', 'asset__name', 'assigned_holder__upn', 'notes')
    # Make FKs easier to select
    raw_id_fields = ('license', 'asset', 'assigned_holder')
