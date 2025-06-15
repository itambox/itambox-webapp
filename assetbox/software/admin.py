from django.contrib import admin
from .models import Software

@admin.register(Software)
class SoftwareAdmin(admin.ModelAdmin):
    list_display = ('name', 'manufacturer', 'description')
    list_filter = ('manufacturer', 'tags')
    search_fields = ('name', 'manufacturer__name', 'description')
    filter_horizontal = ('tags',) # Better widget for M2M 