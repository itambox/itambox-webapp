"""GraphQL input objects for the typed specification command adapter."""

from __future__ import annotations

import graphene

from .scalars import CategoryDefaultSnapshotRevision, DateScalar, DecimalScalar, SafeInteger
from .types import (
    DefinitionLifecycleEnum,
    FieldActivationEnum,
    ScopeModeEnum,
    SpecificationFieldTypeEnum,
    SpecificationTargetEnum,
)


class RequestedScopeSelectorInput(graphene.InputObjectType):
    class Meta:
        name = "RequestedScopeSelector"

    mode = ScopeModeEnum(required=True)
    tenant_id = graphene.ID()
    tenant_group_id = graphene.ID()


class SpecificationValueInput(graphene.InputObjectType):
    class Meta:
        name = "SpecificationValueInput"

    text = graphene.String()
    integer = SafeInteger()
    decimal = DecimalScalar()
    boolean = graphene.Boolean()
    date = DateScalar()
    choice = graphene.String()
    multi_choice = graphene.List(graphene.NonNull(graphene.String))
    null_value = graphene.Boolean()


class SpecificationSetInput(graphene.InputObjectType):
    class Meta:
        name = "SpecificationSetInput"

    key = graphene.String(required=True)
    value = SpecificationValueInput(required=True)


class SpecificationPatchInput(graphene.InputObjectType):
    class Meta:
        name = "SpecificationPatchInput"

    set = graphene.List(graphene.NonNull(SpecificationSetInput), required=True, default_value=())
    clear = graphene.List(graphene.NonNull(graphene.String), required=True, default_value=())


class UpdateAssetSpecificationsInput(graphene.InputObjectType):
    class Meta:
        name = "UpdateAssetSpecificationsInput"

    asset_id = graphene.ID(required=True)
    asset_type_id = graphene.ID()
    requested_scope = RequestedScopeSelectorInput(required=True)
    expected_resource_revision = graphene.String(required=True)
    expected_definition_revision = graphene.String(required=True)
    patch = SpecificationPatchInput(required=True)


class UpdateAssetTypeSpecificationsInput(graphene.InputObjectType):
    class Meta:
        name = "UpdateAssetTypeSpecificationsInput"

    asset_type_id = graphene.ID(required=True)
    expected_resource_revision = graphene.String(required=True)
    expected_definition_revision = graphene.String(required=True)
    patch = SpecificationPatchInput(required=True)


class PreviewAssetTypeCreateInput(graphene.InputObjectType):
    class Meta:
        name = "PreviewAssetTypeCreateInput"

    manufacturer_id = graphene.ID(required=True)
    model = graphene.String(required=True)
    category_id = graphene.ID()
    asset_role_id = graphene.ID()
    fieldsets = graphene.List(graphene.NonNull(graphene.String))
    patch = SpecificationPatchInput(required=True)


class CreateAssetTypeInput(graphene.InputObjectType):
    class Meta:
        name = "CreateAssetTypeInput"

    manufacturer_id = graphene.ID(required=True)
    model = graphene.String(required=True)
    category_id = graphene.ID()
    asset_role_id = graphene.ID()
    fieldsets = graphene.List(graphene.NonNull(graphene.String))
    expected_definition_revision = graphene.String(required=True)
    expected_category_default_snapshot_revision = CategoryDefaultSnapshotRevision()
    preview_token = graphene.String()
    patch = SpecificationPatchInput(required=True)


class PreviewApplyCategoryDefaultsInput(graphene.InputObjectType):
    class Meta:
        name = "PreviewApplyCategoryDefaultsInput"

    asset_type_id = graphene.ID(required=True)
    expected_resource_revision = graphene.String(required=True)
    patch = SpecificationPatchInput(required=True)


class ApplyCategoryDefaultsInput(graphene.InputObjectType):
    class Meta:
        name = "ApplyCategoryDefaultsInput"

    asset_type_id = graphene.ID(required=True)
    expected_resource_revision = graphene.String(required=True)
    expected_definition_revision = graphene.String(required=True)
    expected_category_default_snapshot_revision = CategoryDefaultSnapshotRevision(required=True)
    preview_token = graphene.String(required=True)
    patch = SpecificationPatchInput(required=True)


class SetAssetTypeCompositionInput(graphene.InputObjectType):
    class Meta:
        name = "SetAssetTypeCompositionInput"

    asset_type_id = graphene.ID(required=True)
    expected_resource_revision = graphene.String(required=True)
    expected_definition_revision = graphene.String(required=True)
    fieldsets = graphene.List(graphene.NonNull(graphene.String), required=True)
    patch = SpecificationPatchInput(required=True)


class SetCategoryDefaultsInput(graphene.InputObjectType):
    class Meta:
        name = "SetCategoryDefaultsInput"

    category_id = graphene.ID(required=True)
    expected_resource_revision = graphene.String(required=True)
    fieldsets = graphene.List(graphene.NonNull(graphene.String), required=True)


class CleanupSpecificationHistoryInput(graphene.InputObjectType):
    class Meta:
        name = "CleanupSpecificationHistoryInput"

    owner_id = graphene.ID(required=True)
    target = SpecificationTargetEnum(required=True)
    requested_scope = RequestedScopeSelectorInput()
    expected_resource_revision = graphene.String(required=True)
    expected_definition_revision = graphene.String(required=True)
    keys = graphene.List(graphene.NonNull(graphene.String), required=True)
    preview_token = graphene.String(required=True)


class SpecificationValidationInput(graphene.InputObjectType):
    class Meta:
        name = "SpecificationValidationInput"

    minimum = DecimalScalar()
    maximum = DecimalScalar()
    scale = graphene.Int()
    max_length = graphene.Int()
    max_values = graphene.Int()
    regex = graphene.String()
    rule = graphene.String()


