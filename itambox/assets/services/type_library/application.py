"""Transactional Type Library apply orchestration.

The public pure seam below is deliberately independent of Django.  The DB entry
point later in this module acquires the canonical catalogue lock, reauthorizes
under that lock, recomputes this same plan, and only then invokes the writer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from assets.services.specifications.preview_tokens import PreviewTokenError
from assets.services.type_library.planning import (
    LibraryPlan,
    LibraryPlanningError,
    LibraryReconciliationState,
    plan_reconciliation,
    verify_library_preview_token,
)
from assets.services.type_library_validation import ValidatedLibraryDocument


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
    actor_id: str | int
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


def _ensure_plan_is_current(expected: LibraryPlan, recomputed: LibraryPlan) -> None:
    if recomputed.plan_digest != expected.plan_digest:
        raise LibraryApplyError("STALE_PLAN")


def apply_library_plan(
    request: LibraryApplyRequest,
    incoming: ValidatedLibraryDocument,
    *,
    actor: object,
    using: str = "default",
    state_loader: Callable[[Any], LibraryReconciliationState] | None = None,
    writer: Callable[[Any, ValidatedLibraryDocument, LibraryPlan, str], tuple[str, ...]] | None = None,
) -> LibraryApplyResult:
    """Apply a validated library plan in the canonical exclusive transaction.

    ``state_loader`` and ``writer`` are narrow seams for the real domain reader
    and writer; production callers leave them unset.  They are not transports
    or alternate persistence paths.  The default path is wired to the Library
    models and the dedicated exporter/writer below.
    """

    from django.db import transaction

    from assets.services._command_support import has_global_model_permission
    from assets.services.specifications.locking import catalogue_transaction_lock
    from assets.services.type_library.exporting import load_library_state
    from assets.services.type_library.writing import write_library_document
    from extras.models import SpecificationLibrary

    with transaction.atomic(using=using):
        with catalogue_transaction_lock(using=using, exclusive=True):
            library = (
                SpecificationLibrary.objects.using(using)
                .select_for_update()
                .filter(namespace=request.plan.namespace)
                .first()
            )
            if library is None:
                raise LibraryApplyError("OBJECT_UNAVAILABLE")
            if not getattr(actor, "is_active", False) and not getattr(actor, "is_superuser", False):
                raise LibraryApplyError("OBJECT_UNAVAILABLE")
            if not has_global_model_permission(actor, SpecificationLibrary, "change_specificationlibrary"):
                raise LibraryApplyError("OBJECT_UNAVAILABLE")

            current_state = (state_loader or load_library_state)(library)
            prepared = prepare_library_apply(
                request,
                incoming,
                current_state,
                authorize=lambda: has_global_model_permission(
                    actor, SpecificationLibrary, "change_specificationlibrary"
                ),
            )
            write = writer or write_library_document
            changed = tuple(write(library, incoming, prepared, using))
            return LibraryApplyResult(
                namespace=prepared.namespace,
                release=prepared.incoming_release,
                plan_digest=prepared.plan_digest,
                source_digest=prepared.source_digest,
                changed_action_ids=changed,
                no_op=not changed,
            )


__all__ = [
    "LibraryApplyError",
    "LibraryApplyRequest",
    "LibraryApplyResult",
    "apply_library_plan",
    "prepare_library_apply",
]
