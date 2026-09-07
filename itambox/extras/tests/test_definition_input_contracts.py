"""DB-free constructor contracts for definition input DTOs."""

from decimal import Decimal
from types import MappingProxyType

import pytest

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


def _field_create_input(**overrides):
    values = {
        "namespace": "boundary",
        "local_key": "observed",
        "label": "Observed",
        "object_types": ("assets.assettype", "inventory.item"),
        "field_type": "decimal",
        "activation": "composed",
        "help_text": "Measured value",
        "quantity_kind": "capacity",
        "canonical_unit": "GiB",
        "minimum_value": Decimal("0.5"),
        "maximum_value": Decimal("10.5"),
        "regex": r"^\d+(\.\d+)?$",
        "decimal_scale": 2,
        "max_values": 3,
        "text_max_length": 64,
        "validation_rule": "positive",
        "required": True,
        "nullable": True,
        "mappings": (),
        "choice_set_id": 7,
        "replaced_by": "boundary.replacement",
    }
    values.update(overrides)
    return CustomFieldCreateInputDTO(**values)


def _field_update_input(**overrides):
    values = {
        "label": "Updated",
        "help_text": "Updated help",
        "activation": "global",
        "required": False,
        "mappings": (),
        "object_types": ("assets.assettype",),
        "replaced_by": "boundary.replacement",
    }
    values.update(overrides)
    return CustomFieldUpdateInputDTO(**values)


def _fieldset_create_input(**overrides):
    values = {
        "namespace": "boundary",
        "slug": "details",
        "label": "Details",
        "description": "Definition details",
        "field_identities": ("boundary.first", "boundary.second"),
        "replaced_by": "boundary.replacement",
    }
    values.update(overrides)
    return CustomFieldsetCreateInputDTO(**values)


def _fieldset_update_input(**overrides):
    values = {"label": "Updated details", "description": "Updated description", "replaced_by": "boundary.replacement"}
    values.update(overrides)
    return CustomFieldsetUpdateInputDTO(**values)


def _choice_set_create_input(**overrides):
    values = {
        "namespace": "boundary",
        "slug": "states",
        "label": "States",
        "replaced_by": "boundary.replacement",
    }
    values.update(overrides)
    return CustomFieldChoiceSetCreateInputDTO(**values)


def _choice_set_update_input(**overrides):
    values = {"label": "Updated states", "replaced_by": "boundary.replacement"}
    values.update(overrides)
    return CustomFieldChoiceSetUpdateInputDTO(**values)


def _choice_create_input(**overrides):
    values = {
        "choice_set_id": 7,
        "key": "active",
        "label": "Active",
        "position": 2,
        "replaced_by": "boundary.replacement",
    }
    values.update(overrides)
    return CustomFieldChoiceCreateInputDTO(**values)


def _choice_update_input(**overrides):
    values = {"label": "Updated active", "position": 3, "replaced_by": "boundary.replacement"}
    values.update(overrides)
    return CustomFieldChoiceUpdateInputDTO(**values)


def test_custom_field_create_round_trips_accepted_scalars_and_tuples():
    dto = _field_create_input()

    assert dto.namespace == "boundary"
    assert dto.local_key == "observed"
    assert dto.label == "Observed"
    assert dto.object_types == ("assets.assettype", "inventory.item")
    assert dto.field_type == "decimal"
    assert dto.activation == "composed"
    assert dto.help_text == "Measured value"
    assert dto.quantity_kind == "capacity"
    assert dto.canonical_unit == "GiB"
    assert dto.minimum_value == Decimal("0.5")
    assert dto.maximum_value == Decimal("10.5")
    assert dto.regex == r"^\d+(\.\d+)?$"
    assert dto.decimal_scale == 2
    assert dto.max_values == 3
    assert dto.text_max_length == 64
    assert dto.validation_rule == "positive"
    assert dto.required is True
    assert dto.nullable is True
    assert type(dto.required) is bool
    assert type(dto.nullable) is bool
    assert dto.mappings == ()
    assert dto.choice_set_id == 7
    assert type(dto.choice_set_id) is int
    assert dto.replaced_by == "boundary.replacement"


