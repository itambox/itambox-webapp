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

from assets.models.catalog import AssetType, Category, Manufacturer
from assets.services.specifications._command_support import actor_change_context, has_global_model_permission
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
from assets.services.type_library_validation.errors import ValidationIssue
from extras.models import (
    CustomField,
    CustomFieldChoice,
    CustomFieldChoiceSet,
    CustomFieldset,
    SpecificationLibrary,
)
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


_LIBRARY_MANAGE_PERMISSION = "manage_specification_library"


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
    if not _has_global_model_permission(
        fresh_actor,
        SpecificationLibrary,
        _LIBRARY_MANAGE_PERMISSION,
        using=using,
    ):
        raise LibraryApplyError("OBJECT_UNAVAILABLE")

    with actor_change_context(fresh_actor):
        if library is None:
            current_state = LibraryReconciliationState(
                namespace=request.plan.namespace,
                accepted_release=None,
                baseline_document=None,
                effective_document=None,
            )
        else:
            current_state = load_library_state(library)
        prepared = prepare_library_apply(
            request,
            incoming,
            current_state,
            authorize=lambda: _has_global_model_permission(
                fresh_actor,
                SpecificationLibrary,
                _LIBRARY_MANAGE_PERMISSION,
                using=using,
            ),
        )
        if not _has_library_plan_permissions(
            fresh_actor,
            library,
            incoming,
            prepared,
            using=using,
        ):
            raise LibraryApplyError("OBJECT_UNAVAILABLE")
        reference_issues = _catalogue_reference_issues(incoming, using=using)
        if reference_issues:
            raise LibraryApplyError(reference_issues[0].code, str(reference_issues[0]))
        if library is None:
            source = incoming.normalized_document
            if incoming.kind == "itambox.type-library.snapshot":
                source = source["upstream"]
            library = SpecificationLibrary(
                namespace=request.plan.namespace,
                label=source["library"].get("label", ""),
            )
            library.save(using=using)
        accepted_release_before = library.accepted_release_id
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
            no_op=not changed and library.accepted_release_id == accepted_release_before,
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

    if not _has_global_model_permission(
        fresh_actor,
        SpecificationLibrary,
        _LIBRARY_MANAGE_PERMISSION,
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
    """Backward-compatible alias for the centralized global check."""
    return _has_global_model_permission(actor, model, codename, using=using)


def _has_global_model_permission(
    actor: object,
    model: type,
    codename: str,
    *,
    using: str,
) -> bool:
    """Check a fresh actor's real global permission on the requested alias.

    The existing specification-command helper is authoritative on the default
    alias. The equivalent alias-aware query is kept local so a non-default
    worker database cannot accidentally consult ``default``.
    """
    if using == "default":
        return has_global_model_permission(actor, model, codename)
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


def _has_library_plan_permissions(
    actor: object,
    library: object | None,
    incoming: ValidatedLibraryDocument,
    plan: LibraryPlan,
    *,
    using: str,
) -> bool:
    """Authorize manage plus every model action the canonical writer may use."""
    if not _has_global_model_permission(
        actor,
        SpecificationLibrary,
        _LIBRARY_MANAGE_PERMISSION,
        using=using,
    ):
        return False
    return all(
        _has_global_model_permission(actor, model, codename, using=using)
        for model, codename in _required_library_permissions(library, incoming, plan, using=using)
    )


def _required_library_permissions(
    library: object | None,
    incoming: ValidatedLibraryDocument,
    plan: LibraryPlan,
    *,
    using: str,
) -> tuple[tuple[type, str], ...]:
    """Return concrete global add/change grants for the plan.

    A plan with no adopted upstream action is a true no-op from the
    model-action perspective and needs no add/change grant beyond manage.
    """
    actions = _planned_definition_actions(plan)
    if not actions:
        return ()
    required: set[tuple[type, str]] = set()
    _collect_definition_permissions(
        required,
        _incoming_definitions(incoming),
        actions,
        library,
        using=using,
    )
    _require_retirement_permissions(required, actions)
    return tuple(sorted(required, key=lambda item: (item[0]._meta.label_lower, item[1])))


def _planned_definition_actions(plan: LibraryPlan) -> dict[str, dict[str, set[str]]]:
    actions: dict[str, dict[str, set[str]]] = {}
    for action in plan.actions:
        if action.decision != "take_upstream" or len(action.path) < 3:
            continue
        if action.path[0] != "definitions":
            continue
        actions.setdefault(action.path[1], {}).setdefault(action.identity, set()).add(action.action)
    return actions


def _catalogue_reference_issues(incoming: ValidatedLibraryDocument, *, using: str) -> tuple[ValidationIssue, ...]:
    definitions = _incoming_definitions(incoming)
    prefix = "effective_definitions" if incoming.kind == "itambox.type-library.snapshot" else "definitions"
    issues = []
    for section, model in (("manufacturers", Manufacturer), ("categories", Category)):
        for index, item in enumerate(definitions.get(section, [])):
            slug = item["id"].split("/", 1)[1]
            collision = (
                model._base_manager.using(using)
                .filter(name=item["label"], deleted_at__isnull=True)
                .exclude(slug=slug)
                .exists()
            )
            if collision:
                issues.append(
                    ValidationIssue(
                        "IDENTITY_COLLISION",
                        (prefix, section, index, "label"),
                        "Catalogue name already belongs to another identity",
                    )
                )
    return tuple(issues)


def _incoming_definitions(incoming: ValidatedLibraryDocument) -> dict[str, list[dict[str, object]]]:
    document = incoming.normalized_document
    if incoming.kind == "itambox.type-library.snapshot":
        document = document["upstream"]
    return document.get("definitions", {})


def _collect_definition_permissions(
    required: set[tuple[type, str]],
    definitions: dict[str, list[dict[str, object]]],
    actions: dict[str, dict[str, set[str]]],
    library: object | None,
    *,
    using: str,
) -> None:
    handlers = {
        "choice_sets": _require_choice_set_item,
        "fields": _require_field_item,
        "fieldsets": _require_fieldset_item,
        "asset_types": _require_asset_type_item,
    }
    models = {
        "choice_sets": CustomFieldChoiceSet,
        "fields": CustomField,
        "fieldsets": CustomFieldset,
        "categories": Category,
        "manufacturers": Manufacturer,
        "asset_types": AssetType,
    }
    for section, model in models.items():
        for item in definitions.get(section, []):
            _require_definition_item(
                required,
                section,
                model,
                item,
                actions.get(section, {}).get(_definition_identity(section, item)),
                handlers.get(section),
                library,
                using=using,
            )


def _require_retirement_permissions(
    required: set[tuple[type, str]],
    actions: dict[str, dict[str, set[str]]],
) -> None:
    models = {
        "choice_sets": CustomFieldChoiceSet,
        "fields": CustomField,
        "fieldsets": CustomFieldset,
        "asset_types": AssetType,
    }
    for section, model in models.items():
        for item_actions in actions.get(section, {}).values():
            if "deprecate" in item_actions:
                required.add((model, f"change_{model._meta.model_name}"))


def _require_definition_item(
    required: set[tuple[type, str]],
    section: str,
    model: type,
    item: dict[str, object],
    item_actions: set[str] | None,
    handler: object,
    library: object | None,
    *,
    using: str,
) -> None:
    if not item_actions:
        return
    exists = _definition_exists(section, item, library, using=using)
    if not (item_actions == {"reference"} and exists):
        _require_add_or_change(required, model, exists)
    if handler is not None:
        handler(required, item, using=using)


def _require_choice_set_item(
    required: set[tuple[type, str]],
    item: dict[str, object],
    *,
    using: str,
) -> None:
    _require_choice_permissions(required, item, using=using)


def _require_field_item(
    required: set[tuple[type, str]],
    item: dict[str, object],
    *,
    using: str,
) -> None:
    _require_reference_permission(required, CustomFieldChoiceSet, item.get("choice_set"), using=using)


def _require_fieldset_item(
    required: set[tuple[type, str]],
    item: dict[str, object],
    *,
    using: str,
) -> None:
    for reference in item.get("fields", []):
        _require_reference_permission(required, CustomField, reference, using=using)


def _require_asset_type_item(
    required: set[tuple[type, str]],
    item: dict[str, object],
    *,
    using: str,
) -> None:
    _require_reference_permission(required, CustomFieldset, item.get("fieldsets", []), using=using)
    _require_reference_permission(required, Category, item.get("category"), using=using)
    _require_reference_permission(required, Manufacturer, item.get("manufacturer"), using=using)


def _definition_identity(section: str, item: dict[str, object]) -> str:
    if section == "fields":
        return f"{item['namespace']}/{item['key']}"
    return item["id"]  # type: ignore[return-value]


def _definition_exists(section: str, item: dict[str, object], library: object | None, *, using: str) -> bool:
    if section == "choice_sets":
        namespace, slug = item["id"].split("/", 1)  # type: ignore[union-attr]
        return CustomFieldChoiceSet.objects.using(using).filter(namespace=namespace, slug=slug).exists()
    if section == "fields":
        return CustomField.objects.using(using).filter(namespace=item["namespace"], name=item["key"]).exists()
    if section == "fieldsets":
        namespace, slug = item["id"].split("/", 1)  # type: ignore[union-attr]
        return CustomFieldset.objects.using(using).filter(namespace=namespace, slug=slug).exists()
    if section == "categories":
        return Category.all_objects.using(using).filter(slug=item["id"].split("/", 1)[1]).exists()  # type: ignore[union-attr]
    if section == "manufacturers":
        return Manufacturer.all_objects.using(using).filter(slug=item["id"].split("/", 1)[1]).exists()  # type: ignore[union-attr]
    if section == "asset_types":
        if library is None:
            return False
        return (
            AssetType.all_objects.using(using)
            .filter(
                library_id=library.pk,
                library_definition_key=item["id"].split("/", 1)[1],  # type: ignore[union-attr]
            )
            .exists()
        )
    return False


def _require_add_or_change(required: set[tuple[type, str]], model: type, exists: bool) -> None:
    action = "change" if exists else "add"
    required.add((model, f"{action}_{model._meta.model_name}"))


def _require_reference_permission(
    required: set[tuple[type, str]],
    model: type,
    reference: object,
    *,
    using: str,
) -> None:
    if not reference:
        return
    references = reference if isinstance(reference, (list, tuple, set)) else (reference,)
    for value in references:
        if model is CustomField:
            namespace, name = value.split("/", 1)
            exists = CustomField.objects.using(using).filter(namespace=namespace, name=name).exists()
        elif model is CustomFieldset:
            namespace, slug = value.split("/", 1)
            exists = CustomFieldset.objects.using(using).filter(namespace=namespace, slug=slug).exists()
        elif model is CustomFieldChoiceSet:
            namespace, slug = value.split("/", 1)
            exists = CustomFieldChoiceSet.objects.using(using).filter(namespace=namespace, slug=slug).exists()
        else:
            exists = model.all_objects.using(using).filter(slug=value.split("/", 1)[1]).exists()
        _require_add_or_change(required, model, exists)


def _require_choice_permissions(
    required: set[tuple[type, str]],
    item: dict[str, object],
    *,
    using: str,
) -> None:
    namespace, slug = item["id"].split("/", 1)  # type: ignore[union-attr]
    choice_set = CustomFieldChoiceSet.objects.using(using).filter(namespace=namespace, slug=slug).first()
    for choice in item.get("choices", []):  # type: ignore[union-attr]
        exists = (
            choice_set is not None
            and CustomFieldChoice.objects.using(using)
            .filter(
                choice_set_id=choice_set.pk,
                key=choice["key"],
            )
            .exists()
        )
        _require_add_or_change(required, CustomFieldChoice, exists)
    if choice_set is not None:
        desired = {choice["key"] for choice in item.get("choices", [])}  # type: ignore[union-attr]
        existing = CustomFieldChoice.objects.using(using).filter(choice_set_id=choice_set.pk).exclude(key__in=desired)
        if existing.exists():
            required.add((CustomFieldChoice, "change_customfieldchoice"))


__all__ = [
    "LibraryApplyError",
    "LibraryApplyRequest",
    "LibraryApplyResult",
    "apply_library_plan",
    "prepare_library_apply",
]
