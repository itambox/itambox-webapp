from django.contrib import admin
from django.contrib.admin import helpers
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.text import capfirst
from django.utils.translation import gettext_lazy as _

from software.models import InstalledSoftware

from .models import Asset, AssetDisposal, AssetReservation, AssetRole, AssetType, Manufacturer, Warranty
from .services import (
    DISPOSAL_METADATA_FIELDS,
    disposal_service_payload,
    dispose_asset,
    update_asset_disposal,
)

# Register your models here.


@admin.register(AssetRole)
class AssetRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "asset_count")
    prepopulated_fields = {"slug": ("name",)}

    def asset_count(self, obj):
        return obj.assets.count()

    asset_count.short_description = _("Assets")


@admin.register(Manufacturer)
class ManufacturerAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "asset_count")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)

    def asset_count(self, obj):
        return obj.assets.count()

    asset_count.short_description = _("Assets")


@admin.register(AssetType)
class AssetTypeAdmin(admin.ModelAdmin):
    list_display = ("manufacturer", "model", "slug", "part_number")
    list_filter = ("manufacturer",)
    search_fields = ("manufacturer__name", "model", "slug", "part_number")
    prepopulated_fields = {
        "slug": (
            "manufacturer",
            "model",
        )
    }


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("name", "asset_tag", "status", "tenant", "manufacturer", "model", "asset_role", "location")
    list_filter = ("status", "asset_role", "asset_type__manufacturer", "location", "asset_type", "tenant")
    search_fields = ("name", "asset_tag", "serial_number", "asset_type__model", "tenant__name")


@admin.register(InstalledSoftware)
class InstalledSoftwareAdmin(admin.ModelAdmin):
    list_display = ("asset", "software", "version_detected", "last_seen_date", "discovered_by_agent")
    list_filter = ("software__manufacturer", "software", "discovered_by_agent", "asset__location")
    search_fields = ("asset__name", "asset__asset_tag", "software__name", "version_detected", "notes")
    date_hierarchy = "last_seen_date"
    raw_id_fields = ("asset", "software")


@admin.register(AssetDisposal)
class AssetDisposalAdmin(admin.ModelAdmin):
    list_display = (
        "asset",
        "disposal_method",
        "disposal_date",
        "data_sanitization_method",
        "weee_compliant",
        "sanitized_by",
        "recipient",
        "is_active",
        "cancelled_at",
    )
    list_filter = ("disposal_method", "data_sanitization_method", "weee_compliant", "cancelled_at")
    search_fields = (
        "asset__name",
        "asset__asset_tag",
        "asset__serial_number",
        "sanitization_certificate",
        "sanitized_by",
        "recipient",
    )
    date_hierarchy = "disposal_date"
    raw_id_fields = ("asset",)
    # Owned by cancel_asset_disposal; never editable in the admin (#496).
    readonly_fields = ("cancelled_at", "cancelled_by", "cancellation_reason")

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            # A record's asset identity is immutable.
            fields.append("asset")
        return tuple(fields)

    def has_delete_permission(self, request, obj=None):
        # Evidence (#496): the admin never deletes a disposal record; cancel it.
        return False

    def save_model(self, request, obj, form, change):
        """Route every admin write through the disposal services (#496).

        The services own the lifecycle effects (stamps, status, atomicity) and
        the duplicate/cancellation rules. A service rejection is reported as an
        admin error message and nothing is written — the admin deliberately has
        no second, weaker write path.
        """
        payload = {name: getattr(obj, name) for name in DISPOSAL_METADATA_FIELDS}
        if change:
            updated = update_asset_disposal(
                obj.__class__.all_objects.get(pk=obj.pk),
                user=request.user,
                data=payload,
                request=request,
            )
        else:
            updated = dispose_asset(asset=obj.asset, user=request.user, **disposal_service_payload(payload))
        obj.pk = updated.pk
        for name in DISPOSAL_METADATA_FIELDS:
            setattr(obj, name, getattr(updated, name))

    def _changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        """Turn a write-time service rejection into a bound form error (#496 repair14).

        ``save_model`` must not swallow the rejection: Django's change form would then
        continue its success path (log entry, "was changed successfully" message,
        redirect) even though nothing was written. Raising instead aborts that path
        inside Django's own transaction, and this narrow adapter re-renders the SAME
        bound form with the service message as a non-field error. The service remains
        the only writer, and race-time rejections take the same safe route.
        """
        try:
            return super()._changeform_view(request, object_id, form_url, extra_context)
        except DjangoValidationError as exc:
            return self._service_rejection_response(request, object_id, exc)

    def _service_rejection_response(self, request, object_id, exc):
        obj = self.get_object(request, object_id) if object_id else None
        form_class = self.get_form(request, obj, change=obj is not None)
        form = form_class(request.POST, request.FILES, instance=obj)
        form.add_error(None, "; ".join(getattr(exc, "messages", [str(exc)])))
        adminform = helpers.AdminForm(
            form,
            list(self.get_fieldsets(request, obj)),
            self.get_prepopulated_fields(request, obj),
            self.get_readonly_fields(request, obj),
            model_admin=self,
        )
        context = {
            **self.admin_site.each_context(request),
            # no translatable literal here: the error path must not add new catalog keys
            "title": capfirst(str(self.model._meta.verbose_name))
            if obj is None
            else str(self.model._meta.verbose_name),
            "adminform": adminform,
            "media": self.media + adminform.media,
            "opts": self.model._meta,
            "obj": obj,
            "change": obj is not None,
            "add": obj is None,
            "is_popup": False,
            "save_as": False,
            "errors": form.errors,
            "inline_admin_formsets": [],
            "has_view_permission": self.has_view_permission(request, obj),
            "has_add_permission": self.has_add_permission(request),
            "has_change_permission": self.has_change_permission(request, obj),
            "has_delete_permission": False,
        }
        return self.render_change_form(request, context, add=obj is None, change=obj is not None, obj=obj)


@admin.register(Warranty)
class WarrantyAdmin(admin.ModelAdmin):
    list_display = ("asset", "warranty_type", "supplier", "start_date", "end_date", "reference")
    list_filter = ("warranty_type",)
    search_fields = ("asset__name", "asset__asset_tag", "supplier__name", "reference")
    date_hierarchy = "end_date"
    raw_id_fields = ("asset",)


@admin.register(AssetReservation)
class AssetReservationAdmin(admin.ModelAdmin):
    list_display = ("asset", "reserved_for", "start_date", "end_date", "status", "purpose")
    list_filter = ("status",)
    search_fields = ("asset__name", "asset__asset_tag", "purpose")
    date_hierarchy = "start_date"
    raw_id_fields = ("asset", "reserved_for")


# Registrations for Site, Region, SiteGroup, Tenant, Tag, Location moved to organization/admin.py