class CreateSpecificationFieldInput(graphene.InputObjectType):
    class Meta:
        name = "CreateSpecificationFieldInput"

    key = graphene.String(required=True)
    namespace = graphene.String(required=True)
    label = graphene.String(required=True)
    help_text = graphene.String(required=True, default_value="")
    targets = graphene.List(graphene.NonNull(SpecificationTargetEnum), required=True)
    activation = FieldActivationEnum(required=True)
    field_type = SpecificationFieldTypeEnum(required=True)
    required = graphene.Boolean(default_value=False, required=True)
    nullable = graphene.Boolean(default_value=False, required=True)
    quantity_kind = graphene.String()
    canonical_unit = graphene.String()
    validation = SpecificationValidationInput(required=True)
    choice_set = graphene.String()


class UpdateSpecificationFieldPolicyInput(graphene.InputObjectType):
    class Meta:
        name = "UpdateSpecificationFieldPolicyInput"

    identity = graphene.String(required=True)
    expected_resource_revision = graphene.String(required=True)
    label = graphene.String()
    help_text = graphene.String()
    required = graphene.Boolean()
    activation = FieldActivationEnum()
    lifecycle = DefinitionLifecycleEnum()
    impact_token = graphene.String()


class CreateSpecificationFieldsetInput(graphene.InputObjectType):
    class Meta:
        name = "CreateSpecificationFieldsetInput"

    identity = graphene.String(required=True)
    label = graphene.String(required=True)
    description = graphene.String(required=True, default_value="")
    fields = graphene.List(graphene.NonNull(graphene.String), required=True)


class UpdateSpecificationFieldsetInput(graphene.InputObjectType):
    class Meta:
        name = "UpdateSpecificationFieldsetInput"

    identity = graphene.String(required=True)
    expected_resource_revision = graphene.String(required=True)
    label = graphene.String()
    description = graphene.String()
    fields = graphene.List(graphene.NonNull(graphene.String))
    lifecycle = DefinitionLifecycleEnum()
    impact_token = graphene.String()


class ChoiceDefinitionInput(graphene.InputObjectType):
    class Meta:
        name = "ChoiceDefinitionInput"

    key = graphene.String(required=True)
    label = graphene.String(required=True)


class CreateChoiceSetInput(graphene.InputObjectType):
    class Meta:
        name = "CreateChoiceSetInput"

    identity = graphene.String(required=True)
    label = graphene.String(required=True)
    choices = graphene.List(graphene.NonNull(ChoiceDefinitionInput), required=True)


class AddChoiceInput(graphene.InputObjectType):
    class Meta:
        name = "AddChoiceInput"

    choice_set = graphene.String(required=True)
    expected_resource_revision = graphene.String(required=True)
    choice = ChoiceDefinitionInput(required=True)


class UpdateChoiceInput(graphene.InputObjectType):
    class Meta:
        name = "UpdateChoiceInput"

    choice_set = graphene.String(required=True)
    key = graphene.String(required=True)
    expected_resource_revision = graphene.String(required=True)
    label = graphene.String()
    lifecycle = DefinitionLifecycleEnum()
    impact_token = graphene.String()


class ReorderChoicesInput(graphene.InputObjectType):
    class Meta:
        name = "ReorderChoicesInput"

    choice_set = graphene.String(required=True)
    expected_resource_revision = graphene.String(required=True)
    keys = graphene.List(graphene.NonNull(graphene.String), required=True)


class UpdateChoiceSetInput(graphene.InputObjectType):
    class Meta:
        name = "UpdateChoiceSetInput"

    identity = graphene.String(required=True)
    expected_resource_revision = graphene.String(required=True)
    label = graphene.String()
    lifecycle = DefinitionLifecycleEnum()
    impact_token = graphene.String()


class LibraryResolutionInput(graphene.InputObjectType):
    class Meta:
        name = "LibraryResolutionInput"

    action_id = graphene.String(required=True)
    decision = graphene.String(required=True)


class LibraryPreviewInput(graphene.InputObjectType):
    class Meta:
        name = "LibraryPreviewInput"

    document_text = graphene.String(required=True)
    resolutions = graphene.List(graphene.NonNull(LibraryResolutionInput), required=True, default_value=())


class LibraryApplyInput(graphene.InputObjectType):
    class Meta:
        name = "LibraryApplyInput"

    document_text = graphene.String(required=True)
    plan_token = graphene.String(required=True)
    resolutions = graphene.List(graphene.NonNull(LibraryResolutionInput), required=True)


__all__ = [
    "AddChoiceInput",
    "ApplyCategoryDefaultsInput",
    "ChoiceDefinitionInput",
    "CleanupSpecificationHistoryInput",
    "CreateAssetTypeInput",
    "CreateChoiceSetInput",
    "CreateSpecificationFieldInput",
    "CreateSpecificationFieldsetInput",
    "LibraryApplyInput",
    "LibraryPreviewInput",
    "LibraryResolutionInput",
    "PreviewApplyCategoryDefaultsInput",
    "PreviewAssetTypeCreateInput",
    "ReorderChoicesInput",
    "RequestedScopeSelectorInput",
    "SetAssetTypeCompositionInput",
    "SetCategoryDefaultsInput",
    "SpecificationPatchInput",
    "SpecificationSetInput",
    "SpecificationValidationInput",
    "SpecificationValueInput",
    "UpdateAssetSpecificationsInput",
    "UpdateAssetTypeSpecificationsInput",
    "UpdateChoiceInput",
    "UpdateChoiceSetInput",
    "UpdateSpecificationFieldPolicyInput",
    "UpdateSpecificationFieldsetInput",
]