def test_custom_field_update_round_trips_accepted_scalars_and_tuples():
    dto = _field_update_input()

    assert dto.label == "Updated"
    assert dto.help_text == "Updated help"
    assert dto.activation == "global"
    assert dto.required is False
    assert type(dto.required) is bool
    assert dto.mappings == ()
    assert dto.object_types == ("assets.assettype",)
    assert dto.replaced_by == "boundary.replacement"


def test_custom_fieldset_create_round_trips_strings_and_identity_tuple():
    dto = _fieldset_create_input()

    assert dto.namespace == "boundary"
    assert dto.slug == "details"
    assert dto.label == "Details"
    assert dto.description == "Definition details"
    assert dto.field_identities == ("boundary.first", "boundary.second")
    assert dto.replaced_by == "boundary.replacement"


def test_custom_fieldset_update_round_trips_optional_strings():
    dto = _fieldset_update_input()

    assert dto.label == "Updated details"
    assert dto.description == "Updated description"
    assert dto.replaced_by == "boundary.replacement"


def test_choice_set_create_round_trips_strings():
    dto = _choice_set_create_input()

    assert dto.namespace == "boundary"
    assert dto.slug == "states"
    assert dto.label == "States"
    assert dto.replaced_by == "boundary.replacement"


def test_choice_set_update_round_trips_optional_string():
    dto = _choice_set_update_input()

    assert dto.label == "Updated states"
    assert dto.replaced_by == "boundary.replacement"


def test_choice_create_round_trips_positive_ids_and_strings():
    dto = _choice_create_input()

    assert dto.choice_set_id == 7
    assert type(dto.choice_set_id) is int
    assert dto.key == "active"
    assert dto.label == "Active"
    assert dto.position == 2
    assert type(dto.position) is int
    assert dto.replaced_by == "boundary.replacement"


def test_choice_update_round_trips_optional_string_and_positive_position():
    dto = _choice_update_input()

    assert dto.label == "Updated active"
    assert dto.position == 3
    assert type(dto.position) is int
    assert dto.replaced_by == "boundary.replacement"


@pytest.mark.parametrize(
    ("overrides", "expected_exception"),
    [
        pytest.param({"namespace": None}, TypeError, id="namespace-type"),
        pytest.param({"namespace": ""}, ValueError, id="namespace-empty"),
        pytest.param({"local_key": 1}, TypeError, id="local-key-type"),
        pytest.param({"local_key": ""}, ValueError, id="local-key-empty"),
        pytest.param({"label": None}, TypeError, id="label-type"),
        pytest.param({"help_text": None}, TypeError, id="help-text-type"),
        pytest.param({"object_types": ["assets.assettype"]}, TypeError, id="object-types-container"),
        pytest.param({"object_types": (1,)}, ValueError, id="object-types-item-type"),
        pytest.param({"object_types": ("",)}, ValueError, id="object-types-empty-item"),
        pytest.param({"required": 1}, TypeError, id="required-integer"),
        pytest.param({"nullable": 0}, TypeError, id="nullable-integer"),
        pytest.param({"mappings": []}, TypeError, id="mappings-container"),
        pytest.param({"choice_set_id": 0}, ValueError, id="choice-set-id-zero"),
        pytest.param({"choice_set_id": True}, ValueError, id="choice-set-id-bool"),
    ],
)
def test_custom_field_create_rejects_invalid_constructor_values(overrides, expected_exception):
    with pytest.raises(expected_exception):
        _field_create_input(**overrides)


