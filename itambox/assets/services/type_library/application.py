"""Transactional Type Library apply orchestration.

The preparation function performs no database operations. The DB entry point
acquires the canonical catalogue lock, reauthorizes under that lock, recomputes
the same plan, and only then invokes the writer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.contenttypes.models import ContentType
from django.db import transaction

from assets.services.specifications._command_support import actor_change_context
from assets.services.specifications.locking import catalogue_transaction_lock
from assets.services.specifications.preview_tokens import PreviewTokenError
from assets.services.type_library.exporting import load_library_state
from assets.services.type_library.planning import (
    LibraryPlan,
    LibraryPlanningError,
    LibraryReconciliationState,
    plan_reconciliation,
    verify_library_preview_token,
)
from assets.services.type_library.writing import LibraryWriteError, write_library_document
from assets.services.type_library_validation import ValidatedLibraryDocument
from extras.models import SpecificationLibrary
from organization.services.access_scope import authentication_revision_for_actor


class LibraryApplyError(RuntimeError):
    """Safe, stable apply failure without leaking target existence."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True, slots=True)
class LibraryApplyRequest:
    """All claims bound by the preview token and required for apply."""

    plan: LibraryPlan
    token: str
    actor_id: int
    authentication_revision: str
    access_scope_fingerprint: str | None
    signing_key: str | bytes


@dataclass(frozen=True, slots=True)
class LibraryApplyResult:
    """Stable result returned after the transaction commits."""

    namespace: str
    release: int
    plan_digest: str
    source_digest: str
    changed_action_ids: tuple[str, ...]
    no_op: bool


def prepare_library_apply(
    request: LibraryApplyRequest,
    incoming: ValidatedLibraryDocument,
    current_state: LibraryReconciliationState,
    *,
    authorize: Callable[[], bool],
) -> LibraryPlan:
    """Reauthorize and recompute a preview plan against current state.

    This is the mandatory pre-write seam.  Callers must invoke it only after
    taking the target/database locks; it intentionally never invokes a writer.
    """

    _validate_apply_inputs(request, incoming, current_state, authorize)
    _verify_apply_token(request)
    _ensure_current_state_is_current(request, current_state)
    recomputed = _recompute_apply_plan(request, incoming, current_state)
    _ensure_plan_is_current(request.plan, recomputed)
    return recomputed


def _validate_apply_inputs(
    request: LibraryApplyRequest,
    incoming: ValidatedLibraryDocument,
    current_state: LibraryReconciliationState,
    authorize: Callable[[], bool],
) -> None:
    if not isinstance(request, LibraryApplyRequest):
        raise TypeError("request must be a LibraryApplyRequest")
    if not isinstance(incoming, ValidatedLibraryDocument):
        raise TypeError("incoming must be a ValidatedLibraryDocument")
    if not isinstance(current_state, LibraryReconciliationState):
        raise TypeError("current_state must be a LibraryReconciliationState")
    if not callable(authorize):
        raise TypeError("authorize must be callable")
    if not authorize():
        raise LibraryApplyError("OBJECT_UNAVAILABLE")


def _verify_apply_token(request: LibraryApplyRequest) -> None:
    try:
        verify_library_preview_token(
            request.token,
            request.plan,
            actor_id=request.actor_id,
            authentication_revision=request.authentication_revision,
            access_scope_fingerprint=request.access_scope_fingerprint,
            signing_key=request.signing_key,
        )
    except PreviewTokenError as exc:
        raise LibraryApplyError("STALE_PLAN") from exc


def _recompute_apply_plan(
    request: LibraryApplyRequest,
    incoming: ValidatedLibraryDocument,
    current_state: LibraryReconciliationState,
) -> LibraryPlan:
    try:
        recomputed = plan_reconciliation(
            current_state,
            incoming,
            resolutions=dict(request.plan.resolutions),
        )
    except LibraryPlanningError as exc:
        raise LibraryApplyError(exc.code) from exc
    if not recomputed.can_apply:
        raise LibraryApplyError("CONFLICT")
    return recomputed


def _ensure_current_state_is_current(
    expected: LibraryApplyRequest,
    current_state: LibraryReconciliationState,
) -> None:
    if expected.plan.current_digest != current_state.current_digest:
        raise LibraryApplyError("STALE_PLAN")


