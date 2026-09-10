"""Definition input isolation, nondisclosure, and positive command regressions."""

from dataclasses import replace

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission

from core.models import ObjectChange
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset
from extras.services import definition_commands as commands
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
)
from organization.services.access_scope import ActorContextDTO, authentication_revision_for_actor


def _field_input(**changes):
    return replace(
        CustomFieldCreateInputDTO(
            namespace="boundary",
            local_key="observed",
            label="Observed",
            object_types=("assets.assettype",),
        ),
        **changes,
    )


@pytest.mark.parametrize("updating", [False, True])
def test_mapping_input_is_recursively_detached_and_immutable(updating):
    original = {"values": [True, {"nested": ["original"]}]}
    dto = CustomFieldUpdateInputDTO(mappings=(original,)) if updating else _field_input(mappings=(original,))
    original["values"][1]["nested"][0] = "changed"
    assert dto.mappings[0]["values"][1]["nested"][0] == "original"
    with pytest.raises(TypeError):
        dto.mappings[0]["values"][1]["nested"][0] = "changed"
    with pytest.raises(TypeError):
        dto.mappings[0]["replacement"] = "changed"


@pytest.fixture
def definition_objects(db):
    user = get_user_model().objects.create_user(username="definition-boundary-editor")
    user.user_permissions.add(*Permission.objects.filter(content_type__app_label="extras"))
    actor = ActorContextDTO(actor_id=user.pk, authentication_revision=authentication_revision_for_actor(user))
    field = commands.create_custom_field(actor=actor, definition=_field_input())
    fieldset = commands.create_custom_fieldset(
        actor=actor,
        definition=CustomFieldsetCreateInputDTO(namespace="boundary", slug="section", label="Section"),
    )
    choice_set = commands.create_custom_field_choice_set(
        actor=actor,
        definition=CustomFieldChoiceSetCreateInputDTO(namespace="boundary", slug="choices", label="Choices"),
    )
    choice = commands.create_custom_field_choice(
        actor=actor,
        definition=CustomFieldChoiceCreateInputDTO(
            choice_set_id=choice_set.definition_id, key="one", label="One", position=1
        ),
    )
    results = {"field": field, "fieldset": fieldset, "choice_set": choice_set, "choice": choice}
    assert all(result.outcome == "created" for result in results.values())
    return user, actor, results


_FAMILIES = {
    "field": (CustomField, "custom_field", "field_id", CustomFieldUpdateInputDTO),
    "fieldset": (CustomFieldset, "custom_fieldset", "fieldset_id", CustomFieldsetUpdateInputDTO),
    "choice_set": (
        CustomFieldChoiceSet,
        "custom_field_choice_set",
        "choice_set_id",
        CustomFieldChoiceSetUpdateInputDTO,
    ),
    "choice": (CustomFieldChoice, "custom_field_choice", "choice_id", CustomFieldChoiceUpdateInputDTO),
}


@pytest.mark.parametrize("kind", tuple(_FAMILIES))
@pytest.mark.parametrize("operation", ["update", "deprecate"])
def test_denied_existing_and_missing_definition_are_indistinguishable(definition_objects, kind, operation):
    user, actor, objects = definition_objects
    model, suffix, id_name, changes_type = _FAMILIES[kind]
    created = objects[kind]
    row = model.objects.get(pk=created.definition_id)
    before_timestamp = row.updated_at
    before_changes = ObjectChange._base_manager.count()
    user.user_permissions.clear()
    command = getattr(commands, f"{operation}_{suffix}")
    kwargs = {"actor": actor, id_name: row.pk, "expected_resource_revision": created.resource_revision}
    if operation == "update":
        kwargs["changes"] = changes_type(label="Forbidden")
    existing = command(**kwargs)
    kwargs[id_name] = row.pk + 1000000
    missing = command(**kwargs)
    assert existing == missing
    assert existing.outcome == "rejected"
    assert [issue.code for issue in existing.issues] == ["OBJECT_UNAVAILABLE"]
    assert existing.definition_id is None
    assert existing.identity is None
    assert existing.definition_kind is None
    row.refresh_from_db()
    assert row.updated_at == before_timestamp
    assert ObjectChange._base_manager.count() == before_changes


@pytest.mark.parametrize("kind", tuple(_FAMILIES))
def test_authorized_update_has_a_positive_path_and_true_no_op(definition_objects, kind):
    _user, actor, objects = definition_objects
    model, suffix, id_name, changes_type = _FAMILIES[kind]
    created = objects[kind]
    command = getattr(commands, f"update_{suffix}")
    kwargs = {
        "actor": actor,
        id_name: created.definition_id,
        "expected_resource_revision": resource_revision_for_definition(model.objects.get(pk=created.definition_id)),
        "changes": changes_type(label="Relabelled"),
    }
    changed = command(**kwargs)
    assert changed.outcome == "changed", changed
    row = model.objects.get(pk=created.definition_id)
    assert row.label == "Relabelled"
    timestamp = row.updated_at
    audit_count = ObjectChange._base_manager.count()
    kwargs["expected_resource_revision"] = changed.resource_revision
    repeated = command(**kwargs)
    assert repeated.outcome == "no_op", repeated
    row.refresh_from_db()
    assert row.updated_at == timestamp
    assert ObjectChange._base_manager.count() == audit_count


def test_fieldset_retirement_has_a_positive_path(definition_objects):
    _user, actor, objects = definition_objects
    created = objects["fieldset"]
    retired = commands.deprecate_custom_fieldset(
        actor=actor, fieldset_id=created.definition_id, expected_resource_revision=created.resource_revision
    )
    assert retired.outcome == "changed", retired
    assert CustomFieldset.objects.get(pk=created.definition_id).lifecycle == "deprecated"


def test_mapping_storage_preserves_json_types_and_input_snapshot(definition_objects):
    _user, actor, objects = definition_objects
    created = objects["field"]
    original = {"values": [True]}
    change = CustomFieldUpdateInputDTO(mappings=(original,))
    original["values"][0] = False
    saved = commands.update_custom_field(
        actor=actor,
        field_id=created.definition_id,
        expected_resource_revision=created.resource_revision,
        changes=change,
    )
    assert saved.outcome == "changed", saved
    row = CustomField.objects.get(pk=created.definition_id)
    assert row.mappings[0]["values"][0] is True
    changed = commands.update_custom_field(
        actor=actor,
        field_id=row.pk,
        expected_resource_revision=saved.resource_revision,
        changes=CustomFieldUpdateInputDTO(mappings=({"values": [1]},)),
    )
    assert changed.outcome == "changed", changed
    row.refresh_from_db()
    assert type(row.mappings[0]["values"][0]) is int
