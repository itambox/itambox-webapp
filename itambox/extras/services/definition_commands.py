"""Globally authorized ORM commands for reusable specification definitions."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, IntegrityError, transaction
from django.utils import timezone

from assets.services.specifications._command_support import actor_change_context, json_values_equal
from assets.services.specifications.locking import catalogue_transaction_lock
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset, CustomFieldsetField
from extras.services._definition_command_support import (
    DefinitionCommandError,
    authorize_locked,
    close_command_error,
    close_integrity_error,
    close_validation_error,
    issue,
    lock_choice_dependencies,
    lock_field_dependencies,
    lock_one,
    lock_rows,
    mapping_values,
    reject_for,
    require_positive_id,
    require_revision,
    resolve_choice_replacement,
    resolve_content_types,
    resolve_field_identities,
    resolve_replacement,
    resource_revision_for_definition,
    save_definition,
    success,
    validate_field_key,
    validate_local_definition,
    validate_namespace,
)
from extras.services.definition_command_contracts import (
    CustomFieldChoiceCreateInputDTO,
    CustomFieldChoiceSetCreateInputDTO,
    CustomFieldChoiceSetUpdateInputDTO,
    CustomFieldChoiceUpdateInputDTO,
    CustomFieldCreateInputDTO,
    CustomFieldsetCreateInputDTO,
    CustomFieldsetUpdateInputDTO,
    CustomFieldUpdateInputDTO,
)
from extras.services.specifications.contracts import QualifiedIdentity, ResourceRevision
from organization.services.access_scope import ActorContextDTO


def _require_input(value: object, expected: type[object], name: str) -> None:
    if not isinstance(value, expected):
        raise TypeError(f"{name} must be a {expected.__name__}")


def _apply_field_changes(field: CustomField, changes: CustomFieldUpdateInputDTO, *, using: str):
    target_content_types = None
    changed = False
    if changes.object_types is not None:
        target_content_types = resolve_content_types(changes.object_types, using=using)
    if changes.label is not None and field.label != changes.label:
        field.label = changes.label
        changed = True
    if changes.help_text is not None and field.help_text != changes.help_text:
        field.help_text = changes.help_text
        changed = True
    if changes.activation is not None and field.activation != changes.activation:
        field.activation = changes.activation
        changed = True
    if changes.required is not None and field.required != changes.required:
        field.required = changes.required
        changed = True
    if changes.mappings is not None and not json_values_equal(field.mappings, mapping_values(changes.mappings)):
        field.mappings = mapping_values(changes.mappings)
        changed = True
    if changes.replaced_by is not None and field.replaced_by != changes.replaced_by:
        resolve_replacement(CustomField, field, changes.replaced_by, using=using)
        field.replaced_by = changes.replaced_by
        changed = True
    return target_content_types, changed


def _save_field_and_maybe_set_types(
    field: CustomField,
    principal: object,
    target_content_types,
    *,
    using: str,
) -> None:
    with actor_change_context(principal):
        field.save(using=using)
        if target_content_types is not None:
            field.log_m2m_change("object_types", target_content_types, actor=principal)


def create_custom_field(
    *,
    actor: ActorContextDTO,
    definition: CustomFieldCreateInputDTO,
    using: str = DEFAULT_DB_ALIAS,
):
    _require_input(definition, CustomFieldCreateInputDTO, "definition")
    field: CustomField | None = None
    try:
        validate_namespace(definition.namespace)
        name = validate_field_key(definition.local_key, definition.namespace)
        with transaction.atomic(using=using):
            with catalogue_transaction_lock(exclusive=True, using=using):
                principal = authorize_locked(actor, CustomField, "add_customfield", using=using)
                content_types = resolve_content_types(definition.object_types, using=using)
                choice_set = None
                if definition.choice_set_id is not None:
                    lock_rows(CustomFieldChoiceSet, (definition.choice_set_id,), using=using)
                    choice_set = CustomFieldChoiceSet.objects.using(using).filter(pk=definition.choice_set_id).first()
                    if choice_set is None or choice_set.lifecycle != CustomFieldChoiceSet.LIFECYCLE_ACTIVE:
                        raise DefinitionCommandError(issue("REFERENCE_CONFLICT", path=("choice_set_id",)))
                field = CustomField(
                    name=name,
                    namespace=definition.namespace,
                    label=definition.label,
                    help_text=definition.help_text,
                    field_type=definition.field_type,
                    activation=definition.activation,
                    quantity_kind=definition.quantity_kind,
                    canonical_unit=definition.canonical_unit,
                    minimum_value=definition.minimum_value,
                    maximum_value=definition.maximum_value,
                    regex=definition.regex,
                    decimal_scale=definition.decimal_scale,
                    max_values=definition.max_values,
                    text_max_length=definition.text_max_length,
                    validation_rule=definition.validation_rule,
                    required=definition.required,
                    nullable=definition.nullable,
                    mappings=mapping_values(definition.mappings),
                    choice_set=choice_set,
                    replaced_by=None,
                    management_kind=CustomField.MANAGEMENT_LOCAL,
                    lifecycle=CustomField.LIFECYCLE_ACTIVE,
                    version=1,
                )
                if definition.replaced_by is not None:
                    resolve_replacement(CustomField, None, definition.replaced_by, using=using)
                    field.replaced_by = definition.replaced_by
                field.full_clean()
                _save_field_and_maybe_set_types(field, principal, content_types, using=using)
                field.full_clean()
            return success(field, "created", using=using)
    except DefinitionCommandError as error:
        return close_command_error(field, error)
    except ValidationError as error:
        return close_validation_error(field, error)
    except IntegrityError as error:
        return close_integrity_error(field, error)


def update_custom_field(
    *,
    actor: ActorContextDTO,
    field_id: int,
    expected_resource_revision: ResourceRevision,
    changes: CustomFieldUpdateInputDTO,
    using: str = DEFAULT_DB_ALIAS,
):
    _require_input(changes, CustomFieldUpdateInputDTO, "changes")
    field: CustomField | None = None
    try:
        field_id = require_positive_id(field_id, "field_id")
        expected = require_revision(expected_resource_revision)
        with transaction.atomic(using=using):
            with catalogue_transaction_lock(exclusive=True, using=using):
                field = lock_field_dependencies(field_id, using=using)
                principal = authorize_locked(actor, CustomField, "change_customfield", using=using)
                validate_local_definition(field)
                actual = resource_revision_for_definition(field, using=using)
                if actual != expected:
                    return reject_for(field, issue("STALE_RESOURCE", message_key="specifications.stale_resource"))
                target_content_types, changed = _apply_field_changes(field, changes, using=using)
                if target_content_types is not None:
                    current = set(field.object_types.using(using).values_list("pk", flat=True))
                    changed = changed or current != {content_type.pk for content_type in target_content_types}
                if not changed:
                    return success(field, "no_op", using=using)
                field.version += 1
                field.full_clean()
                _save_field_and_maybe_set_types(field, principal, target_content_types, using=using)
                field.full_clean()
            return success(field, "changed", using=using)
    except DefinitionCommandError as error:
        return close_command_error(field, error)
    except ValidationError as error:
        return close_validation_error(field, error)
    except IntegrityError as error:
        return close_integrity_error(field, error)


def deprecate_custom_field(
    *,
    actor: ActorContextDTO,
    field_id: int,
    expected_resource_revision: ResourceRevision,
    replacement_identity: QualifiedIdentity | None = None,
    using: str = DEFAULT_DB_ALIAS,
):
    field: CustomField | None = None
    try:
        field_id = require_positive_id(field_id, "field_id")
        expected = require_revision(expected_resource_revision)
        with transaction.atomic(using=using):
            with catalogue_transaction_lock(exclusive=True, using=using):
                field = lock_field_dependencies(field_id, using=using)
                principal = authorize_locked(actor, CustomField, "change_customfield", using=using)
                validate_local_definition(field)
                actual = resource_revision_for_definition(field, using=using)
                if actual != expected:
                    return reject_for(field, issue("STALE_RESOURCE", message_key="specifications.stale_resource"))
                if field.lifecycle == CustomField.LIFECYCLE_DEPRECATED:
                    if replacement_identity in {None, field.replaced_by}:
                        return success(field, "no_op", using=using)
                    return reject_for(field, issue("IMMUTABLE_DEFINITION"))
                if replacement_identity is not None:
                    resolve_replacement(CustomField, field, replacement_identity, using=using)
                field.lifecycle = CustomField.LIFECYCLE_DEPRECATED
                field.deprecated_at = timezone.now()
                field.replaced_by = replacement_identity
                field.version += 1
                field.full_clean()
                save_definition(field, principal, using=using)
            return success(field, "changed", using=using)
    except DefinitionCommandError as error:
        return close_command_error(field, error)
    except ValidationError as error:
        return close_validation_error(field, error)
    except IntegrityError as error:
        return close_integrity_error(field, error)


def _validate_fieldset_members(fieldset: CustomFieldset, fields: list[CustomField]) -> None:
    if fieldset.lifecycle != CustomFieldset.LIFECYCLE_ACTIVE:
        raise DefinitionCommandError(issue("IMMUTABLE_DEFINITION"))
    for position, field in enumerate(fields, start=1):
        if field.lifecycle != CustomField.LIFECYCLE_ACTIVE or field.activation != CustomField.ACTIVATION_COMPOSED:
            raise DefinitionCommandError(issue("REFERENCE_CONFLICT", path=(f"field_identities[{position - 1}]",)))
        membership = CustomFieldsetField(fieldset=fieldset, custom_field=field, position=position)
        membership.clean()


def _replace_fieldset_memberships_locked(
    fieldset: CustomFieldset,
    fields: list[CustomField],
    principal: object,
    *,
    using: str,
    bump_version: bool,
) -> bool:
    _validate_fieldset_members(fieldset, fields)
    current = list(
        CustomFieldsetField.objects.using(using)
        .filter(fieldset_id=fieldset.pk)
        .order_by("position", "custom_field_id")
        .values_list("custom_field_id", flat=True)
    )
    target = [field.pk for field in fields]
    if current == target:
        return False
    fieldset.snapshot()
    CustomFieldsetField.objects.using(using).filter(fieldset_id=fieldset.pk).delete()
    CustomFieldsetField.objects.using(using).bulk_create(
        [
            CustomFieldsetField(fieldset=fieldset, custom_field=field, position=position)
            for position, field in enumerate(fields, 1)
        ]
    )
    if bump_version:
        fieldset.version += 1
    with actor_change_context(principal):
        fieldset.save(using=using)
    return True


def create_custom_fieldset(
    *,
    actor: ActorContextDTO,
    definition: CustomFieldsetCreateInputDTO,
    using: str = DEFAULT_DB_ALIAS,
):
    _require_input(definition, CustomFieldsetCreateInputDTO, "definition")
    fieldset: CustomFieldset | None = None
    try:
        validate_namespace(definition.namespace)
        with transaction.atomic(using=using):
            with catalogue_transaction_lock(exclusive=True, using=using):
                principal = authorize_locked(actor, CustomFieldset, "add_customfieldset", using=using)
                fields = resolve_field_identities(definition.field_identities, using=using)
                locked_fields = lock_rows(CustomField, [field.pk for field in fields], using=using)
                by_id = {field.pk: field for field in locked_fields}
                fields = [by_id[field.pk] for field in fields]
                fieldset = CustomFieldset(
                    namespace=definition.namespace,
                    slug=definition.slug,
                    label=definition.label,
                    description=definition.description,
                    management_kind=CustomFieldset.MANAGEMENT_LOCAL,
                    lifecycle=CustomFieldset.LIFECYCLE_ACTIVE,
                    version=1,
                )
                if definition.replaced_by is not None:
                    resolve_replacement(CustomFieldset, None, definition.replaced_by, using=using)
                    fieldset.replaced_by = definition.replaced_by
                fieldset.full_clean()
                save_definition(fieldset, principal, using=using)
                _replace_fieldset_memberships_locked(fieldset, fields, principal, using=using, bump_version=True)
            return success(fieldset, "created", using=using)
    except DefinitionCommandError as error:
        return close_command_error(fieldset, error)
    except ValidationError as error:
        return close_validation_error(fieldset, error)
    except IntegrityError as error:
        return close_integrity_error(fieldset, error)


def update_custom_fieldset(
    *,
    actor: ActorContextDTO,
    fieldset_id: int,
    expected_resource_revision: ResourceRevision,
    changes: CustomFieldsetUpdateInputDTO,
    using: str = DEFAULT_DB_ALIAS,
):
    _require_input(changes, CustomFieldsetUpdateInputDTO, "changes")
    fieldset: CustomFieldset | None = None
    try:
        fieldset_id = require_positive_id(fieldset_id, "fieldset_id")
        expected = require_revision(expected_resource_revision)
        with transaction.atomic(using=using):
            with catalogue_transaction_lock(exclusive=True, using=using):
                fieldset = lock_one(CustomFieldset, fieldset_id, using=using)
                principal = authorize_locked(actor, CustomFieldset, "change_customfieldset", using=using)
                validate_local_definition(fieldset)
                actual = resource_revision_for_definition(fieldset, using=using)
                if actual != expected:
                    return reject_for(fieldset, issue("STALE_RESOURCE", message_key="specifications.stale_resource"))
                changed = False
                if changes.label is not None and fieldset.label != changes.label:
                    fieldset.label = changes.label
                    changed = True
                if changes.description is not None and fieldset.description != changes.description:
                    fieldset.description = changes.description
                    changed = True
                if changes.replaced_by is not None and fieldset.replaced_by != changes.replaced_by:
                    resolve_replacement(CustomFieldset, fieldset, changes.replaced_by, using=using)
                    fieldset.replaced_by = changes.replaced_by
                    changed = True
                if not changed:
                    return success(fieldset, "no_op", using=using)
                fieldset.version += 1
                fieldset.full_clean()
                save_definition(fieldset, principal, using=using)
            return success(fieldset, "changed", using=using)
    except DefinitionCommandError as error:
        return close_command_error(fieldset, error)
    except ValidationError as error:
        return close_validation_error(fieldset, error)
    except IntegrityError as error:
        return close_integrity_error(fieldset, error)


def deprecate_custom_fieldset(
    *,
    actor: ActorContextDTO,
    fieldset_id: int,
    expected_resource_revision: ResourceRevision,
    replacement_identity: QualifiedIdentity | None = None,
    using: str = DEFAULT_DB_ALIAS,
):
    fieldset: CustomFieldset | None = None
    try:
        fieldset_id = require_positive_id(fieldset_id, "fieldset_id")
        expected = require_revision(expected_resource_revision)
        with transaction.atomic(using=using):
            with catalogue_transaction_lock(exclusive=True, using=using):
                fieldset = lock_one(CustomFieldset, fieldset_id, using=using)
                principal = authorize_locked(actor, CustomFieldset, "change_customfieldset", using=using)
                validate_local_definition(fieldset)
                actual = resource_revision_for_definition(fieldset, using=using)
                if actual != expected:
                    return reject_for(fieldset, issue("STALE_RESOURCE", message_key="specifications.stale_resource"))
                if fieldset.lifecycle == CustomFieldset.LIFECYCLE_DEPRECATED:
                    if replacement_identity in {None, fieldset.replaced_by}:
                        return success(fieldset, "no_op", using=using)
                    return reject_for(fieldset, issue("IMMUTABLE_DEFINITION"))
                if replacement_identity is not None:
                    resolve_replacement(CustomFieldset, fieldset, replacement_identity, using=using)
                fieldset.lifecycle = CustomFieldset.LIFECYCLE_DEPRECATED
                fieldset.deprecated_at = timezone.now()
                fieldset.replaced_by = replacement_identity
                fieldset.version += 1
                fieldset.full_clean()
                save_definition(fieldset, principal, using=using)
            return success(fieldset, "changed", using=using)
    except DefinitionCommandError as error:
        return close_command_error(fieldset, error)
    except ValidationError as error:
        return close_validation_error(fieldset, error)
    except IntegrityError as error:
        return close_integrity_error(fieldset, error)


def replace_custom_fieldset_memberships(
    *,
    actor: ActorContextDTO,
    fieldset_id: int,
    field_identities: tuple[QualifiedIdentity, ...],
    expected_resource_revision: ResourceRevision,
    using: str = DEFAULT_DB_ALIAS,
):
    fieldset: CustomFieldset | None = None
    try:
        fieldset_id = require_positive_id(fieldset_id, "fieldset_id")
        expected = require_revision(expected_resource_revision)
        with transaction.atomic(using=using):
            with catalogue_transaction_lock(exclusive=True, using=using):
                fieldset = lock_one(CustomFieldset, fieldset_id, using=using)
                principal = authorize_locked(actor, CustomFieldset, "change_customfieldset", using=using)
                validate_local_definition(fieldset)
                actual = resource_revision_for_definition(fieldset, using=using)
                if actual != expected:
                    return reject_for(fieldset, issue("STALE_RESOURCE", message_key="specifications.stale_resource"))
                fields = resolve_field_identities(field_identities, using=using)
                locked_fields = lock_rows(CustomField, [field.pk for field in fields], using=using)
                by_id = {field.pk: field for field in locked_fields}
                fields = [by_id[field.pk] for field in fields]
                changed = _replace_fieldset_memberships_locked(
                    fieldset,
                    fields,
                    principal,
                    using=using,
                    bump_version=True,
                )
            return success(fieldset, "changed" if changed else "no_op", using=using)
    except DefinitionCommandError as error:
        return close_command_error(fieldset, error)
    except ValidationError as error:
        return close_validation_error(fieldset, error)
    except IntegrityError as error:
        return close_integrity_error(fieldset, error)


def _apply_choice_set_changes(
    choice_set: CustomFieldChoiceSet, changes: CustomFieldChoiceSetUpdateInputDTO, *, using: str
) -> bool:
    changed = False
    if changes.label is not None and choice_set.label != changes.label:
        choice_set.label = changes.label
        changed = True
    if changes.replaced_by is not None and choice_set.replaced_by != changes.replaced_by:
        resolve_replacement(CustomFieldChoiceSet, choice_set, changes.replaced_by, using=using)
        choice_set.replaced_by = changes.replaced_by
        changed = True
    return changed


def create_custom_field_choice_set(
    *,
    actor: ActorContextDTO,
    definition: CustomFieldChoiceSetCreateInputDTO,
    using: str = DEFAULT_DB_ALIAS,
):
    _require_input(definition, CustomFieldChoiceSetCreateInputDTO, "definition")
    choice_set: CustomFieldChoiceSet | None = None
    try:
        validate_namespace(definition.namespace)
        with transaction.atomic(using=using):
            with catalogue_transaction_lock(exclusive=True, using=using):
                principal = authorize_locked(actor, CustomFieldChoiceSet, "add_customfieldchoiceset", using=using)
                choice_set = CustomFieldChoiceSet(
                    namespace=definition.namespace,
                    slug=definition.slug,
                    label=definition.label,
                    management_kind=CustomFieldChoiceSet.MANAGEMENT_LOCAL,
                    lifecycle=CustomFieldChoiceSet.LIFECYCLE_ACTIVE,
                    version=1,
                )
                if definition.replaced_by is not None:
                    resolve_replacement(CustomFieldChoiceSet, None, definition.replaced_by, using=using)
                    choice_set.replaced_by = definition.replaced_by
                choice_set.full_clean()
                save_definition(choice_set, principal, using=using)
            return success(choice_set, "created", using=using)
    except DefinitionCommandError as error:
        return close_command_error(choice_set, error)
    except ValidationError as error:
        return close_validation_error(choice_set, error)
    except IntegrityError as error:
        return close_integrity_error(choice_set, error)


def update_custom_field_choice_set(
    *,
    actor: ActorContextDTO,
    choice_set_id: int,
    expected_resource_revision: ResourceRevision,
    changes: CustomFieldChoiceSetUpdateInputDTO,
    using: str = DEFAULT_DB_ALIAS,
):
    _require_input(changes, CustomFieldChoiceSetUpdateInputDTO, "changes")
    choice_set: CustomFieldChoiceSet | None = None
    try:
        choice_set_id = require_positive_id(choice_set_id, "choice_set_id")
        expected = require_revision(expected_resource_revision)
        with transaction.atomic(using=using):
            with catalogue_transaction_lock(exclusive=True, using=using):
                choice_set = lock_one(CustomFieldChoiceSet, choice_set_id, using=using)
                principal = authorize_locked(actor, CustomFieldChoiceSet, "change_customfieldchoiceset", using=using)
                validate_local_definition(choice_set)
                actual = resource_revision_for_definition(choice_set, using=using)
                if actual != expected:
                    return reject_for(choice_set, issue("STALE_RESOURCE", message_key="specifications.stale_resource"))
                if not _apply_choice_set_changes(choice_set, changes, using=using):
                    return success(choice_set, "no_op", using=using)
                choice_set.version += 1
                choice_set.full_clean()
                save_definition(choice_set, principal, using=using)
            return success(choice_set, "changed", using=using)
    except DefinitionCommandError as error:
        return close_command_error(choice_set, error)
    except ValidationError as error:
        return close_validation_error(choice_set, error)
    except IntegrityError as error:
        return close_integrity_error(choice_set, error)


def deprecate_custom_field_choice_set(
    *,
    actor: ActorContextDTO,
    choice_set_id: int,
    expected_resource_revision: ResourceRevision,
    replacement_identity: QualifiedIdentity | None = None,
    using: str = DEFAULT_DB_ALIAS,
):
    choice_set: CustomFieldChoiceSet | None = None
    try:
        choice_set_id = require_positive_id(choice_set_id, "choice_set_id")
        expected = require_revision(expected_resource_revision)
        with transaction.atomic(using=using):
            with catalogue_transaction_lock(exclusive=True, using=using):
                choice_set = lock_one(CustomFieldChoiceSet, choice_set_id, using=using)
                principal = authorize_locked(actor, CustomFieldChoiceSet, "change_customfieldchoiceset", using=using)
                validate_local_definition(choice_set)
                actual = resource_revision_for_definition(choice_set, using=using)
                if actual != expected:
                    return reject_for(choice_set, issue("STALE_RESOURCE", message_key="specifications.stale_resource"))
                if choice_set.lifecycle == CustomFieldChoiceSet.LIFECYCLE_DEPRECATED:
                    if replacement_identity in {None, choice_set.replaced_by}:
                        return success(choice_set, "no_op", using=using)
                    return reject_for(choice_set, issue("IMMUTABLE_DEFINITION"))
                dependent_ids = list(
                    CustomField.objects.using(using)
                    .filter(choice_set_id=choice_set.pk, lifecycle=CustomField.LIFECYCLE_ACTIVE)
                    .order_by("pk")
                    .values_list("pk", flat=True)
                )
                lock_rows(CustomField, dependent_ids, using=using)
                if dependent_ids:
                    raise DefinitionCommandError(issue("DEPENDENCY_RETIREMENT", path=("choice_set",)))
                if replacement_identity is not None:
                    resolve_replacement(CustomFieldChoiceSet, choice_set, replacement_identity, using=using)
                choice_set.lifecycle = CustomFieldChoiceSet.LIFECYCLE_DEPRECATED
                choice_set.deprecated_at = timezone.now()
                choice_set.replaced_by = replacement_identity
                choice_set.version += 1
                choice_set.full_clean()
                save_definition(choice_set, principal, using=using)
            return success(choice_set, "changed", using=using)
    except DefinitionCommandError as error:
        return close_command_error(choice_set, error)
    except ValidationError as error:
        return close_validation_error(choice_set, error)
    except IntegrityError as error:
        return close_integrity_error(choice_set, error)


def _choice_replacement_target(
    choice_set: CustomFieldChoiceSet,
    source: CustomFieldChoice | None,
    replacement: QualifiedIdentity | None,
    *,
    using: str,
):
    return resolve_choice_replacement(choice_set, source, replacement, using=using)


def create_custom_field_choice(
    *,
    actor: ActorContextDTO,
    definition: CustomFieldChoiceCreateInputDTO,
    using: str = DEFAULT_DB_ALIAS,
):
    _require_input(definition, CustomFieldChoiceCreateInputDTO, "definition")
    choice: CustomFieldChoice | None = None
    try:
        choice_set_id = require_positive_id(definition.choice_set_id, "choice_set_id")
        with transaction.atomic(using=using):
            with catalogue_transaction_lock(exclusive=True, using=using):
                choice_set_rows = lock_rows(CustomFieldChoiceSet, (choice_set_id,), using=using)
                if not choice_set_rows:
                    raise DefinitionCommandError(
                        issue("OBJECT_UNAVAILABLE", message_key="specifications.object_unavailable")
                    )
                choice_set = choice_set_rows[0]
                principal = authorize_locked(actor, CustomFieldChoice, "add_customfieldchoice", using=using)
                validate_local_definition(choice_set)
                if choice_set.lifecycle != CustomFieldChoiceSet.LIFECYCLE_ACTIVE:
                    raise DefinitionCommandError(issue("REFERENCE_CONFLICT", path=("choice_set_id",)))
                choice = CustomFieldChoice(
                    choice_set=choice_set,
                    key=definition.key,
                    label=definition.label,
                    position=definition.position,
                    lifecycle=CustomFieldChoice.LIFECYCLE_ACTIVE,
                    version=1,
                    replaced_by=definition.replaced_by,
                )
                if definition.replaced_by is not None:
                    _choice_replacement_target(choice_set, None, definition.replaced_by, using=using)
                choice.full_clean()
                save_definition(choice, principal, using=using)
            return success(choice, "created", using=using)
    except DefinitionCommandError as error:
        return close_command_error(choice, error)
    except ValidationError as error:
        return close_validation_error(choice, error)
    except IntegrityError as error:
        return close_integrity_error(choice, error)


def update_custom_field_choice(
    *,
    actor: ActorContextDTO,
    choice_id: int,
    expected_resource_revision: ResourceRevision,
    changes: CustomFieldChoiceUpdateInputDTO,
    using: str = DEFAULT_DB_ALIAS,
):
    _require_input(changes, CustomFieldChoiceUpdateInputDTO, "changes")
    choice: CustomFieldChoice | None = None
    try:
        choice_id = require_positive_id(choice_id, "choice_id")
        expected = require_revision(expected_resource_revision)
        with transaction.atomic(using=using):
            with catalogue_transaction_lock(exclusive=True, using=using):
                choice_set, choice = lock_choice_dependencies(choice_id, using=using)
                principal = authorize_locked(actor, CustomFieldChoice, "change_customfieldchoice", using=using)
                validate_local_definition(choice_set)
                actual = resource_revision_for_definition(choice, using=using)
                if actual != expected:
                    return reject_for(choice, issue("STALE_RESOURCE", message_key="specifications.stale_resource"))
                changed = False
                if changes.label is not None and choice.label != changes.label:
                    choice.label = changes.label
                    changed = True
                if changes.position is not None and choice.position != changes.position:
                    choice.position = changes.position
                    changed = True
                if changes.replaced_by is not None and choice.replaced_by != changes.replaced_by:
                    _choice_replacement_target(choice_set, choice, changes.replaced_by, using=using)
                    choice.replaced_by = changes.replaced_by
                    changed = True
                if not changed:
                    return success(choice, "no_op", using=using)
                choice.version += 1
                choice.full_clean(validate_unique=False)
                save_definition(choice, principal, using=using)
            return success(choice, "changed", using=using)
    except DefinitionCommandError as error:
        return close_command_error(choice, error)
    except ValidationError as error:
        return close_validation_error(choice, error)
    except IntegrityError as error:
        return close_integrity_error(choice, error)


def deprecate_custom_field_choice(
    *,
    actor: ActorContextDTO,
    choice_id: int,
    expected_resource_revision: ResourceRevision,
    replacement_identity: QualifiedIdentity | None = None,
    using: str = DEFAULT_DB_ALIAS,
):
    choice: CustomFieldChoice | None = None
    try:
        choice_id = require_positive_id(choice_id, "choice_id")
        expected = require_revision(expected_resource_revision)
        with transaction.atomic(using=using):
            with catalogue_transaction_lock(exclusive=True, using=using):
                choice_set, choice = lock_choice_dependencies(choice_id, using=using)
                principal = authorize_locked(actor, CustomFieldChoice, "change_customfieldchoice", using=using)
                validate_local_definition(choice_set)
                actual = resource_revision_for_definition(choice, using=using)
                if actual != expected:
                    return reject_for(choice, issue("STALE_RESOURCE", message_key="specifications.stale_resource"))
                if choice.lifecycle == CustomFieldChoice.LIFECYCLE_DEPRECATED:
                    if replacement_identity in {None, choice.replaced_by}:
                        return success(choice, "no_op", using=using)
                    return reject_for(choice, issue("IMMUTABLE_DEFINITION"))
                if replacement_identity is not None:
                    _choice_replacement_target(choice_set, choice, replacement_identity, using=using)
                choice.lifecycle = CustomFieldChoice.LIFECYCLE_DEPRECATED
                choice.deprecated_at = timezone.now()
                choice.replaced_by = replacement_identity
                choice.version += 1
                choice.full_clean()
                save_definition(choice, principal, using=using)
            return success(choice, "changed", using=using)
    except DefinitionCommandError as error:
        return close_command_error(choice, error)
    except ValidationError as error:
        return close_validation_error(choice, error)
    except IntegrityError as error:
        return close_integrity_error(choice, error)


__all__ = [
    "create_custom_field",
    "create_custom_field_choice",
    "create_custom_field_choice_set",
    "create_custom_fieldset",
    "deprecate_custom_field",
    "deprecate_custom_field_choice",
    "deprecate_custom_field_choice_set",
    "deprecate_custom_fieldset",
    "replace_custom_fieldset_memberships",
    "update_custom_field",
    "update_custom_field_choice",
    "update_custom_field_choice_set",
    "update_custom_fieldset",
]