@pytest.mark.parametrize(
    ("overrides", "expected_exception"),
    [
        pytest.param({"label": 1}, TypeError, id="label-type"),
        pytest.param({"help_text": []}, TypeError, id="help-text-type"),
        pytest.param({"required": 0}, TypeError, id="required-integer"),
        pytest.param({"mappings": [True]}, TypeError, id="mappings-container"),
        pytest.param({"object_types": ["assets.assettype"]}, TypeError, id="object-types-container"),
        pytest.param({"object_types": (None,)}, ValueError, id="object-types-item-type"),
        pytest.param({"object_types": ("",)}, ValueError, id="object-types-empty-item"),
    ],
)
def test_custom_field_update_rejects_invalid_constructor_values(overrides, expected_exception):
    with pytest.raises(expected_exception):
        _field_update_input(**overrides)


@pytest.mark.parametrize(
    ("overrides", "expected_exception"),
    [
        pytest.param({"namespace": ""}, ValueError, id="namespace-empty"),
        pytest.param({"slug": ""}, ValueError, id="slug-empty"),
        pytest.param({"label": None}, TypeError, id="label-type"),
        pytest.param({"description": 1}, TypeError, id="description-type"),
        pytest.param({"field_identities": ["boundary.first"]}, TypeError, id="identities-container"),
        pytest.param({"field_identities": ("",)}, ValueError, id="identity-empty"),
        pytest.param({"field_identities": ("boundary.first", "boundary.first")}, ValueError, id="identity-duplicate"),
    ],
)
def test_custom_fieldset_create_rejects_invalid_constructor_values(overrides, expected_exception):
    with pytest.raises(expected_exception):
        _fieldset_create_input(**overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"label": 1}, id="label-type"),
        pytest.param({"description": []}, id="description-type"),
    ],
)
def test_custom_fieldset_update_rejects_non_string_values(overrides):
    with pytest.raises(TypeError):
        _fieldset_update_input(**overrides)


@pytest.mark.parametrize(
    ("overrides", "expected_exception"),
    [
        pytest.param({"namespace": ""}, ValueError, id="namespace-empty"),
        pytest.param({"slug": ""}, ValueError, id="slug-empty"),
        pytest.param({"label": None}, TypeError, id="label-type"),
    ],
)
def test_choice_set_create_rejects_invalid_constructor_values(overrides, expected_exception):
    with pytest.raises(expected_exception):
        _choice_set_create_input(**overrides)


def test_choice_set_update_rejects_non_string_label():
    with pytest.raises(TypeError):
        _choice_set_update_input(label=1)


@pytest.mark.parametrize(
    ("overrides", "expected_exception"),
    [
        pytest.param({"choice_set_id": 0}, ValueError, id="choice-set-id-zero"),
        pytest.param({"choice_set_id": -1}, ValueError, id="choice-set-id-negative"),
        pytest.param({"choice_set_id": True}, ValueError, id="choice-set-id-bool"),
        pytest.param({"key": ""}, ValueError, id="key-empty"),
        pytest.param({"key": None}, TypeError, id="key-type"),
        pytest.param({"label": 1}, TypeError, id="label-type"),
        pytest.param({"position": 0}, ValueError, id="position-zero"),
        pytest.param({"position": False}, ValueError, id="position-bool"),
    ],
)
def test_choice_create_rejects_invalid_constructor_values(overrides, expected_exception):
    with pytest.raises(expected_exception):
        _choice_create_input(**overrides)


@pytest.mark.parametrize(
    ("overrides", "expected_exception"),
    [
        pytest.param({"label": 1}, TypeError, id="label-type"),
        pytest.param({"position": 0}, ValueError, id="position-zero"),
        pytest.param({"position": True}, ValueError, id="position-bool"),
    ],
)
def test_choice_update_rejects_invalid_constructor_values(overrides, expected_exception):
    with pytest.raises(expected_exception):
        _choice_update_input(**overrides)


