"""Extras-owned generic-presentation provider."""

from core.forms import JournalEntryForm
from extras.models import Bookmark, CustomField, FileAttachment, ImageAttachment, JournalEntry, ObjectWatch
from itambox.registry import (
    DetailContextInput,
    ListContextInput,
    ListFilterInput,
    ListParamsInput,
    ListParamsResult,
)


def _custom_field_context(input: DetailContextInput) -> dict[str, object]:
    data = getattr(input.obj, "custom_field_data", None) or {}
    if not data:
        return {"custom_fields_display": []}
    labels = dict(CustomField.objects.filter(object_types=input.content_type).values_list("name", "label"))
    return {"custom_fields_display": [(labels.get(name, name), value) for name, value in sorted(data.items())]}


def _bookmark_context(input: DetailContextInput) -> dict[str, object]:
    is_bookmarked = False
    if input.request.user.is_authenticated:
        is_bookmarked = Bookmark.objects.filter(
            user=input.request.user,
            model=input.content_type,
            object_id=input.obj.pk,
        ).exists()
    return {
        "is_bookmarkable": True,
        "bookmark_content_type_id": input.content_type.pk,
        "is_bookmarked": is_bookmarked,
    }


def _watch_context(input: DetailContextInput) -> dict[str, object]:
    is_watched = False
    if input.request.user.is_authenticated:
        is_watched = ObjectWatch.objects.filter(
            user=input.request.user,
            model=input.content_type,
            object_id=input.obj.pk,
        ).exists()
    return {
        "is_watchable": True,
        "watch_content_type_id": input.content_type.pk,
        "is_watched": is_watched,
    }


class _ExtrasGenericPresentationProvider:
    def resolve_list_params(self, input: ListParamsInput) -> ListParamsResult:
        return ListParamsResult(params=input.params, state={})

    def filter_list_queryset(self, input: ListFilterInput):
        return input.queryset

    def build_list_context(self, input: ListContextInput) -> dict[str, object]:
        return {}

    def build_detail_context(self, input: DetailContextInput) -> dict[str, object]:
        context: dict[str, object] = {
            "attachment_app_label": input.obj._meta.app_label,
            "attachment_model_name": input.obj._meta.model_name,
        }
        active_features = input.active_features

        if "journaling" in active_features:
            journal_entries = JournalEntry.objects.filter(
                model=input.content_type,
                object_id=input.obj.pk,
            )
            context.update(
                {
                    "has_journaling": True,
                    "journal_app_label": input.obj._meta.app_label,
                    "journal_model_name": input.obj._meta.model_name,
                    "journal_entries": journal_entries.select_related("user").order_by("-created")[:50],
                    "journal_entries_count": journal_entries.count(),
                    "journal_form": JournalEntryForm(),
                }
            )

        if "custom_field_data" in active_features:
            context.update(_custom_field_context(input))

        if "image_attachments" in active_features:
            context["image_attachments"] = ImageAttachment.objects.filter(
                model=input.content_type,
                object_id=input.obj.pk,
            ).order_by("-created")[:20]
            context["has_image_attachments"] = True

        if "file_attachments" in active_features:
            context["file_attachments"] = FileAttachment.objects.filter(
                model=input.content_type,
                object_id=input.obj.pk,
            ).order_by("-created")[:20]
            context["has_file_attachments"] = True

        if "job_file_attachments" in active_features:
            context["attachments"] = FileAttachment.objects.filter(
                model=input.content_type,
                object_id=input.obj.pk,
            )

        if "bookmarkable" in active_features:
            context.update(_bookmark_context(input))

        if "watchable" in active_features:
            context.update(_watch_context(input))

        return context


EXTRAS_GENERIC_PRESENTATION_PROVIDER = _ExtrasGenericPresentationProvider()
