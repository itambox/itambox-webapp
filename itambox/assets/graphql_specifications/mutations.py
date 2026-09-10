"""Typed GraphQL adapters for the canonical specification commands.

The adapter owns transport parsing and payload rendering only.  All state
changes go through ``assets.services.specifications.commands``; this module
never writes ``custom_field_data`` or composition rows itself.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

import graphene
from django.db import transaction
from graphql import GraphQLError

from assets.models import Asset, AssetType, Category
from assets.services.specifications._command_support import (
    has_global_model_permission,
    load_prospective_definition,
    stored_values_for,
)
from assets.services.specifications._create_commands import _preview_token_key
from assets.services.specifications.commands import (
    apply_category_defaults,
    cleanup_asset_history,
    cleanup_asset_type_history,
    create_asset_type,
    preview_apply_category_defaults,
    preview_asset_history_cleanup,
    preview_asset_type_create,
    preview_asset_type_history_cleanup,
    set_asset_type_composition,
    set_category_defaults,
    update_asset_specifications,
    update_asset_type_specifications,
)
from assets.services.specifications.contracts import (
    AssetTypeNativeCreateInputDTO,
    CategoryId,
    CommandRejectedDTO,
    DestinationAssetTypeSelectionDTO,
    DomainIssueDTO,
    ExplicitFieldsetSelectionDTO,
    FieldsetSelectionDTO,
    OwnerChangedDTO,
    OwnerCreatedDTO,
    OwnerNoOpDTO,
    OwnerRefDTO,
    SpecificationPatchDTO,
)
from assets.services.specifications.contracts import (
    CategoryDefaultSnapshotRevision as CategoryDefaultSnapshotRevisionValue,
)
from assets.services.specifications.loader import (
    _assemble_current_fields,
    _assemble_fieldsets,
    _definition_queryset,
    _field_dto,
    _load_fieldset_memberships,
)
from assets.services.specifications.preview_tokens import (
    OwnerRef as PreviewOwnerRef,
)
from assets.services.specifications.preview_tokens import (
    PreviewTokenExpectation,
    issue_preview_token,
    normalized_input_digest,
)
from assets.services.type_library import commands as library_commands
from assets.services.type_library.application import LibraryApplyError, LibraryApplyRequest
from assets.services.type_library.commands import LibraryCommandError
from assets.services.type_library.exporting import LibraryExportError
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset
from extras.services._definition_command_support import resource_revision_for_definition
from extras.services.definition_command_contracts import (
    CustomFieldChoiceCreateInputDTO,
    CustomFieldChoiceSetCreateInputDTO,
    CustomFieldChoiceSetUpdateInputDTO,
    CustomFieldChoiceUpdateInputDTO,
    CustomFieldCreateInputDTO,
    CustomFieldsetCreateInputDTO,
    CustomFieldsetUpdateInputDTO,
    CustomFieldUpdateInputDTO,
    DefinitionRejectedDTO,
    DefinitionSuccessDTO,
)
from extras.services.definition_commands import (
    create_custom_field,
    create_custom_field_choice,
    create_custom_field_choice_set,
    create_custom_fieldset,
    deprecate_custom_field,
    deprecate_custom_field_choice,
    deprecate_custom_field_choice_set,
    deprecate_custom_fieldset,
    replace_custom_fieldset_memberships,
    update_custom_field,
    update_custom_field_choice,
    update_custom_field_choice_set,
    update_custom_fieldset,
)
from organization.services.access_scope import (
    AccessScopeDeniedDTO,
    AccessScopeResolutionRequestDTO,
    AccessScopeResolvedDTO,
    ActorContextDTO,
    ActorId,
    RequestedScopeSelectorDTO,
    ResolvedAccessAuthorizationDTO,
    TenantGroupId,
    TenantId,
    authentication_revision_for_actor,
    resolve_access_scope,
)

from .inputs import (
    AddChoiceInput,
    ApplyCategoryDefaultsInput,
    CleanupSpecificationHistoryInput,
    CreateAssetTypeInput,
    CreateChoiceSetInput,
    CreateSpecificationFieldInput,
    CreateSpecificationFieldsetInput,
    LibraryApplyInput,
    LibraryPreviewInput,
    PreviewApplyCategoryDefaultsInput,
    PreviewAssetTypeCreateInput,
    ReorderChoicesInput,
    RequestedScopeSelectorInput,
    SetAssetTypeCompositionInput,
    SetCategoryDefaultsInput,
    UpdateAssetSpecificationsInput,
    UpdateAssetTypeSpecificationsInput,
    UpdateChoiceInput,
    UpdateChoiceSetInput,
    UpdateSpecificationFieldPolicyInput,
    UpdateSpecificationFieldsetInput,
)
from .integration import authenticated_user, choice_set_for_identity, owner_resource_revision
from .loaders import request_loader_for_info
from .scalars import CategoryDefaultSnapshotRevision as CategoryDefaultSnapshotRevisionScalar
from .scalars import SafeInteger
from .types import (
    ChoiceSetType,
    FieldsetView,
    LibraryExportModeEnum,
    SpecificationDefinitionType,
    SpecificationFieldNode,
    SpecificationFieldsetType,
    SpecificationTargetEnum,
    UserErrorType,
)

_MISSING = object()

_MESSAGE_TEXT = {
    "specifications.invalid_type": "The submitted value has an invalid type.",
    "specifications.invalid_decimal": "Enter a decimal value with no more than three decimal places.",
    "specifications.invalid_range": "The submitted value is outside the allowed range.",
    "specifications.invalid_date": "Enter a valid date.",
    "specifications.invalid_choice": "Select a valid choice.",
    "specifications.required_field": "This field is required.",
    "specifications.unknown_field_key": "The field key is not part of this specification.",
    "specifications.read_only_field": "This field is read-only.",
    "specifications.conflict_clear_overlap": "A field cannot be set and cleared in the same patch.",
    "specifications.duplicate_field": "The field occurs more than once.",
    "specifications.immutable_definition": "The definition is immutable.",
    "specifications.ownership_conflict": "The submitted object is not owned by this scope.",
    "specifications.reference_conflict": "A referenced object is not available.",
    "specifications.dependency_retirement": "A referenced dependency cannot be retired.",
    "specifications.unsupported_structure": "The specification structure cannot be resolved.",
    "specifications.stale_resource": "The resource changed after the plan was created.",
    "specifications.stale_definition": "The effective definition changed after the plan was created.",
    "specifications.stale_plan": "The preview plan is no longer valid.",
    "specifications.export_blocked": "The requested export is blocked.",
    "specifications.object_unavailable": "The requested object is unavailable.",
    "specifications.missing_precondition": "A required write precondition is missing.",
}


@dataclass(frozen=True)
class _InputError(Exception):
    issues: tuple[DomainIssueDTO, ...]


class AssetSpecificationPayload(graphene.ObjectType):
    class Meta:
        name = "AssetSpecificationPayload"

    asset = graphene.Field("assets.schema.AssetNode")
    user_errors = graphene.List(graphene.NonNull(UserErrorType), required=True)


class AssetTypeSpecificationPayload(graphene.ObjectType):
    class Meta:
        name = "AssetTypeSpecificationPayload"

    asset_type = graphene.Field("assets.schema.AssetTypeNode")
    user_errors = graphene.List(graphene.NonNull(UserErrorType), required=True)


class CategoryDefaultsPayload(graphene.ObjectType):
    class Meta:
        name = "CategoryDefaultsPayload"

    category = graphene.Field("assets.schema.CategoryNode")
    user_errors = graphene.List(graphene.NonNull(UserErrorType), required=True)


class AssetTypeCreatePreview(graphene.ObjectType):
    class Meta:
        name = "AssetTypeCreatePreview"

    preview_token = graphene.String()
    definition = graphene.Field(SpecificationDefinitionType, required=True)
    expected_definition_revision = graphene.String(required=True)
    expected_resource_revision = graphene.String()
    category_default_snapshot_revision = graphene.Field(CategoryDefaultSnapshotRevisionScalar)
    consumes_category_defaults = graphene.Boolean(required=True)


class AssetTypeCreatePreviewPayload(graphene.ObjectType):
    class Meta:
        name = "AssetTypeCreatePreviewPayload"

    preview = graphene.Field(AssetTypeCreatePreview)
    user_errors = graphene.List(graphene.NonNull(UserErrorType), required=True)


class CleanupPayload(graphene.ObjectType):
    class Meta:
        name = "CleanupPayload"

    removed_keys = graphene.List(graphene.NonNull(graphene.String), required=True)
    user_errors = graphene.List(graphene.NonNull(UserErrorType), required=True)


class ImpactPreview(graphene.ObjectType):
    class Meta:
        name = "ImpactPreview"

    token = graphene.String(required=True)
    definition_revision = graphene.String(required=True)
    issues = graphene.List(graphene.NonNull(UserErrorType), required=True)


class DefinitionPayload(graphene.ObjectType):
    class Meta:
        name = "DefinitionPayload"

    field = graphene.Field(SpecificationFieldNode)
    fieldset = graphene.Field(SpecificationFieldsetType)
    choice_set = graphene.Field(ChoiceSetType)
    user_errors = graphene.List(graphene.NonNull(UserErrorType), required=True)


class LibraryActionType(graphene.ObjectType):
    class Meta:
        name = "LibraryAction"

    id = graphene.String(required=True)
    kind = graphene.String(required=True)
    identity = graphene.String(required=True)
    path = graphene.List(graphene.NonNull(graphene.String), required=True)
    message = graphene.String(required=True)
    blocking = graphene.Boolean(required=True)


class LibraryPlanType(graphene.ObjectType):
    class Meta:
        name = "LibraryPlan"

    token = graphene.String(required=True)
    digest = graphene.String(required=True)
    can_apply = graphene.Boolean(required=True)
    actions = graphene.List(graphene.NonNull(LibraryActionType), required=True)
    user_errors = graphene.List(graphene.NonNull(UserErrorType), required=True)


class LibraryApplyPayload(graphene.ObjectType):
    class Meta:
        name = "LibraryApplyPayload"

    applied = graphene.Boolean(required=True)
    changed = graphene.Boolean(required=True)
    accepted_release = graphene.Field(SafeInteger)
    user_errors = graphene.List(graphene.NonNull(UserErrorType), required=True)


class LibraryExportPayload(graphene.ObjectType):
    class Meta:
        name = "LibraryExportPayload"

    document_text = graphene.String()
    semantic_digest = graphene.String()
    user_errors = graphene.List(graphene.NonNull(UserErrorType), required=True)


def _field(value: object, name: str, default: object = _MISSING) -> object:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _has_field(value: object, name: str) -> bool:
    return _field(value, name) is not _MISSING


def _message(message_key: str) -> str:
    return _MESSAGE_TEXT.get(message_key, message_key.rsplit(".", 1)[-1].replace("_", " ").capitalize())


def _graphql_error(issue: DomainIssueDTO, *, prefix_input: bool = False) -> GraphQLError:
    path = _graphql_path(issue, prefix_input=prefix_input)
    return GraphQLError(
        _message(str(issue.message_key)),
        extensions={"code": str(issue.code), "path": list(path)},
    )


def _raise_preview_failure(issues: Sequence[DomainIssueDTO]) -> None:
    issue = tuple(issues)[0] if issues else _issue("OBJECT_UNAVAILABLE")
    raise _graphql_error(issue, prefix_input=False)


def _identity(value: object, *, path: Sequence[str]) -> str:
    if type(value) is not str or value.count("/") != 1:
        _raise_input("INVALID_TYPE", path=path)
    namespace, local = value.split("/", 1)
    if not namespace or not local:
        _raise_input("INVALID_TYPE", path=path)
    return value


def _identity_list(value: object, *, path: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        _raise_input("INVALID_TYPE", path=path)
    identities = tuple(_identity(item, path=path) for item in value)
    if len(set(identities)) != len(identities):
        _raise_input("DUPLICATE_FIELD", path=path)
    return identities


def _string_value(value: object, *, path: Sequence[str], allow_empty: bool = True) -> str:
    if type(value) is not str or (not allow_empty and not value):
        _raise_input("INVALID_TYPE", path=path)
    return value


def _optional_bool(value: object, *, path: Sequence[str]) -> bool | None:
    if value is _MISSING or value is None:
        return None
    if type(value) is not bool:
        _raise_input("INVALID_TYPE", path=path)
    return value


def _optional_int(value: object, *, path: Sequence[str]) -> int | None:
    if value is _MISSING or value is None:
        return None
    if type(value) is not int:
        _raise_input("INVALID_TYPE", path=path)
    return value


def _targets(value: object) -> tuple[str, ...]:
    raw = _field(value, "targets")
    if not isinstance(raw, (list, tuple)):
        _raise_input("INVALID_TYPE", path=("input", "targets"))
    mapped: list[str] = []
    for target in raw:
        target_value = str(_enum_value(target))
        if target_value == "asset_type":
            mapped.append("assets.assettype")
        elif target_value == "asset":
            mapped.append("assets.asset")
        else:
            _raise_input("INVALID_TYPE", path=("input", "targets"))
    if len(set(mapped)) != len(mapped):
        _raise_input("DUPLICATE_FIELD", path=("input", "targets"))
    return tuple(mapped)


_FIELD_TYPE_TO_COMMAND = {
    "text": "text",
    "integer": "integer",
    "decimal": "decimal",
    "boolean": "boolean",
    "date": "date",
    "single_select": "single-select",
    "multi_select": "multi-select",
}


def _field_type(value: object) -> str:
    field_type = str(_enum_value(value))
    try:
        return _FIELD_TYPE_TO_COMMAND[field_type]
    except KeyError:
        _raise_input("INVALID_TYPE", path=("input", "fieldType"))
    raise AssertionError("unreachable")


def _decimal_or_none(value: object, *, path: Sequence[str]) -> Decimal | None:
    if value is _MISSING or value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        _raise_input("INVALID_TYPE", path=path)
    raise AssertionError("unreachable")


def _validation(value: object) -> dict[str, object]:
    if value is _MISSING or value is None:
        _raise_input("INVALID_TYPE", path=("input", "validation"))
    minimum = _decimal_or_none(_field(value, "minimum"), path=("input", "validation", "minimum"))
    maximum = _decimal_or_none(_field(value, "maximum"), path=("input", "validation", "maximum"))
    scale = _optional_int(_field(value, "scale"), path=("input", "validation", "scale"))
    max_length = _optional_int(_field(value, "max_length"), path=("input", "validation", "maxLength"))
    max_values = _optional_int(_field(value, "max_values"), path=("input", "validation", "maxValues"))
    regex = _optional_string(_field(value, "regex"), path=("input", "validation", "regex"))
    rule = _optional_string(_field(value, "rule"), path=("input", "validation", "rule"))
    return {
        "minimum_value": minimum,
        "maximum_value": maximum,
        "decimal_scale": scale,
        "text_max_length": max_length,
        "max_values": max_values,
        "regex": regex,
        "validation_rule": rule,
    }


def _definition_model(kind: str, identity: str):
    if kind == "field":
        namespace, name = identity.split("/", 1)
        return CustomField.objects.filter(namespace=namespace, name=name).first()
    if kind in {"fieldset", "choice_set"}:
        namespace, slug = identity.split("/", 1)
        model = CustomFieldset if kind == "fieldset" else CustomFieldChoiceSet
        return model.objects.filter(namespace=namespace, slug=slug).first()
    if kind == "choice":
        choice_identity, key = identity.rsplit("#", 1)
        namespace, slug = choice_identity.split("/", 1)
        return (
            CustomFieldChoice.objects.select_related("choice_set")
            .filter(choice_set__namespace=namespace, choice_set__slug=slug, key=key)
            .first()
        )
    return None


def _definition_view(kind: str, identity: str):
    if kind == "field":
        definition = _definition_model(kind, identity)
        if definition is None:
            return None
        field = _definition_queryset(CustomField.objects.filter(pk=definition.pk)).first()
        return None if field is None else {"field": _field_dto(field, {})}
    if kind == "fieldset":
        fieldset = _definition_model(kind, identity)
        if fieldset is None:
            return None
        rows = _load_fieldset_memberships((fieldset.pk,))
        fields_by_key, fields_by_identity = _assemble_current_fields(rows, {})
        del fields_by_key
        fieldsets = _assemble_fieldsets((fieldset.pk,), {fieldset.pk: fieldset}, rows, fields_by_identity)
        definition = fieldsets.get(identity)
        if definition is None:
            return None
        return {
            "fieldset": FieldsetView(
                definition=definition,
                fields=tuple(fields_by_identity[str(item.field_identity)] for item in definition.field_memberships),
            )
        }
    if kind == "choice_set":
        return {"choice_set": choice_set_for_identity(identity)}
    if kind == "choice":
        choice_set_identity = identity.rsplit("#", 1)[0]
        return {"choice_set": choice_set_for_identity(choice_set_identity)}
    return None


def _definition_payload(result: object):
    if isinstance(result, DefinitionRejectedDTO):
        return DefinitionPayload(field=None, fieldset=None, choice_set=None, user_errors=_user_errors(result.issues))
    if not isinstance(result, DefinitionSuccessDTO):
        return DefinitionPayload(
            field=None,
            fieldset=None,
            choice_set=None,
            user_errors=_user_errors((_issue("OBJECT_UNAVAILABLE"),)),
        )
    view = _definition_view(str(result.definition_kind), str(result.identity))
    if view is None or any(value is None for value in view.values()):
        return DefinitionPayload(
            field=None,
            fieldset=None,
            choice_set=None,
            user_errors=_user_errors((_issue("OBJECT_UNAVAILABLE"),)),
        )
    return DefinitionPayload(
        field=view.get("field"),
        fieldset=view.get("fieldset"),
        choice_set=view.get("choice_set"),
        user_errors=(),
    )


def _definition_result(call) -> object:
    try:
        return call()
    except (TypeError, ValueError):
        return DefinitionRejectedDTO(
            outcome="rejected",
            definition_kind=None,
            definition_id=None,
            identity=None,
            issues=(_issue("INVALID_TYPE", path=("input",)),),
        )


def _definition_id_or_rejection(kind: str, identity: str):
    definition = _definition_model(kind, identity)
    if definition is None:
        return None, DefinitionRejectedDTO(
            outcome="rejected",
            definition_kind=None,
            definition_id=None,
            identity=None,
            issues=(_issue("OBJECT_UNAVAILABLE"),),
        )
    return definition.pk, None


def _impact_token_guard(value: object, *, path: Sequence[str]) -> None:
    if value is not _MISSING and value is not None:
        _raise_input("UNSUPPORTED_STRUCTURE", path=path)


def _lifecycle(value: object, *, path: Sequence[str]) -> str | None:
    if value is _MISSING or value is None:
        return None
    lifecycle = str(_enum_value(value))
    if lifecycle not in {"active", "deprecated"}:
        _raise_input("INVALID_TYPE", path=path)
    return lifecycle


def _parse_field_create(value: object) -> CustomFieldCreateInputDTO:
    validation = _validation(_field(value, "validation"))
    try:
        return CustomFieldCreateInputDTO(
            namespace=_required_string(_field(value, "namespace"), path=("input", "namespace")),
            local_key=_required_string(_field(value, "key"), path=("input", "key")),
            label=_required_string(_field(value, "label"), path=("input", "label")),
            object_types=_targets(value),
            field_type=_field_type(_field(value, "field_type")),  # type: ignore[arg-type]
            activation=str(_enum_value(_field(value, "activation"))),  # type: ignore[arg-type]
            help_text=_optional_string(_field(value, "help_text"), path=("input", "helpText")) or "",
            quantity_kind=_optional_string(_field(value, "quantity_kind"), path=("input", "quantityKind")),
            canonical_unit=_optional_string(_field(value, "canonical_unit"), path=("input", "canonicalUnit")),
            **validation,
            required=bool(_field(value, "required", False)),
            nullable=bool(_field(value, "nullable", False)),
            mappings=(),
        )
    except (TypeError, ValueError):
        _raise_input("INVALID_TYPE", path=("input",))
    raise AssertionError("unreachable")


def _issue(
    code: str,
    *,
    path: Sequence[str] = (),
    field_key: str | None = None,
    message_key: str | None = None,
) -> DomainIssueDTO:
    return DomainIssueDTO(
        code=code,  # type: ignore[arg-type]
        path=tuple(path),
        field_key=field_key,
        message_key=message_key or f"specifications.{code.lower()}",
    )


def _raise_input(
    code: str,
    *,
    path: Sequence[str],
    field_key: str | None = None,
) -> None:
    raise _InputError((_issue(code, path=path, field_key=field_key),))


def _positive_id(value: object, *, path: Sequence[str]) -> int:
    if type(value) is int and value > 0:
        return value
    if type(value) is str and value.isascii() and value.isdecimal() and int(value) > 0:
        return int(value)
    _raise_input("INVALID_TYPE", path=path)
    raise AssertionError("unreachable")


def _optional_id(value: object, *, path: Sequence[str]) -> int | None:
    if value is _MISSING or value is None:
        return None
    return _positive_id(value, path=path)


def _required_string(value: object, *, path: Sequence[str]) -> str:
    if type(value) is not str or not value:
        _raise_input("INVALID_TYPE", path=path)
    return value


def _optional_string(value: object, *, path: Sequence[str]) -> str | None:
    if value is _MISSING or value is None:
        return None
    return _string_value(value, path=path)


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _graphql_path(issue: DomainIssueDTO, *, prefix_input: bool) -> tuple[str, ...]:
    raw_path = tuple(str(item) for item in issue.path)
    if raw_path[:1] == ("input",):
        return raw_path
    if not raw_path:
        if issue.code == "STALE_RESOURCE":
            raw_path = ("expected_resource_revision",)
        elif issue.code == "STALE_DEFINITION":
            raw_path = ("expected_definition_revision",)
        elif issue.code == "STALE_PLAN":
            raw_path = ("preview_token",)
        else:
            return ()
    if raw_path[0] == "specification_patch":
        raw_path = ("patch", *raw_path[1:])
    elif raw_path[0] in {"set", "clear"}:
        raw_path = ("patch", *raw_path)

    if raw_path[0] in {"patch", "keys", "required"}:
        path = (raw_path[0], *raw_path[1:])
    else:
        path = tuple(
            part[:1] + part[1:]
            if "_" not in part
            else part.split("_", 1)[0] + "".join(piece[:1].upper() + piece[1:] for piece in part.split("_")[1:])
            for part in raw_path
        )
    return ("input", *path) if prefix_input else path


def _user_errors(issues: Sequence[DomainIssueDTO], *, prefix_input: bool = True):
    return tuple(
        UserErrorType(
            code=str(item.code),
            path=_graphql_path(item, prefix_input=prefix_input),
            field_key=None if item.field_key is None else str(item.field_key),
            message=_message(str(item.message_key)),
        )
        for item in issues
    )


def _patch_value(value: object, *, path: Sequence[str], field_key: str) -> object:
    arms = (
        ("text", _field(value, "text")),
        ("integer", _field(value, "integer")),
        ("decimal", _field(value, "decimal")),
        ("boolean", _field(value, "boolean")),
        ("date", _field(value, "date")),
        ("choice", _field(value, "choice")),
        ("multi_choice", _field(value, "multi_choice")),
        ("null_value", _field(value, "null_value")),
    )
    supplied = tuple(
        (name, arm_value) for name, arm_value in arms if arm_value is not _MISSING and arm_value is not None
    )
    if len(supplied) != 1:
        _raise_input("INVALID_TYPE", path=path, field_key=field_key)
    name, arm_value = supplied[0]
    if name == "null_value":
        if arm_value is not True:
            _raise_input("INVALID_TYPE", path=path, field_key=field_key)
        return None
    if name == "multi_choice":
        if not isinstance(arm_value, (list, tuple)) or any(type(item) is not str for item in arm_value):
            _raise_input("INVALID_TYPE", path=path, field_key=field_key)
        return tuple(arm_value)
    return arm_value


def _parse_set_values(value: object) -> tuple[dict[str, object], list[DomainIssueDTO]]:
    set_items = _field(value, "set", ())
    if not isinstance(set_items, (list, tuple)):
        return {}, [_issue("INVALID_TYPE", path=("input", "patch", "set"))]

    set_values: dict[str, object] = {}
    issues: list[DomainIssueDTO] = []
    for item in set_items:
        key = _field(item, "key")
        raw_value = _field(item, "value")
        if type(key) is not str or not key:
            issues.append(_issue("INVALID_TYPE", path=("input", "patch", "set")))
            continue
        if key in set_values:
            issues.append(_issue("DUPLICATE_FIELD", path=("input", "patch", "set"), field_key=key))
            continue
        if raw_value is _MISSING or raw_value is None:
            issues.append(_issue("INVALID_TYPE", path=("input", "patch", "set"), field_key=key))
            continue
        try:
            set_values[key] = _patch_value(
                raw_value,
                path=("input", "patch", "set"),
                field_key=key,
            )
        except _InputError as error:
            issues.extend(error.issues)
    return set_values, issues


def _parse_clear_keys(value: object) -> tuple[list[str], list[DomainIssueDTO]]:
    clear_items = _field(value, "clear", ())
    if not isinstance(clear_items, (list, tuple)):
        return [], [_issue("INVALID_TYPE", path=("input", "patch", "clear"))]

    clear_keys: list[str] = []
    issues: list[DomainIssueDTO] = []
    for key in clear_items:
        if type(key) is not str or not key:
            issues.append(_issue("INVALID_TYPE", path=("input", "patch", "clear")))
            continue
        if key in clear_keys:
            issues.append(_issue("DUPLICATE_FIELD", path=("input", "patch", "clear"), field_key=key))
            continue
        clear_keys.append(key)
    return clear_keys, issues


def _patch(value: object) -> SpecificationPatchDTO:
    set_values, issues = _parse_set_values(value)
    clear_keys, clear_issues = _parse_clear_keys(value)
    issues.extend(clear_issues)

    overlap = sorted(set(set_values).intersection(clear_keys))
    if overlap:
        issues.append(_issue("CONFLICT_CLEAR_OVERLAP", field_key=overlap[0]))
    if issues:
        raise _InputError(tuple(issues))
    return SpecificationPatchDTO(set_values=set_values, clear_keys=tuple(clear_keys))


def _fieldset_selection(
    value: object, *, presence_sensitive: bool
) -> FieldsetSelectionDTO | ExplicitFieldsetSelectionDTO:
    if value is _MISSING:
        if presence_sensitive:
            return FieldsetSelectionDTO(presence="omitted", identities=())
        _raise_input("INVALID_TYPE", path=("input", "fieldsets"))
    if value is None or not isinstance(value, (list, tuple)):
        _raise_input("INVALID_TYPE", path=("input", "fieldsets"))
    if any(type(identity) is not str for identity in value):
        _raise_input("INVALID_TYPE", path=("input", "fieldsets"))
    try:
        if presence_sensitive:
            return FieldsetSelectionDTO(presence="explicit", identities=tuple(value))
        return ExplicitFieldsetSelectionDTO(identities=tuple(value))
    except (TypeError, ValueError):
        _raise_input("INVALID_TYPE", path=("input", "fieldsets"))
    raise AssertionError("unreachable")


def _native_create(value: object) -> tuple[AssetTypeNativeCreateInputDTO, FieldsetSelectionDTO, SpecificationPatchDTO]:
    manufacturer_id = _positive_id(_field(value, "manufacturer_id"), path=("input", "manufacturerId"))
    model = _required_string(_field(value, "model"), path=("input", "model"))
    category_id = _optional_id(_field(value, "category_id"), path=("input", "categoryId"))
    asset_role_id = _optional_id(_field(value, "asset_role_id"), path=("input", "assetRoleId"))
    fieldsets = _fieldset_selection(_field(value, "fieldsets"), presence_sensitive=True)
    patch = _patch(_field(value, "patch"))
    try:
        native = AssetTypeNativeCreateInputDTO(
            manufacturer_id=manufacturer_id,
            model=model,
            slug=None,
            part_number="",
            ean="",
            region="",
            configuration="",
            eol_months=None,
            category_id=category_id,
            suggested_asset_role_id=asset_role_id,
            depreciation_id=None,
            staged_image_id=None,
            description="",
            comments="",
            tag_ids=(),
            requestable=False,
        )
    except (TypeError, ValueError):
        _raise_input("INVALID_TYPE", path=("input",))
    return native, fieldsets, patch


def _actor(info: object) -> ActorContextDTO:
    user = authenticated_user(info)
    return ActorContextDTO(
        actor_id=ActorId(int(user.pk)),
        authentication_revision=authentication_revision_for_actor(user),
    )


def _scope_selector(value: object, *, required: bool) -> RequestedScopeSelectorDTO:
    if value is _MISSING or value is None:
        if required:
            _raise_input("MISSING_PRECONDITION", path=("input", "requestedScope"))
        _raise_input("MISSING_PRECONDITION", path=("input", "requestedScope"))
    mode = _enum_value(_field(value, "mode"))
    tenant_id = _optional_id(_field(value, "tenant_id"), path=("input", "requestedScope", "tenantId"))
    tenant_group_id = _optional_id(
        _field(value, "tenant_group_id"),
        path=("input", "requestedScope", "tenantGroupId"),
    )
    if mode not in {"tenant", "tenant_group", "all_accessible"}:
        _raise_input("INVALID_TYPE", path=("input", "requestedScope", "mode"))
    try:
        return RequestedScopeSelectorDTO(
            mode=mode,  # type: ignore[arg-type]
            tenant_id=None if tenant_id is None else TenantId(tenant_id),
            tenant_group_id=None if tenant_group_id is None else TenantGroupId(tenant_group_id),
        )
    except (TypeError, ValueError):
        _raise_input("INVALID_TYPE", path=("input", "requestedScope"))
    raise AssertionError("unreachable")


def _asset_authorization(info: object, requested_scope: object) -> ResolvedAccessAuthorizationDTO | None:
    selector = _scope_selector(requested_scope, required=True)
    actor = _actor(info)
    request = AccessScopeResolutionRequestDTO(
        actor=actor,
        selector=selector,
        operation="update_asset_specifications",
        required_permission="assets.change_asset",
    )
    resolved = resolve_access_scope(request)
    if isinstance(resolved, AccessScopeDeniedDTO):
        return None
    if not isinstance(resolved, AccessScopeResolvedDTO):
        return None
    return ResolvedAccessAuthorizationDTO(
        actor=actor,
        request=resolved.request,
        initial_scope=resolved.access_scope,
    )


def _command_result(call) -> object:
    try:
        return call()
    except (TypeError, ValueError):
        return CommandRejectedDTO(
            outcome="rejected",
            safe_owner=None,
            issues=(_issue("INVALID_TYPE", path=("input",)),),
        )


def _owner_model(owner: OwnerRefDTO | None):
    if owner is None:
        return None
    model = {
        "asset": Asset,
        "asset_type": AssetType,
        "category": Category,
    }.get(owner.owner_kind)
    if model is None:
        return None
    manager = getattr(model, "all_objects", model._base_manager)
    return manager.filter(pk=owner.owner_id, deleted_at__isnull=True).first()


def _owner_from_success(result: object):
    if isinstance(result, (OwnerChangedDTO, OwnerNoOpDTO, OwnerCreatedDTO)):
        return _owner_model(result.owner)
    return None


def _owner_payload(result: object, payload_type: type[object], owner_field: str):
    if isinstance(result, CommandRejectedDTO):
        return payload_type(**{owner_field: None, "user_errors": _user_errors(result.issues)})
    return payload_type(**{owner_field: _owner_from_success(result), "user_errors": ()})


def _preview_payload(result: object):
    if isinstance(result, CommandRejectedDTO):
        return AssetTypeCreatePreviewPayload(preview=None, user_errors=_user_errors(result.issues))
    return AssetTypeCreatePreviewPayload(
        preview=AssetTypeCreatePreview(
            preview_token=None if result.preview_token is None else str(result.preview_token),
            definition=result.definition,
            expected_definition_revision=str(result.expected_definition_revision),
            expected_resource_revision=(
                None if result.expected_resource_revision is None else str(result.expected_resource_revision)
            ),
            category_default_snapshot_revision=(
                None
                if result.expected_category_default_snapshot_revision is None
                else str(result.expected_category_default_snapshot_revision)
            ),
            consumes_category_defaults=result.consumes_category_defaults,
        ),
        user_errors=_user_errors(result.issues),
    )


def _cleanup_payload(result: object, keys: Sequence[str]):
    if isinstance(result, CommandRejectedDTO):
        return CleanupPayload(removed_keys=(), user_errors=_user_errors(result.issues))
    removed = tuple(str(key) for key in keys) if isinstance(result, OwnerChangedDTO) else ()
    return CleanupPayload(removed_keys=removed, user_errors=())


def _target(value: object) -> str:
    target = str(_enum_value(value))
    if target not in {"asset_type", "asset"}:
        _raise_input("INVALID_TYPE", path=("input", "target"))
    return target


def _keys(value: object, *, path: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or any(type(key) is not str for key in value):
        _raise_input("INVALID_TYPE", path=path)
    return tuple(value)


def _history_revisions(info: object, owner_kind: str, owner_id: int) -> tuple[str, str]:
    model = AssetType if owner_kind == "asset_type" else Asset
    owner = model.all_objects.filter(pk=owner_id, deleted_at__isnull=True).first()
    if owner is None:
        return "unavailable", "unavailable"
    try:
        read = request_loader_for_info(info).read_owner(owner, target_kind=owner_kind)
        return str(owner_resource_revision(owner)), str(read.definition.revision)
    except (TypeError, ValueError, KeyError):
        return "unavailable", "unavailable"


class PreviewAssetTypeCreate(graphene.Mutation):
    class Arguments:
        input = PreviewAssetTypeCreateInput(required=True)

    Output = AssetTypeCreatePreviewPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            native, fieldsets, patch = _native_create(input)
            actor = _actor(info)
        except _InputError as error:
            return AssetTypeCreatePreviewPayload(preview=None, user_errors=_user_errors(error.issues))
        result = _command_result(
            lambda: preview_asset_type_create(actor=actor, native=native, fieldsets=fieldsets, patch=patch)
        )
        return _preview_payload(result)


class CreateAssetType(graphene.Mutation):
    class Arguments:
        input = CreateAssetTypeInput(required=True)

    Output = AssetTypeSpecificationPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            native, fieldsets, patch = _native_create(input)
            expected_definition_revision = _required_string(
                _field(input, "expected_definition_revision"),
                path=("input", "expectedDefinitionRevision"),
            )
            preview_token = _optional_string(
                _field(input, "preview_token"),
                path=("input", "previewToken"),
            )
            snapshot_revision = _optional_string(
                _field(input, "expected_category_default_snapshot_revision"),
                path=("input", "expectedCategoryDefaultSnapshotRevision"),
            )
            consumes_defaults = native.category_id is not None and fieldsets.presence == "omitted"
            if consumes_defaults:
                missing: list[DomainIssueDTO] = []
                if preview_token is None:
                    missing.append(_issue("MISSING_PRECONDITION", path=("input", "previewToken")))
                if snapshot_revision is None:
                    missing.append(
                        _issue(
                            "MISSING_PRECONDITION",
                            path=("input", "expectedCategoryDefaultSnapshotRevision"),
                        )
                    )
                if missing:
                    return _owner_payload(
                        CommandRejectedDTO("rejected", None, tuple(missing)),
                        AssetTypeSpecificationPayload,
                        "asset_type",
                    )
            elif preview_token is not None or snapshot_revision is not None:
                issues = []
                if preview_token is not None:
                    issues.append(_issue("INVALID_TYPE", path=("input", "previewToken")))
                if snapshot_revision is not None:
                    issues.append(_issue("INVALID_TYPE", path=("input", "expectedCategoryDefaultSnapshotRevision")))
                return _owner_payload(
                    CommandRejectedDTO("rejected", None, tuple(issues)),
                    AssetTypeSpecificationPayload,
                    "asset_type",
                )
            actor = _actor(info)
        except _InputError as error:
            return _owner_payload(
                CommandRejectedDTO("rejected", None, error.issues),
                AssetTypeSpecificationPayload,
                "asset_type",
            )
        result = _command_result(
            lambda: create_asset_type(
                actor=actor,
                native=native,
                fieldsets=fieldsets,
                patch=patch,
                preview_token=preview_token,
                expected_definition_revision=expected_definition_revision,
                expected_category_default_snapshot_revision=(
                    None if snapshot_revision is None else CategoryDefaultSnapshotRevisionValue(snapshot_revision)
                ),
            )
        )
        return _owner_payload(result, AssetTypeSpecificationPayload, "asset_type")


class UpdateAssetTypeSpecifications(graphene.Mutation):
    class Arguments:
        input = UpdateAssetTypeSpecificationsInput(required=True)

    Output = AssetTypeSpecificationPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            asset_type_id = _positive_id(_field(input, "asset_type_id"), path=("input", "assetTypeId"))
            expected_resource_revision = _required_string(
                _field(input, "expected_resource_revision"),
                path=("input", "expectedResourceRevision"),
            )
            expected_definition_revision = _required_string(
                _field(input, "expected_definition_revision"),
                path=("input", "expectedDefinitionRevision"),
            )
            patch = _patch(_field(input, "patch"))
            actor = _actor(info)
        except _InputError as error:
            return _owner_payload(
                CommandRejectedDTO("rejected", None, error.issues),
                AssetTypeSpecificationPayload,
                "asset_type",
            )
        result = _command_result(
            lambda: update_asset_type_specifications(
                actor=actor,
                asset_type_id=asset_type_id,
                expected_resource_revision=expected_resource_revision,
                expected_definition_revision=expected_definition_revision,
                patch=patch,
            )
        )
        return _owner_payload(result, AssetTypeSpecificationPayload, "asset_type")


class PreviewApplyCategoryDefaults(graphene.Mutation):
    class Arguments:
        input = PreviewApplyCategoryDefaultsInput(required=True)

    Output = AssetTypeCreatePreviewPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            asset_type_id = _positive_id(_field(input, "asset_type_id"), path=("input", "assetTypeId"))
            expected_resource_revision = _required_string(
                _field(input, "expected_resource_revision"),
                path=("input", "expectedResourceRevision"),
            )
            patch = _patch(_field(input, "patch"))
            actor = _actor(info)
        except _InputError as error:
            return AssetTypeCreatePreviewPayload(preview=None, user_errors=_user_errors(error.issues))
        result = _command_result(
            lambda: preview_apply_category_defaults(
                actor=actor,
                asset_type_id=asset_type_id,
                expected_resource_revision=expected_resource_revision,
                patch=patch,
            )
        )
        return _preview_payload(result)


class ApplyCategoryDefaults(graphene.Mutation):
    class Arguments:
        input = ApplyCategoryDefaultsInput(required=True)

    Output = AssetTypeSpecificationPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            asset_type_id = _positive_id(_field(input, "asset_type_id"), path=("input", "assetTypeId"))
            expected_resource_revision = _required_string(
                _field(input, "expected_resource_revision"),
                path=("input", "expectedResourceRevision"),
            )
            expected_definition_revision = _required_string(
                _field(input, "expected_definition_revision"),
                path=("input", "expectedDefinitionRevision"),
            )
            expected_snapshot_revision = _required_string(
                _field(input, "expected_category_default_snapshot_revision"),
                path=("input", "expectedCategoryDefaultSnapshotRevision"),
            )
            preview_token = _required_string(
                _field(input, "preview_token"),
                path=("input", "previewToken"),
            )
            patch = _patch(_field(input, "patch"))
            actor = _actor(info)
        except _InputError as error:
            return _owner_payload(
                CommandRejectedDTO("rejected", None, error.issues),
                AssetTypeSpecificationPayload,
                "asset_type",
            )
        result = _command_result(
            lambda: apply_category_defaults(
                actor=actor,
                asset_type_id=asset_type_id,
                preview_token=preview_token,
                expected_resource_revision=expected_resource_revision,
                expected_definition_revision=expected_definition_revision,
                expected_category_default_snapshot_revision=expected_snapshot_revision,
                patch=patch,
            )
        )
        return _owner_payload(result, AssetTypeSpecificationPayload, "asset_type")


class SetAssetTypeComposition(graphene.Mutation):
    class Arguments:
        input = SetAssetTypeCompositionInput(required=True)

    Output = AssetTypeSpecificationPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            asset_type_id = _positive_id(_field(input, "asset_type_id"), path=("input", "assetTypeId"))
            expected_resource_revision = _required_string(
                _field(input, "expected_resource_revision"),
                path=("input", "expectedResourceRevision"),
            )
            expected_definition_revision = _required_string(
                _field(input, "expected_definition_revision"),
                path=("input", "expectedDefinitionRevision"),
            )
            fieldsets = _fieldset_selection(_field(input, "fieldsets"), presence_sensitive=False)
            patch = _patch(_field(input, "patch"))
            actor = _actor(info)
        except _InputError as error:
            return _owner_payload(
                CommandRejectedDTO("rejected", None, error.issues),
                AssetTypeSpecificationPayload,
                "asset_type",
            )
        result = _command_result(
            lambda: set_asset_type_composition(
                actor=actor,
                asset_type_id=asset_type_id,
                fieldsets=fieldsets,
                expected_resource_revision=expected_resource_revision,
                expected_definition_revision=expected_definition_revision,
                patch=patch,
            )
        )
        return _owner_payload(result, AssetTypeSpecificationPayload, "asset_type")


class UpdateAssetSpecifications(graphene.Mutation):
    class Arguments:
        input = UpdateAssetSpecificationsInput(required=True)

    Output = AssetSpecificationPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            asset_id = _positive_id(_field(input, "asset_id"), path=("input", "assetId"))
            raw_destination = _field(input, "asset_type_id")
            if raw_destination is _MISSING:
                destination = DestinationAssetTypeSelectionDTO(presence="keep_current", asset_type_id=None)
            elif raw_destination is None:
                _raise_input("INVALID_TYPE", path=("input", "assetTypeId"))
            else:
                destination = DestinationAssetTypeSelectionDTO(
                    presence="replace",
                    asset_type_id=_positive_id(raw_destination, path=("input", "assetTypeId")),
                )
            expected_resource_revision = _required_string(
                _field(input, "expected_resource_revision"),
                path=("input", "expectedResourceRevision"),
            )
            expected_definition_revision = _required_string(
                _field(input, "expected_definition_revision"),
                path=("input", "expectedDefinitionRevision"),
            )
            patch = _patch(_field(input, "patch"))
            authorization = _asset_authorization(info, _field(input, "requested_scope"))
        except _InputError as error:
            return _owner_payload(
                CommandRejectedDTO("rejected", None, error.issues),
                AssetSpecificationPayload,
                "asset",
            )
        if authorization is None:
            return _owner_payload(
                CommandRejectedDTO("rejected", None, (_issue("OBJECT_UNAVAILABLE"),)),
                AssetSpecificationPayload,
                "asset",
            )
        result = _command_result(
            lambda: update_asset_specifications(
                authorization=authorization,
                asset_id=asset_id,
                destination=destination,
                expected_resource_revision=expected_resource_revision,
                expected_definition_revision=expected_definition_revision,
                patch=patch,
            )
        )
        return _owner_payload(result, AssetSpecificationPayload, "asset")


class SetCategoryDefaults(graphene.Mutation):
    class Arguments:
        input = SetCategoryDefaultsInput(required=True)

    Output = CategoryDefaultsPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            category_id = _positive_id(_field(input, "category_id"), path=("input", "categoryId"))
            expected_resource_revision = _required_string(
                _field(input, "expected_resource_revision"),
                path=("input", "expectedResourceRevision"),
            )
            fieldsets = _fieldset_selection(_field(input, "fieldsets"), presence_sensitive=False)
            actor = _actor(info)
        except _InputError as error:
            return _owner_payload(
                CommandRejectedDTO("rejected", None, error.issues),
                CategoryDefaultsPayload,
                "category",
            )
        result = _command_result(
            lambda: set_category_defaults(
                actor=actor,
                category_id=CategoryId(category_id),
                expected_resource_revision=expected_resource_revision,
                fieldsets=fieldsets,
            )
        )
        return _owner_payload(result, CategoryDefaultsPayload, "category")


class PreviewSpecificationHistoryCleanup(graphene.Mutation):
    class Arguments:
        owner_id = graphene.ID(required=True)
        target = SpecificationTargetEnum(required=True)
        requested_scope = RequestedScopeSelectorInput()
        keys = graphene.List(graphene.NonNull(graphene.String), required=True)

    Output = ImpactPreview

    @staticmethod
    def mutate(root, info, owner_id, target, keys, requested_scope=_MISSING):
        del root
        try:
            target_kind = _target(target)
            owner_id = _positive_id(owner_id, path=("ownerId",))
            keys = _keys(keys, path=("keys",))
            if target_kind == "asset":
                authorization = _asset_authorization(info, requested_scope)
                if authorization is None:
                    _raise_preview_failure((_issue("OBJECT_UNAVAILABLE"),))
                expected_resource_revision, expected_definition_revision = _history_revisions(info, "asset", owner_id)
                result = _command_result(
                    lambda: preview_asset_history_cleanup(
                        authorization=authorization,
                        asset_id=owner_id,
                        keys=keys,
                        expected_resource_revision=expected_resource_revision,
                        expected_definition_revision=expected_definition_revision,
                    )
                )
            else:
                if requested_scope is not _MISSING:
                    _raise_input("INVALID_TYPE", path=("requestedScope",))
                actor = _actor(info)
                expected_resource_revision, expected_definition_revision = _history_revisions(
                    info, "asset_type", owner_id
                )
                result = _command_result(
                    lambda: preview_asset_type_history_cleanup(
                        actor=actor,
                        asset_type_id=owner_id,
                        keys=keys,
                        expected_resource_revision=expected_resource_revision,
                        expected_definition_revision=expected_definition_revision,
                    )
                )
        except _InputError as error:
            _raise_preview_failure(error.issues)
        if isinstance(result, CommandRejectedDTO):
            _raise_preview_failure(result.issues)
        if result.preview_token is None or result.expected_definition_revision is None:
            _raise_preview_failure((_issue("OBJECT_UNAVAILABLE"),))
        return ImpactPreview(
            token=str(result.preview_token),
            definition_revision=str(result.expected_definition_revision),
            issues=_user_errors(result.issues, prefix_input=False),
        )


def _definition_rejection(*issues: DomainIssueDTO) -> DefinitionRejectedDTO:
    return DefinitionRejectedDTO(
        outcome="rejected",
        definition_kind=None,
        definition_id=None,
        identity=None,
        issues=tuple(issues) or (_issue("OBJECT_UNAVAILABLE"),),
    )


def _choice_definition(value: object, *, path: Sequence[str]) -> tuple[str, str]:
    key = _string_value(_field(value, "key"), path=(*path, "key"), allow_empty=False)
    label = _string_value(_field(value, "label"), path=(*path, "label"))
    return key, label


def _field_update_changes(value: object) -> CustomFieldUpdateInputDTO:
    activation = _field(value, "activation")
    if activation is _MISSING or activation is None:
        activation_value = None
    else:
        activation_value = str(_enum_value(activation))
    return CustomFieldUpdateInputDTO(
        label=_optional_string(_field(value, "label"), path=("input", "label")),
        help_text=_optional_string(_field(value, "help_text"), path=("input", "helpText")),
        activation=activation_value,  # type: ignore[arg-type]
        required=_optional_bool(_field(value, "required"), path=("input", "required")),
    )


def _fieldset_create(value: object) -> CustomFieldsetCreateInputDTO:
    identity = _identity(_field(value, "identity"), path=("input", "identity"))
    namespace, slug = identity.split("/", 1)
    fields = _identity_list(_field(value, "fields"), path=("input", "fields"))
    return CustomFieldsetCreateInputDTO(
        namespace=namespace,
        slug=slug,
        label=_string_value(_field(value, "label"), path=("input", "label")),
        description=_optional_string(_field(value, "description"), path=("input", "description")) or "",
        field_identities=fields,
    )


def _choice_set_create(value: object) -> CustomFieldChoiceSetCreateInputDTO:
    identity = _identity(_field(value, "identity"), path=("input", "identity"))
    namespace, slug = identity.split("/", 1)
    return CustomFieldChoiceSetCreateInputDTO(
        namespace=namespace,
        slug=slug,
        label=_string_value(_field(value, "label"), path=("input", "label")),
    )


class _DefinitionAbort(Exception):
    def __init__(self, result: object):
        self.result = result
        super().__init__("definition command sequence rejected")


class CreateSpecificationField(graphene.Mutation):
    class Arguments:
        input = CreateSpecificationFieldInput(required=True)

    Output = DefinitionPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            definition = _parse_field_create(input)
            choice_set = _field(input, "choice_set")
            if choice_set is not _MISSING and choice_set is not None:
                choice_set_identity = _identity(choice_set, path=("input", "choiceSet"))
                choice_set_id, rejected = _definition_id_or_rejection("choice_set", choice_set_identity)
                if rejected is not None:
                    return _definition_payload(rejected)
                definition = replace(definition, choice_set_id=choice_set_id)
            actor = _actor(info)
        except _InputError as error:
            return _definition_payload(_definition_rejection(*error.issues))
        result = _definition_result(lambda: create_custom_field(actor=actor, definition=definition))
        return _definition_payload(result)


class UpdateSpecificationFieldPolicy(graphene.Mutation):
    class Arguments:
        input = UpdateSpecificationFieldPolicyInput(required=True)

    Output = DefinitionPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            identity = _identity(_field(input, "identity"), path=("input", "identity"))
            expected = _required_string(
                _field(input, "expected_resource_revision"),
                path=("input", "expectedResourceRevision"),
            )
            impact_token = _field(input, "impact_token")
            _impact_token_guard(impact_token, path=("input", "impactToken"))
            lifecycle = _lifecycle(_field(input, "lifecycle"), path=("input", "lifecycle"))
            definition_id, rejected = _definition_id_or_rejection("field", identity)
            if rejected is not None:
                return _definition_payload(rejected)
            actor = _actor(info)
            if lifecycle == "deprecated":
                if any(
                    _field(input, name) not in {_MISSING, None}
                    for name in ("label", "help_text", "required", "activation")
                ):
                    return _definition_payload(_definition_rejection(_issue("UNSUPPORTED_STRUCTURE")))
                result = _definition_result(
                    lambda: deprecate_custom_field(
                        actor=actor,
                        field_id=definition_id,
                        expected_resource_revision=expected,
                    )
                )
            elif (
                lifecycle == "active" and _definition_model("field", identity).lifecycle != CustomField.LIFECYCLE_ACTIVE
            ):
                result = _definition_rejection(_issue("IMMUTABLE_DEFINITION"))
            else:
                result = _definition_result(
                    lambda: update_custom_field(
                        actor=actor,
                        field_id=definition_id,
                        expected_resource_revision=expected,
                        changes=_field_update_changes(input),
                    )
                )
        except _InputError as error:
            return _definition_payload(_definition_rejection(*error.issues))
        return _definition_payload(result)


class CreateSpecificationFieldset(graphene.Mutation):
    class Arguments:
        input = CreateSpecificationFieldsetInput(required=True)

    Output = DefinitionPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            definition = _fieldset_create(input)
            actor = _actor(info)
        except _InputError as error:
            return _definition_payload(_definition_rejection(*error.issues))
        result = _definition_result(lambda: create_custom_fieldset(actor=actor, definition=definition))
        return _definition_payload(result)


class UpdateSpecificationFieldset(graphene.Mutation):
    class Arguments:
        input = UpdateSpecificationFieldsetInput(required=True)

    Output = DefinitionPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            identity = _identity(_field(input, "identity"), path=("input", "identity"))
            expected = _required_string(
                _field(input, "expected_resource_revision"),
                path=("input", "expectedResourceRevision"),
            )
            impact_token = _field(input, "impact_token")
            _impact_token_guard(impact_token, path=("input", "impactToken"))
            lifecycle = _lifecycle(_field(input, "lifecycle"), path=("input", "lifecycle"))
            fieldset_id, rejected = _definition_id_or_rejection("fieldset", identity)
            if rejected is not None:
                return _definition_payload(rejected)
            label = _optional_string(_field(input, "label"), path=("input", "label"))
            description = _optional_string(_field(input, "description"), path=("input", "description"))
            fields_raw = _field(input, "fields")
            fields = (
                None
                if fields_raw is _MISSING or fields_raw is None
                else _identity_list(fields_raw, path=("input", "fields"))
            )
            has_policy = label is not None or description is not None
            actor = _actor(info)
            if lifecycle == "deprecated":
                if has_policy or fields is not None:
                    return _definition_payload(_definition_rejection(_issue("UNSUPPORTED_STRUCTURE")))
                result = _definition_result(
                    lambda: deprecate_custom_fieldset(
                        actor=actor,
                        fieldset_id=fieldset_id,
                        expected_resource_revision=expected,
                    )
                )
            elif (
                lifecycle == "active"
                and _definition_model("fieldset", identity).lifecycle != CustomFieldset.LIFECYCLE_ACTIVE
            ):
                result = _definition_rejection(_issue("IMMUTABLE_DEFINITION"))
            elif fields is not None and has_policy:
                return _definition_payload(_definition_rejection(_issue("UNSUPPORTED_STRUCTURE")))
            elif fields is not None:
                result = _definition_result(
                    lambda: replace_custom_fieldset_memberships(
                        actor=actor,
                        fieldset_id=fieldset_id,
                        field_identities=fields,
                        expected_resource_revision=expected,
                    )
                )
            else:
                result = _definition_result(
                    lambda: update_custom_fieldset(
                        actor=actor,
                        fieldset_id=fieldset_id,
                        expected_resource_revision=expected,
                        changes=CustomFieldsetUpdateInputDTO(label=label, description=description),
                    )
                )
        except _InputError as error:
            return _definition_payload(_definition_rejection(*error.issues))
        return _definition_payload(result)


class CreateChoiceSet(graphene.Mutation):
    class Arguments:
        input = CreateChoiceSetInput(required=True)

    Output = DefinitionPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            definition = _choice_set_create(input)
            raw_choices = _field(input, "choices")
            if not isinstance(raw_choices, (list, tuple)):
                _raise_input("INVALID_TYPE", path=("input", "choices"))
            choices = tuple(_choice_definition(item, path=("input", "choices")) for item in raw_choices)
            actor = _actor(info)
        except _InputError as error:
            return _definition_payload(_definition_rejection(*error.issues))
        try:
            with transaction.atomic():
                result = _definition_result(lambda: create_custom_field_choice_set(actor=actor, definition=definition))
                if isinstance(result, DefinitionRejectedDTO):
                    raise _DefinitionAbort(result)
                for position, (key, label) in enumerate(choices, start=1):
                    choice_result = _definition_result(
                        lambda key=key, label=label, position=position: create_custom_field_choice(
                            actor=actor,
                            definition=CustomFieldChoiceCreateInputDTO(
                                choice_set_id=result.definition_id,
                                key=key,
                                label=label,
                                position=position,
                            ),
                        )
                    )
                    if isinstance(choice_result, DefinitionRejectedDTO):
                        raise _DefinitionAbort(choice_result)
        except _DefinitionAbort as abort:
            result = abort.result
        return _definition_payload(result)


class UpdateChoiceSet(graphene.Mutation):
    class Arguments:
        input = UpdateChoiceSetInput(required=True)

    Output = DefinitionPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            identity = _identity(_field(input, "identity"), path=("input", "identity"))
            expected = _required_string(
                _field(input, "expected_resource_revision"),
                path=("input", "expectedResourceRevision"),
            )
            _impact_token_guard(_field(input, "impact_token"), path=("input", "impactToken"))
            lifecycle = _lifecycle(_field(input, "lifecycle"), path=("input", "lifecycle"))
            definition_id, rejected = _definition_id_or_rejection("choice_set", identity)
            if rejected is not None:
                return _definition_payload(rejected)
            label = _optional_string(_field(input, "label"), path=("input", "label"))
            actor = _actor(info)
            if lifecycle == "deprecated":
                if label is not None:
                    return _definition_payload(_definition_rejection(_issue("UNSUPPORTED_STRUCTURE")))
                result = _definition_result(
                    lambda: deprecate_custom_field_choice_set(
                        actor=actor,
                        choice_set_id=definition_id,
                        expected_resource_revision=expected,
                    )
                )
            elif (
                lifecycle == "active"
                and _definition_model("choice_set", identity).lifecycle != CustomFieldChoiceSet.LIFECYCLE_ACTIVE
            ):
                result = _definition_rejection(_issue("IMMUTABLE_DEFINITION"))
            else:
                result = _definition_result(
                    lambda: update_custom_field_choice_set(
                        actor=actor,
                        choice_set_id=definition_id,
                        expected_resource_revision=expected,
                        changes=CustomFieldChoiceSetUpdateInputDTO(label=label),
                    )
                )
        except _InputError as error:
            return _definition_payload(_definition_rejection(*error.issues))
        return _definition_payload(result)


class AddChoice(graphene.Mutation):
    class Arguments:
        input = AddChoiceInput(required=True)

    Output = DefinitionPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            choice_set_identity = _identity(_field(input, "choice_set"), path=("input", "choiceSet"))
            expected = _required_string(
                _field(input, "expected_resource_revision"),
                path=("input", "expectedResourceRevision"),
            )
            key, label = _choice_definition(_field(input, "choice"), path=("input", "choice"))
            choice_set_id, rejected = _definition_id_or_rejection("choice_set", choice_set_identity)
            if rejected is not None:
                return _definition_payload(rejected)
            choice_set = _definition_model("choice_set", choice_set_identity)
            if str(resource_revision_for_definition(choice_set)) != expected:
                return _definition_payload(_definition_rejection(_issue("STALE_RESOURCE")))
            last_position = choice_set.choices.order_by("-position").values_list("position", flat=True).first() or 0
            actor = _actor(info)
        except _InputError as error:
            return _definition_payload(_definition_rejection(*error.issues))
        result = _definition_result(
            lambda: create_custom_field_choice(
                actor=actor,
                definition=CustomFieldChoiceCreateInputDTO(
                    choice_set_id=choice_set_id,
                    key=key,
                    label=label,
                    position=last_position + 1,
                ),
            )
        )
        return _definition_payload(result)


class UpdateChoice(graphene.Mutation):
    class Arguments:
        input = UpdateChoiceInput(required=True)

    Output = DefinitionPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            choice_set_identity = _identity(_field(input, "choice_set"), path=("input", "choiceSet"))
            key = _string_value(_field(input, "key"), path=("input", "key"), allow_empty=False)
            expected = _required_string(
                _field(input, "expected_resource_revision"),
                path=("input", "expectedResourceRevision"),
            )
            _impact_token_guard(_field(input, "impact_token"), path=("input", "impactToken"))
            lifecycle = _lifecycle(_field(input, "lifecycle"), path=("input", "lifecycle"))
            choice_identity = f"{choice_set_identity}#{key}"
            choice_id, rejected = _definition_id_or_rejection("choice", choice_identity)
            if rejected is not None:
                return _definition_payload(rejected)
            choice = _definition_model("choice", choice_identity)
            label = _optional_string(_field(input, "label"), path=("input", "label"))
            actor = _actor(info)
            if lifecycle == "deprecated":
                if label is not None:
                    return _definition_payload(_definition_rejection(_issue("UNSUPPORTED_STRUCTURE")))
                result = _definition_result(
                    lambda: deprecate_custom_field_choice(
                        actor=actor,
                        choice_id=choice_id,
                        expected_resource_revision=expected,
                    )
                )
            elif lifecycle == "active" and choice.lifecycle != CustomFieldChoice.LIFECYCLE_ACTIVE:
                result = _definition_rejection(_issue("IMMUTABLE_DEFINITION"))
            else:
                result = _definition_result(
                    lambda: update_custom_field_choice(
                        actor=actor,
                        choice_id=choice_id,
                        expected_resource_revision=expected,
                        changes=CustomFieldChoiceUpdateInputDTO(label=label),
                    )
                )
        except _InputError as error:
            return _definition_payload(_definition_rejection(*error.issues))
        return _definition_payload(result)


@dataclass(frozen=True)
class _ChoiceReorderData:
    choice_set_id: int
    expected: str
    keys: tuple[str, ...]
    choice_set: object
    choices: dict[str, object]


def _prepare_choice_reorder(value: object) -> _ChoiceReorderData | DefinitionRejectedDTO:
    choice_set_identity = _identity(_field(value, "choice_set"), path=("input", "choiceSet"))
    expected = _required_string(
        _field(value, "expected_resource_revision"),
        path=("input", "expectedResourceRevision"),
    )
    raw_keys = _field(value, "keys")
    if not isinstance(raw_keys, (list, tuple)) or any(type(key) is not str or not key for key in raw_keys):
        _raise_input("INVALID_TYPE", path=("input", "keys"))
    keys = tuple(raw_keys)
    if len(set(keys)) != len(keys):
        _raise_input("DUPLICATE_FIELD", path=("input", "keys"))
    choice_set_id, rejected = _definition_id_or_rejection("choice_set", choice_set_identity)
    if rejected is not None:
        return rejected
    choice_set = _definition_model("choice_set", choice_set_identity)
    choices = {choice.key: choice for choice in choice_set.choices.all()}
    if set(keys) != set(choices):
        return _definition_rejection(_issue("REFERENCE_CONFLICT", path=("input", "keys")))
    return _ChoiceReorderData(
        choice_set_id=choice_set_id,
        expected=expected,
        keys=keys,
        choice_set=choice_set,
        choices=choices,
    )


def _execute_choice_reorder(data: _ChoiceReorderData, actor: ActorContextDTO) -> object:
    with transaction.atomic():
        current_set_revision = str(resource_revision_for_definition(data.choice_set))
        if current_set_revision != data.expected:
            raise _DefinitionAbort(_definition_rejection(_issue("STALE_RESOURCE")))
        for position, key in enumerate(data.keys, start=1):
            choice = data.choices[key]
            result = _definition_result(
                lambda choice=choice, position=position: update_custom_field_choice(
                    actor=actor,
                    choice_id=choice.pk,
                    expected_resource_revision=str(resource_revision_for_definition(choice)),
                    changes=CustomFieldChoiceUpdateInputDTO(position=position),
                )
            )
            if isinstance(result, DefinitionRejectedDTO):
                raise _DefinitionAbort(result)
        return _definition_result(
            lambda: update_custom_field_choice_set(
                actor=actor,
                choice_set_id=data.choice_set_id,
                expected_resource_revision=str(resource_revision_for_definition(data.choice_set)),
                changes=CustomFieldChoiceSetUpdateInputDTO(),
            )
        )


class ReorderChoices(graphene.Mutation):
    class Arguments:
        input = ReorderChoicesInput(required=True)

    Output = DefinitionPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            data = _prepare_choice_reorder(input)
            if isinstance(data, DefinitionRejectedDTO):
                return _definition_payload(data)
            actor = _actor(info)
        except _InputError as error:
            return _definition_payload(_definition_rejection(*error.issues))
        try:
            result = _execute_choice_reorder(data, actor)
        except _DefinitionAbort as abort:
            result = abort.result
        return _definition_payload(result)


class PreviewAssetTypeComposition(graphene.Mutation):
    class Arguments:
        asset_type_id = graphene.ID(required=True)
        fieldsets = graphene.List(graphene.NonNull(graphene.String), required=True)

    Output = ImpactPreview

    @staticmethod
    def mutate(root, info, asset_type_id, fieldsets):
        del root
        try:
            asset_type_id = _positive_id(asset_type_id, path=("assetTypeId",))
            if not isinstance(fieldsets, (list, tuple)):
                _raise_input("INVALID_TYPE", path=("fieldsets",))
            fieldsets = tuple(_identity(item, path=("fieldsets",)) for item in fieldsets)
            if len(set(fieldsets)) != len(fieldsets):
                _raise_input("DUPLICATE_FIELD", path=("fieldsets",))
            actor = _actor(info)
            user = authenticated_user(info)
        except _InputError as error:
            _raise_preview_failure(error.issues)
        if user is None or not has_global_model_permission(user, AssetType, "change_assettype"):
            _raise_preview_failure((_issue("OBJECT_UNAVAILABLE"),))
        owner = AssetType.all_objects.filter(pk=asset_type_id, deleted_at__isnull=True).first()
        if owner is None:
            _raise_preview_failure((_issue("OBJECT_UNAVAILABLE"),))
        try:
            definition, _, _ = load_prospective_definition(fieldsets, "asset_type", tuple(stored_values_for(owner)))
            expected_resource_revision = str(owner_resource_revision(owner))
            expected_definition_revision = str(definition.revision)
            token = issue_preview_token(
                PreviewTokenExpectation(
                    actor_id=int(actor.actor_id),
                    authentication_revision=str(actor.authentication_revision),
                    access_scope_fingerprint=None,
                    command_kind="set_asset_type_composition",
                    target=PreviewOwnerRef("asset_type", asset_type_id),
                    normalized_input_digest=normalized_input_digest({"fieldsets": fieldsets}),
                    expected_resource_revision=expected_resource_revision,
                    expected_definition_revision=expected_definition_revision,
                    expected_category_default_snapshot_revision=None,
                    historical_state_digest=None,
                ),
                key=_preview_token_key(),
            )
        except (KeyError, TypeError, ValueError, RuntimeError):
            _raise_preview_failure((_issue("OBJECT_UNAVAILABLE"),))
        return ImpactPreview(token=token, definition_revision=expected_definition_revision, issues=())


class CleanupSpecificationHistory(graphene.Mutation):
    class Arguments:
        input = CleanupSpecificationHistoryInput(required=True)

    Output = CleanupPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            target_kind = _target(_field(input, "target"))
            owner_id = _positive_id(_field(input, "owner_id"), path=("input", "ownerId"))
            keys = _keys(_field(input, "keys"), path=("input", "keys"))
            preview_token = _required_string(_field(input, "preview_token"), path=("input", "previewToken"))
            expected_resource_revision = _required_string(
                _field(input, "expected_resource_revision"),
                path=("input", "expectedResourceRevision"),
            )
            expected_definition_revision = _required_string(
                _field(input, "expected_definition_revision"),
                path=("input", "expectedDefinitionRevision"),
            )
            if target_kind == "asset":
                authorization = _asset_authorization(info, _field(input, "requested_scope"))
                if authorization is None:
                    return _cleanup_payload(CommandRejectedDTO("rejected", None, (_issue("OBJECT_UNAVAILABLE"),)), keys)
            else:
                if _has_field(input, "requested_scope"):
                    _raise_input("INVALID_TYPE", path=("input", "requestedScope"))
                authorization = None
                actor = _actor(info)
        except _InputError as error:
            return _cleanup_payload(CommandRejectedDTO("rejected", None, error.issues), ())

        if target_kind == "asset":
            result = _command_result(
                lambda: cleanup_asset_history(
                    authorization=authorization,
                    asset_id=owner_id,
                    keys=keys,
                    preview_token=preview_token,
                    expected_resource_revision=expected_resource_revision,
                    expected_definition_revision=expected_definition_revision,
                )
            )
        else:
            result = _command_result(
                lambda: cleanup_asset_type_history(
                    actor=actor,
                    asset_type_id=owner_id,
                    keys=keys,
                    preview_token=preview_token,
                    expected_resource_revision=expected_resource_revision,
                    expected_definition_revision=expected_definition_revision,
                )
            )
        return _cleanup_payload(result, keys)


def _library_errors(error: Exception) -> tuple[UserErrorType, ...]:
    issues = getattr(error, "issues", ())
    rendered = []
    for item in issues:
        code = str(getattr(item, "code", getattr(error, "code", "OBJECT_UNAVAILABLE")))
        path = tuple(str(part) for part in getattr(item, "path", ()))
        message = str(getattr(item, "message", _message(code.lower())))
        rendered.append(UserErrorType(code=code, path=path, field_key=None, message=message))
    if rendered:
        return tuple(rendered)
    code = str(getattr(error, "code", "OBJECT_UNAVAILABLE"))
    return (UserErrorType(code=code, path=(), field_key=None, message=_message(code.lower())),)


def _library_resolution_map(value: object, *, path: Sequence[str]) -> dict[str, str]:
    if not isinstance(value, (list, tuple)):
        _raise_input("INVALID_TYPE", path=path)
    result: dict[str, str] = {}
    for item in value:
        action_id = _string_value(_field(item, "action_id"), path=(*path, "actionId"), allow_empty=False)
        decision = _string_value(_field(item, "decision"), path=(*path, "decision"), allow_empty=False)
        if action_id in result:
            _raise_input("DUPLICATE_FIELD", path=path)
        result[action_id] = decision
    return result


def _library_plan_payload(result) -> LibraryPlanType:
    plan = result.plan
    actions = tuple(
        LibraryActionType(
            id=str(action.action_id),
            kind=str(action.action),
            identity=str(action.identity),
            path=tuple(str(part) for part in action.path),
            message=str(action.reason),
            blocking=action.action == "conflict" or action.decision in {"abort", "conflict"},
        )
        for action in plan.actions
    )
    return LibraryPlanType(
        token=str(result.preview_token),
        digest=str(plan.plan_digest),
        can_apply=bool(plan.can_apply),
        actions=actions,
        user_errors=(),
    )


class PreviewLibrary(graphene.Mutation):
    class Arguments:
        input = LibraryPreviewInput(required=True)

    Output = LibraryPlanType

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            document = _string_value(_field(input, "document_text"), path=("input", "documentText"), allow_empty=False)
            resolutions = _library_resolution_map(_field(input, "resolutions"), path=("input", "resolutions"))
            actor = authenticated_user(info)
            if actor is None:
                _raise_input("OBJECT_UNAVAILABLE", path=("input",))
            result = library_commands.preview_library(
                document, actor=actor, signing_key=_preview_token_key(), resolutions=resolutions or None
            )
        except _InputError as error:
            _raise_preview_failure(error.issues)
        except LibraryCommandError as error:
            _raise_preview_failure((_issue(error.code),))
        return _library_plan_payload(result)


class ApplyLibrary(graphene.Mutation):
    class Arguments:
        input = LibraryApplyInput(required=True)

    Output = LibraryApplyPayload

    @staticmethod
    def mutate(root, info, input):
        del root
        try:
            document = _string_value(_field(input, "document_text"), path=("input", "documentText"), allow_empty=False)
            token = _string_value(_field(input, "plan_token"), path=("input", "planToken"), allow_empty=False)
            resolutions = _library_resolution_map(_field(input, "resolutions"), path=("input", "resolutions"))
            actor = authenticated_user(info)
            if actor is None:
                return LibraryApplyPayload(
                    applied=False,
                    changed=False,
                    accepted_release=None,
                    user_errors=_user_errors((_issue("OBJECT_UNAVAILABLE"),)),
                )
            preview = library_commands.preview_library(
                document, actor=actor, signing_key=_preview_token_key(), resolutions=resolutions or None
            )
            library_module = library_commands

            request = LibraryApplyRequest(
                plan=preview.plan,
                token=token,
                actor_id=actor.pk,
                authentication_revision=authentication_revision_for_actor(actor),
                access_scope_fingerprint=None,
                signing_key=_preview_token_key(),
            )
            result = library_module.apply_library(document, request, actor=actor)
        except _InputError as error:
            return LibraryApplyPayload(
                applied=False,
                changed=False,
                accepted_release=None,
                user_errors=_user_errors(error.issues),
            )
        except (LibraryApplyError, LibraryCommandError) as error:
            return LibraryApplyPayload(
                applied=False,
                changed=False,
                accepted_release=None,
                user_errors=_library_errors(error),
            )
        return LibraryApplyPayload(
            applied=True,
            changed=not result.no_op,
            accepted_release=int(result.release),
            user_errors=(),
        )


class ExportLibrary(graphene.Mutation):
    class Arguments:
        namespace = graphene.String(required=True)
        mode = LibraryExportModeEnum(required=True)
        acknowledge_retained_history = graphene.Boolean(default_value=False)

    Output = LibraryExportPayload

    @staticmethod
    def mutate(root, info, namespace, mode, acknowledge_retained_history=False):
        del root
        actor = authenticated_user(info)
        if actor is None:
            return LibraryExportPayload(
                document_text=None,
                semantic_digest=None,
                user_errors=_user_errors((_issue("OBJECT_UNAVAILABLE"),)),
            )
        try:
            namespace = _string_value(namespace, path=("namespace",), allow_empty=False)
            mode = str(_enum_value(mode))
            result = library_commands.export_library(
                namespace, actor=actor, mode=mode, acknowledge_retained_history=acknowledge_retained_history is True
            )
        except _InputError as error:
            return LibraryExportPayload(
                document_text=None, semantic_digest=None, user_errors=_user_errors(error.issues)
            )
        except (LibraryCommandError, LibraryExportError) as error:
            return LibraryExportPayload(
                document_text=None,
                semantic_digest=None,
                user_errors=_library_errors(error),
            )
        return LibraryExportPayload(
            document_text=json.dumps(result.document, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            semantic_digest=str(result.semantic_digest),
            user_errors=(),
        )


__all__ = [
    "ApplyCategoryDefaults",
    "AssetSpecificationPayload",
    "AssetTypeCreatePreview",
    "AssetTypeCreatePreviewPayload",
    "AssetTypeSpecificationPayload",
    "CategoryDefaultsPayload",
    "CleanupPayload",
    "CleanupSpecificationHistory",
    "CreateAssetType",
    "ImpactPreview",
    "PreviewApplyCategoryDefaults",
    "PreviewAssetTypeCreate",
    "PreviewSpecificationHistoryCleanup",
    "SetAssetTypeComposition",
    "SetCategoryDefaults",
    "UpdateAssetSpecifications",
    "UpdateAssetTypeSpecifications",
]