def test_create_tuple_defaults_and_explicit_empty_tuples_are_supported():
    field_required = {
        "namespace": "boundary",
        "local_key": "observed",
        "label": "Observed",
        "object_types": ("assets.assettype",),
    }
    omitted_field = CustomFieldCreateInputDTO(**field_required)
    explicit_empty_field = CustomFieldCreateInputDTO(**field_required, mappings=())
    assert omitted_field.mappings == ()
    assert explicit_empty_field.mappings == ()

    omitted_fieldset = CustomFieldsetCreateInputDTO(namespace="boundary", slug="details")
    explicit_empty_fieldset = CustomFieldsetCreateInputDTO(namespace="boundary", slug="details", field_identities=())
    assert omitted_fieldset.field_identities == ()
    assert explicit_empty_fieldset.field_identities == ()


def test_update_optional_tuples_preserve_omission_as_none_vs_empty_tuple():
    omitted = CustomFieldUpdateInputDTO()
    empty_mappings = CustomFieldUpdateInputDTO(mappings=())
    empty_object_types = CustomFieldUpdateInputDTO(object_types=())

    assert omitted.mappings is None
    assert omitted.object_types is None
    assert empty_mappings.mappings == ()
    assert empty_mappings.object_types is None
    assert empty_object_types.mappings is None
    assert empty_object_types.object_types == ()


def test_update_optional_scalars_preserve_omission_as_none():
    assert CustomFieldUpdateInputDTO().label is None
    assert CustomFieldUpdateInputDTO().help_text is None
    assert CustomFieldUpdateInputDTO().required is None
    assert CustomFieldsetUpdateInputDTO().label is None
    assert CustomFieldsetUpdateInputDTO().description is None
    assert CustomFieldChoiceSetUpdateInputDTO().label is None
    assert CustomFieldChoiceUpdateInputDTO().label is None
    assert CustomFieldChoiceUpdateInputDTO().position is None


@pytest.mark.parametrize("updating", [False, True], ids=["create", "update"])
def test_mapping_values_are_detached_and_deeply_immutable(updating):
    source = {"values": [True, {"nested": ["original"]}], "metadata": {"count": 1}}
    if updating:
        dto = CustomFieldUpdateInputDTO(mappings=(source,))
    else:
        dto = _field_create_input(mappings=(source,))

    source["values"].append(False)
    source["values"][1]["nested"].append("changed")
    source["metadata"]["count"] = 99

    mapping = dto.mappings[0]
    assert type(mapping) is MappingProxyType
    assert type(mapping["values"]) is tuple
    assert mapping["values"][0] is True
    assert type(mapping["values"][0]) is bool
    assert type(mapping["values"][1]) is MappingProxyType
    assert type(mapping["values"][1]["nested"]) is tuple
    assert mapping["values"][1]["nested"] == ("original",)
    assert mapping["metadata"]["count"] == 1
    assert type(mapping["metadata"]["count"]) is int

    with pytest.raises(TypeError):
        mapping["replacement"] = "changed"
    with pytest.raises(TypeError):
        mapping["metadata"]["count"] = 2
    with pytest.raises(TypeError):
        mapping["values"][0] = False
    with pytest.raises(TypeError):
        mapping["values"][1]["nested"][0] = "changed"


@pytest.mark.parametrize(
    "bad_value", [pytest.param({"not-json"}, id="set"), pytest.param(bytearray(b"not-json"), id="bytearray")]
)
@pytest.mark.parametrize("updating", [False, True], ids=["create", "update"])
def test_mapping_rejects_non_json_mutable_values(bad_value, updating):
    source = {"payload": bad_value}
    with pytest.raises(TypeError):
        if updating:
            CustomFieldUpdateInputDTO(mappings=(source,))
        else:
            _field_create_input(mappings=(source,))


@pytest.mark.parametrize("updating", [False, True], ids=["create", "update"])
def test_mapping_rejects_non_string_keys_recursively(updating):
    source = {"nested": {1: "invalid key"}}
    with pytest.raises(TypeError):
        if updating:
            CustomFieldUpdateInputDTO(mappings=(source,))
        else:
            _field_create_input(mappings=(source,))
