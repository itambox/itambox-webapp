from django.contrib import admin
from .models import Tag, SavedFilter

# Register your models here.

@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
     prepopulated_fields = {"slug": ("name",)}
     list_display = ('name', 'slug', 'color', 'description')


@admin.register(SavedFilter)
class SavedFilterAdmin(admin.ModelAdmin):
    list_display = ('name', 'content_type', 'shared', 'enabled', 'tenant', 'created_by')
    list_filter = ('shared', 'enabled', 'content_type')
    search_fields = ('name', 'description')
    raw_id_fields = ('content_type', 'created_by', 'tenant')
