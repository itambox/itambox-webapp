from django.contrib import admin

from core.forms import TenantScopedAdminFormMixin

from .models import SavedFilter, Tag

# Register your models here.


@admin.register(Tag)
class TagAdmin(TenantScopedAdminFormMixin, admin.ModelAdmin):
    prepopulated_fields = {"slug": ("name",)}
    list_display = ("name", "slug", "color", "description")


@admin.register(SavedFilter)
class SavedFilterAdmin(TenantScopedAdminFormMixin, admin.ModelAdmin):
    list_display = ("name", "content_type", "shared", "enabled", "tenant", "created_by")
    list_filter = ("shared", "enabled", "content_type")
    search_fields = ("name", "description")
    raw_id_fields = ("content_type", "created_by", "tenant")