def _ensure_plan_is_current(expected: LibraryPlan, recomputed: LibraryPlan) -> None:
    if recomputed.plan_digest != expected.plan_digest:
        raise LibraryApplyError("STALE_PLAN")


def apply_library_plan(
    request: LibraryApplyRequest,
    incoming: ValidatedLibraryDocument,
    *,
    actor: object,
    using: str = "default",
) -> LibraryApplyResult:
    """Apply a validated library plan in the canonical exclusive transaction.

    The production path is intentionally not injectable: it always uses the
    existing Library models, exporter state loader, and dedicated writer.
    """

    with transaction.atomic(using=using):
        with catalogue_transaction_lock(using=using, exclusive=True):
            fresh_actor = _reauthorize_apply_actor(actor, request, using=using)
            return _apply_library_plan_locked(request, incoming, fresh_actor, using=using)


def _apply_library_plan_locked(
    request: LibraryApplyRequest,
    incoming: ValidatedLibraryDocument,
    fresh_actor: object,
    *,
    using: str,
) -> LibraryApplyResult:

    library = (
        SpecificationLibrary.objects.using(using).select_for_update().filter(namespace=request.plan.namespace).first()
    )
    if not _has_library_model_permission(
        fresh_actor,
        SpecificationLibrary,
        "change_specificationlibrary",
        using=using,
    ):
        raise LibraryApplyError("OBJECT_UNAVAILABLE")

    with actor_change_context(fresh_actor):
        if library is None:
            source = incoming.normalized_document
            if incoming.kind == "itambox.type-library.snapshot":
                source = source["upstream"]
            library = SpecificationLibrary(
                namespace=request.plan.namespace,
                label=source["library"].get("label", ""),
            )
            library.save(using=using)

        current_state = load_library_state(library)
        prepared = prepare_library_apply(
            request,
            incoming,
            current_state,
            authorize=lambda: _has_library_model_permission(
                fresh_actor,
                SpecificationLibrary,
                "change_specificationlibrary",
                using=using,
            ),
        )
        try:
            changed = tuple(write_library_document(library, incoming, prepared, using))
        except LibraryWriteError as exc:
            raise LibraryApplyError(exc.code) from exc
        return LibraryApplyResult(
            namespace=prepared.namespace,
            release=prepared.incoming_release,
            plan_digest=prepared.plan_digest,
            source_digest=prepared.source_digest,
            changed_action_ids=changed,
            no_op=not changed,
        )


def _reauthorize_apply_actor(actor: object, request: LibraryApplyRequest, *, using: str) -> object:
    actual_actor_id = getattr(actor, "pk", None)
    if actual_actor_id != request.actor_id:
        raise LibraryApplyError("OBJECT_UNAVAILABLE")
    fresh_actor = _reload_library_actor(actor, using=using)
    if fresh_actor is None:
        raise LibraryApplyError("OBJECT_UNAVAILABLE")

    if authentication_revision_for_actor(fresh_actor) != request.authentication_revision:
        raise LibraryApplyError("STALE_PLAN")

    if not _has_library_model_permission(
        fresh_actor,
        SpecificationLibrary,
        "change_specificationlibrary",
        using=using,
    ):
        raise LibraryApplyError("OBJECT_UNAVAILABLE")
    return fresh_actor


def _reload_library_actor(actor: object, *, using: str) -> object | None:

    actor_id = getattr(actor, "pk", None)
    if type(actor_id) is not int or actor_id <= 0:
        return None
    return get_user_model()._base_manager.using(using).filter(pk=actor_id, is_active=True).first()


def _has_library_model_permission(
    actor: object,
    model: type,
    codename: str,
    *,
    using: str,
) -> bool:
    """Check a freshly loaded actor through the requested database alias."""

    if getattr(actor, "is_superuser", False):
        return True
    content_type = ContentType.objects.db_manager(using).get_for_model(model)
    permission = Permission.objects.using(using).filter(content_type=content_type, codename=codename).first()
    if permission is None:
        return False
    user_permissions = getattr(actor, "user_permissions", None)
    if user_permissions is not None and user_permissions.using(using).filter(pk=permission.pk).exists():
        return True
    groups = getattr(actor, "groups", None)
    return groups is not None and groups.using(using).filter(permissions__pk=permission.pk).exists()


__all__ = [
    "LibraryApplyError",
    "LibraryApplyRequest",
    "LibraryApplyResult",
    "apply_library_plan",
    "prepare_library_apply",
]
