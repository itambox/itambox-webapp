"""Public Type Library commands consumed by adapters.

The adapter boundary stays deliberately small: raw bounded JSON enters preview or
apply, and the existing validated plan/token/export artifact contracts leave it.
No HTTP, GraphQL, or form transport concerns belong here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from assets.services.type_library.application import (
    LibraryApplyRequest,
    LibraryApplyResult,
    apply_library_plan,
)
from assets.services.type_library.exporting import (
    LibraryExportArtifact,
    export_effective_snapshot,
    export_fork,
    export_original_release,
    load_library_state,
)
from assets.services.type_library.planning import (
    LibraryPlan,
    LibraryPlanningError,
    LibraryReconciliationState,
    issue_library_preview_token,
    plan_reconciliation,
)
from assets.services.type_library_validation import (
    InstalledDependency,
    LibraryValidationError,
    ValidatedLibraryDocument,
    validate_library_document,
)
from assets.services.type_library_validation.errors import ValidationIssue

ExportMode = Literal["original_release", "effective_snapshot", "fork"]


class LibraryCommandError(RuntimeError):
    """Stable transport-neutral error for preview/export command callers."""

    def __init__(
        self,
        code: str,
        path: tuple[str | int, ...] = (),
        message: str | None = None,
        *,
        issues: tuple[ValidationIssue, ...] = (),
    ):
        self.code = code
        self.path = path
        self.issues = issues or (ValidationIssue(code, path, message or code),)
        super().__init__("; ".join(str(issue) for issue in self.issues))


@dataclass(frozen=True, slots=True)
class LibraryPreviewResult:
    """A plan and signed token ready for an explicit apply request."""

    validated: ValidatedLibraryDocument
    plan: LibraryPlan
    preview_token: str


def preview_library(
    document: bytes | str,
    *,
    actor: object,
    signing_key: str | bytes,
    access_scope_fingerprint: str | None = None,
    using: str = "default",
) -> LibraryPreviewResult:
    """Validate and preview a release against the current DB-backed state."""

    from django.db import transaction

    from assets.services.specifications._command_support import has_global_model_permission
    from assets.services.specifications.locking import catalogue_transaction_lock
    from extras.models import SpecificationLibrary
    from organization.services.access_scope import authentication_revision_for_actor

    with transaction.atomic(using=using):
        with catalogue_transaction_lock(using=using, exclusive=False):
            _authorize(actor, SpecificationLibrary, has_global_model_permission)
            installed = _installed_dependencies(using)
            incoming = _validate_document(document, installed)
            source = incoming.normalized_document
            if incoming.kind == "itambox.type-library.snapshot":
                source = source["upstream"]
            namespace = source["library"]["namespace"]
            library = (
                SpecificationLibrary.objects.using(using)
                .filter(namespace=namespace)
                .first()
            )
            if library is None:
                state = LibraryReconciliationState(
                    namespace=namespace,
                    accepted_release=None,
                    baseline_document=None,
                    effective_document=None,
                )
            else:
                state = load_library_state(library)
            try:
                plan = plan_reconciliation(state, incoming)
            except LibraryPlanningError as exc:
                raise LibraryCommandError(exc.code) from exc
            authentication_revision = authentication_revision_for_actor(actor)
            token = issue_library_preview_token(
                plan,
                actor_id=actor.pk,
                authentication_revision=authentication_revision,
                access_scope_fingerprint=access_scope_fingerprint,
                signing_key=signing_key,
            )
            return LibraryPreviewResult(validated=incoming, plan=plan, preview_token=token)


def apply_library(
    document: bytes | str,
    request: LibraryApplyRequest,
    *,
    actor: object,
    using: str = "default",
) -> LibraryApplyResult:
    """Validate raw source and apply it through the real locked transaction."""

    installed = _installed_dependencies(using)
    incoming = _validate_document(document, installed)
    return apply_library_plan(request, incoming, actor=actor, using=using)


def export_library(
    namespace: str,
    *,
    actor: object,
    mode: ExportMode = "effective_snapshot",
    new_namespace: str | None = None,
    acknowledge_retained_history: bool = False,
    using: str = "default",
) -> LibraryExportArtifact:
    """Export the accepted source, effective snapshot, or deliberate fork."""

    from django.db import transaction

    from assets.services.specifications._command_support import has_global_model_permission
    from assets.services.specifications.locking import catalogue_transaction_lock
    from extras.models import SpecificationLibrary

    if mode not in {"original_release", "effective_snapshot", "fork"}:
        raise LibraryCommandError("INVALID_EXPORT_MODE", ("mode",))
    if mode == "fork" and new_namespace is None:
        raise LibraryCommandError("INVALID_NAMESPACE", ("new_namespace",))
    with transaction.atomic(using=using):
        with catalogue_transaction_lock(using=using, exclusive=False):
            _authorize(actor, SpecificationLibrary, has_global_model_permission)
            library = (
                SpecificationLibrary.objects.using(using)
                .select_related("accepted_release")
                .filter(namespace=namespace)
                .first()
            )
            if library is None or library.accepted_release is None:
                raise LibraryCommandError("OBJECT_UNAVAILABLE")
            installed = _installed_dependencies(using)
            release = library.accepted_release.source_document
            if mode == "original_release":
                return export_original_release(release, installed_dependencies=installed)
            if mode == "fork":
                return export_fork(
                    release,
                    new_namespace=new_namespace,
                    installed_dependencies=installed,
                )
            from assets.services.type_library.writing import effective_definitions_from_library

            return export_effective_snapshot(
                release,
                effective_definitions_from_library(library),
                installed_dependencies=installed,
                acknowledge_retained_history=acknowledge_retained_history,
            )


def _authorize(actor: object, model: type, checker: Any) -> None:
    if not getattr(actor, "is_active", False) and not getattr(actor, "is_superuser", False):
        raise LibraryCommandError("OBJECT_UNAVAILABLE")
    if not checker(actor, model, "change_specificationlibrary"):
        raise LibraryCommandError("OBJECT_UNAVAILABLE")


def _installed_dependencies(using: str) -> tuple[InstalledDependency, ...]:
    from extras.models import SpecificationLibraryRelease

    releases = (
        SpecificationLibraryRelease.objects.using(using)
        .select_related("library")
        .all()
    )
    return tuple(
        InstalledDependency(
            namespace=release.library.namespace,
            release=release.sequence,
            digest=release.semantic_digest,
            document=release.source_document,
        )
        for release in releases
    )


def _validate_document(
    document: bytes | str,
    installed: tuple[InstalledDependency, ...],
) -> ValidatedLibraryDocument:
    try:
        return validate_library_document(document, installed_dependencies=installed)
    except LibraryValidationError as exc:
        raise LibraryCommandError(exc.code, exc.path, issues=exc.issues) from exc


__all__ = [
    "ExportMode",
    "LibraryCommandError",
    "LibraryPreviewResult",
    "apply_library",
    "export_library",
    "preview_library",
]
