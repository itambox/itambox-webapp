"""Command-backed browser views for the global Type Library workflow."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse
from django.shortcuts import render
from django.utils.http import content_disposition_header
from django.utils.translation import gettext_lazy as _
from django.views.generic import DetailView, FormView, ListView
from django.views.generic.detail import SingleObjectMixin

from assets.library_forms import (
    LibraryApplyForm,
    LibraryExportForm,
    LibraryUploadForm,
    deserialize_library_plan,
    resolution_field_name,
    serialize_library_plan,
)
from assets.services.type_library.application import (
    LibraryApplyError,
    LibraryApplyRequest,
    _has_library_model_permission,
)
from assets.services.type_library.commands import (
    LibraryCommandError,
    apply_library,
    export_library,
    preview_library,
)
from assets.services.type_library.exporting import LibraryExportError
from core.tables.constants import TABLE_EMPTY_VALUE
from extras.models import SpecificationLibrary
from organization.services.access_scope import authentication_revision_for_actor

_MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
_REFRESH_AFTER_APPLY_ERRORS = frozenset({"STALE_PLAN", "OBJECT_UNAVAILABLE", "CONFLICT"})


def _permission_codename(user: object, codename: str) -> bool:
    return bool(
        _has_library_model_permission(
            user,
            SpecificationLibrary,
            codename,
            using="default",
        )
    )


def _format_issue(issue: object) -> str:
    code = getattr(issue, "code", "LIBRARY_ERROR")
    path = getattr(issue, "path", ())
    message = getattr(issue, "message", str(issue))
    rendered_path = "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in path).lstrip(".")
    location = rendered_path or "document"
    return f"{code} at {location}: {message}"


def _add_command_errors(form: Any, error: LibraryCommandError | LibraryExportError) -> None:
    for issue in getattr(error, "issues", ()):
        form.add_error(None, _format_issue(issue))


def _json_value(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _plan_context(plan: object, apply_form: LibraryApplyForm | None = None) -> dict[str, object]:
    actions = tuple(getattr(plan, "actions", ()))
    counts = Counter(getattr(action, "action", "unknown") for action in actions)
    rows = []
    for action in getattr(plan, "conflicts", ()):
        rows.append(
            {
                "action": action,
                "field": apply_form[resolution_field_name(action.action_id)] if apply_form is not None else None,
                "baseline": _json_value(action.baseline),
                "local": _json_value(action.local),
                "incoming": _json_value(action.incoming),
            }
        )
    return {
        "plan_counts": dict(counts),
        "conflict_rows": rows,
        "planned_paths": len(actions),
        "preserved_local_count": sum(1 for action in actions if getattr(action, "decision", None) == "keep_local"),
    }


def _apply_result_context(result: object, plan: object) -> dict[str, object]:
    changed_ids = tuple(getattr(result, "changed_action_ids", ()))
    actions = tuple(getattr(plan, "actions", ()))
    changed_actions = {action.action_id for action in actions if action.action_id in changed_ids}
    changed_counts = Counter(
        getattr(action, "action", "unknown") for action in actions if action.action_id in changed_actions
    )
    return {
        "apply_result": result,
        "changed_path_count": len(changed_ids),
        "changed_path_counts": dict(changed_counts),
        "result_no_op": bool(getattr(result, "no_op", False)),
        "preserved_local_count": sum(1 for action in actions if getattr(action, "decision", None) == "keep_local"),
    }


class _GlobalLibraryPermissionMixin:
    """Authorize the global library operation before any object lookup."""

    library_permission = "view_specificationlibrary"

    def dispatch(self, request, *args, **kwargs):
        if not _permission_codename(request.user, self.library_permission):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class TypeLibraryListView(LoginRequiredMixin, _GlobalLibraryPermissionMixin, ListView):
    """List globally installed library identities without tenant data."""

    model = SpecificationLibrary
    context_object_name = "libraries"
    template_name = "assets/type_library/list.html"
    library_permission = "view_specificationlibrary"

    def get_queryset(self):
        return SpecificationLibrary.objects.order_by("namespace")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["table_empty_value"] = TABLE_EMPTY_VALUE
        context["can_manage_library"] = _permission_codename(self.request.user, "manage_specification_library")
        context["global_impact_notice"] = _(
            "This global catalogue workflow changes definitions for all accessible tenants; tenant Asset values, "
            "assignments, audit history, and installation policy are not part of a Library document."
        )
        return context


class TypeLibraryDetailView(LoginRequiredMixin, _GlobalLibraryPermissionMixin, DetailView):
    """Show release history and explicit global impact for one library."""

    model = SpecificationLibrary
    context_object_name = "library"
    template_name = "assets/type_library/detail.html"
    library_permission = "view_specificationlibrary"

    def get_queryset(self):
        return SpecificationLibrary.objects.prefetch_related("releases").all()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["releases"] = tuple(self.object.releases.order_by("-sequence"))
        context["export_form"] = LibraryExportForm(current_namespace=self.object.namespace)
        context["table_empty_value"] = TABLE_EMPTY_VALUE
        context["can_manage_library"] = _permission_codename(self.request.user, "manage_specification_library")
        context["global_impact_notice"] = _(
            "Applying this Library updates the global definition catalogue. Tenant Asset values, assignments, "
            "audit/policy state, and tenant installation decisions remain excluded."
        )
        context["history_notice"] = _(
            "Release rows and the original source remain immutable; an effective snapshot includes current local "
            "overrides only when you explicitly acknowledge retained history."
        )
        return context


class TypeLibraryImportView(LoginRequiredMixin, _GlobalLibraryPermissionMixin, FormView):
    """Run upload, preview, resolution, and apply as separate explicit steps."""

    template_name = "assets/type_library/import.html"
    form_class = LibraryUploadForm
    library_permission = "manage_specification_library"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("upload_form", context.get("form", LibraryUploadForm()))
        context.setdefault("apply_form", None)
        context.setdefault("preview", None)
        context.setdefault("preview_context", {})
        context.setdefault(
            "global_impact_notice",
            _(
                "Apply changes the global catalogue only. Tenant Asset values, assignments, audit/policy state, "
                "and tenant installation policy are excluded from this document."
            ),
        )
        return context

    def form_invalid(self, form):
        return self._render_import(upload_form=form)

    def form_valid(self, form):
        document = form.cleaned_data["document"]
        raw = document.read(_MAX_DOCUMENT_BYTES + 1)
        if len(raw) > _MAX_DOCUMENT_BYTES:
            form.add_error("document", _("The JSON document exceeds the 10 MiB upload limit."))
            return self._render_import(upload_form=form)
        try:
            source_document = raw.decode("utf-8")
        except UnicodeDecodeError:
            form.add_error("document", _("The uploaded Library must be UTF-8 JSON."))
            return self._render_import(upload_form=form)
        return self._render_preview(source_document)

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action", "preview")
        if action in {"review_resolutions", "apply"}:
            return self._post_apply_workflow(action)
        return super().post(request, *args, **kwargs)

    def _post_apply_workflow(self, action: str):
        submitted_plan = None
        try:
            submitted_plan = deserialize_library_plan(self.request.POST.get("plan_payload", ""))
        except (TypeError, ValueError, ValidationError):
            # The bound form reports invalid payloads as field errors below.
            submitted_plan = None
        conflicts = getattr(submitted_plan, "conflicts", ()) if submitted_plan is not None else ()
        apply_form = LibraryApplyForm(self.request.POST, conflicts=conflicts)
        if not apply_form.is_valid():
            return self._render_import(apply_form=apply_form, preview=None)
        source_document = apply_form.cleaned_data["source_document"]
        if action == "review_resolutions":
            return self._render_preview(source_document, resolutions=apply_form.resolutions())
        return self._apply_original_plan(apply_form)

    def _render_preview(self, source_document: str, resolutions: dict[str, str] | None = None):
        try:
            preview = preview_library(
                source_document,
                actor=self.request.user,
                signing_key=settings.SECRET_KEY,
                access_scope_fingerprint=None,
                resolutions=resolutions,
            )
        except LibraryCommandError as error:
            form = LibraryUploadForm()
            _add_command_errors(form, error)
            return self._render_import(upload_form=form, preview=None)
        apply_form = LibraryApplyForm(
            initial={
                "source_document": source_document,
                "plan_payload": serialize_library_plan(preview.plan),
                "preview_token": preview.preview_token,
            },
            conflicts=preview.plan.conflicts,
        )
        return self._render_import(
            apply_form=apply_form,
            preview=preview,
            preview_context=_plan_context(preview.plan, apply_form),
        )

    def _apply_original_plan(self, apply_form: LibraryApplyForm):
        plan = deserialize_library_plan(apply_form.cleaned_data["plan_payload"])
        if not plan.can_apply:
            apply_form.add_error(None, _("Resolve every blocking conflict before applying the Library."))
            return self._render_import(apply_form=apply_form, preview=None, preview_context=_plan_context(plan))
        request = LibraryApplyRequest(
            plan=plan,
            token=apply_form.cleaned_data["preview_token"],
            actor_id=self.request.user.pk,
            authentication_revision=authentication_revision_for_actor(self.request.user),
            access_scope_fingerprint=None,
            signing_key=settings.SECRET_KEY,
        )
        try:
            result = apply_library(
                apply_form.cleaned_data["source_document"],
                request,
                actor=self.request.user,
            )
        except LibraryApplyError as error:
            message = {
                "STALE_PLAN": _(
                    "The Library changed after preview. A fresh preview is shown below; your uploaded draft was retained."
                ),
                "OBJECT_UNAVAILABLE": _(
                    "The Library operation is no longer available for this actor. The draft was retained."
                ),
                "CONFLICT": _("A new blocking conflict appeared during apply. A fresh preview is shown below."),
            }.get(error.code, _("The Library could not be applied."))
            if error.code in _REFRESH_AFTER_APPLY_ERRORS:
                return self._refresh_after_apply_error(apply_form.cleaned_data["source_document"], message)
            apply_form.add_error(None, message)
            return self._render_import(apply_form=apply_form, preview=None, stale_error=message)
        except LibraryCommandError as error:
            _add_command_errors(apply_form, error)
            return self._render_import(apply_form=apply_form, preview=None)
        return self._render_import(
            apply_form=None,
            preview=None,
            result=result,
            result_context=_apply_result_context(result, plan),
        )

    def _refresh_after_apply_error(self, source_document: str, message: str):
        try:
            preview = preview_library(
                source_document,
                actor=self.request.user,
                signing_key=settings.SECRET_KEY,
                access_scope_fingerprint=None,
            )
        except LibraryCommandError as error:
            form = LibraryApplyForm(initial={"source_document": source_document})
            form.add_error(None, message)
            _add_command_errors(form, error)
            return self._render_import(apply_form=form, preview=None, stale_error=message)
        apply_form = LibraryApplyForm(
            initial={
                "source_document": source_document,
                "plan_payload": serialize_library_plan(preview.plan),
                "preview_token": preview.preview_token,
            },
            conflicts=preview.plan.conflicts,
        )
        return self._render_import(
            apply_form=apply_form,
            preview=preview,
            preview_context=_plan_context(preview.plan, apply_form),
            stale_error=message,
        )

    def _render_import(
        self,
        *,
        upload_form=None,
        apply_form=None,
        preview=None,
        preview_context=None,
        result=None,
        result_context=None,
        stale_error=None,
    ):
        context = self.get_context_data(form=upload_form or LibraryUploadForm())
        context["upload_form"] = upload_form or LibraryUploadForm()
        context["apply_form"] = apply_form
        context["preview"] = preview
        context["preview_context"] = preview_context or {}
        context["result"] = result
        context["result_context"] = result_context or {}
        context["stale_error"] = stale_error
        return self.render_to_response(context)


class TypeLibraryExportView(LoginRequiredMixin, _GlobalLibraryPermissionMixin, SingleObjectMixin, FormView):
    """Download an explicit original, effective, or fork export."""

    model = SpecificationLibrary
    form_class = LibraryExportForm
    template_name = "assets/type_library/detail.html"
    library_permission = "manage_specification_library"

    def get_queryset(self):
        return SpecificationLibrary.objects.all()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["current_namespace"] = self.object.namespace
        return kwargs

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        return render(
            self.request,
            self.template_name,
            {
                "library": self.object,
                "table_empty_value": TABLE_EMPTY_VALUE,
                "releases": tuple(self.object.releases.order_by("-sequence")),
                "export_form": form,
                "can_manage_library": True,
                "global_impact_notice": _(
                    "Applying this Library updates the global definition catalogue. Tenant Asset values, assignments, "
                    "audit/policy state, and tenant installation decisions remain excluded."
                ),
                "history_notice": _(
                    "Release rows and the original source remain immutable; an effective snapshot includes current local "
                    "overrides only when you explicitly acknowledge retained history."
                ),
            },
            status=400,
        )

    def form_valid(self, form):
        try:
            artifact = export_library(
                self.object.namespace,
                actor=self.request.user,
                mode=form.cleaned_data["mode"],
                new_namespace=form.cleaned_data.get("new_namespace") or None,
                acknowledge_retained_history=form.cleaned_data.get("acknowledge_retained_history", False),
            )
        except (LibraryCommandError, LibraryExportError) as error:
            _add_command_errors(form, error)
            return self.form_invalid(form)
        filename = f"{artifact.namespace}-{artifact.mode}.json"
        response = HttpResponse(artifact.canonical_bytes, content_type="application/json; charset=utf-8")
        response["Content-Disposition"] = content_disposition_header(True, filename)
        response["X-Library-Export-Mode"] = artifact.mode
        response["X-Library-Source-Digest"] = artifact.source_digest
        return response


__all__ = [
    "TypeLibraryDetailView",
    "TypeLibraryExportView",
    "TypeLibraryImportView",
    "TypeLibraryListView",
]
