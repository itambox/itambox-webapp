"""PostgreSQL graph-loader contract and measured query-bound regressions."""

import re

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext

from assets.models import Asset, AssetType, Manufacturer
from assets.models.catalog import AssetTypeFieldset
from assets.services.specifications import loader
from assets.services.specifications.contracts import SpecificationGraphLoadRequest
from extras.models import CustomField, CustomFieldChoice, CustomFieldChoiceSet, CustomFieldset, CustomFieldsetField

pytestmark = pytest.mark.django_db


def _field(name, *, activation, model=AssetType, lifecycle="active"):
    field = CustomField.objects.create(
        namespace="local",
        name=name,
        label=name,
        field_type=CustomField.FIELD_TYPE_TEXT,
        activation=activation,
        lifecycle=lifecycle,
    )
    field.object_types.add(ContentType.objects.get_for_model(model))
    return field


def _load(type_ids, history=()):
    return loader.load_specification_graph(
        SpecificationGraphLoadRequest(
            asset_type_ids=tuple(type_ids),
            requested_target_kinds=frozenset({"asset_type", "asset"}),
            requested_field_keys=frozenset(history),
        )
    )


def test_real_orm_loads_globals_composition_and_history_without_owner_reads():
    manufacturer = Manufacturer.objects.create(name="Loader Probe", slug="loader-probe")
    owner = AssetType.objects.create(manufacturer=manufacturer, model="Probe", slug="loader-probe")
    group = CustomFieldset.objects.create(namespace="local", slug="loader-probe", label="Probe")
    field = _field("loader_probe_field", activation=CustomField.ACTIVATION_COMPOSED)
    CustomFieldsetField.objects.create(fieldset=group, custom_field=field, position=1)
    AssetTypeFieldset.objects.create(asset_type=owner, fieldset=group, position=1)
    global_field = _field("loader_probe_global", activation=CustomField.ACTIVATION_GLOBAL, model=Asset)
    hidden = _field("loader_probe_history", activation=CustomField.ACTIVATION_COMPOSED, lifecycle="deprecated")

    with CaptureQueriesContext(connection) as queries:
        graph = _load([owner.pk, owner.pk], [hidden.name, "loader_probe_unknown"])
    assert tuple(graph.type_memberships) == (owner.pk,)
    assert tuple(graph.fields_by_key) == (field.name, global_field.name)
    assert graph.fields_by_key[field.name].targets == frozenset({"asset_type"})
    assert graph.global_field_keys_by_target["asset"] == (global_field.name,)
    assert graph.global_field_keys_by_target["asset_type"] == ()
    assert graph.historical_definitions_by_key[hidden.name].lifecycle == "deprecated"
    assert "loader_probe_unknown" not in graph.historical_definitions_by_key
    assert hidden.name not in graph.fields_by_key
    for query in queries:
        assert not re.search(r'FROM\s+"(?:assets_asset|assets_assettype|organization_tenant)"', query["sql"])
    with pytest.raises(TypeError):
        graph.fields_by_key["injected"] = graph.fields_by_key[field.name]

    before = graph.fields_by_key[field.name].resource_revision
    AssetType.all_objects.filter(pk=owner.pk).update(custom_field_data={field.name: "stored observation"})
    assert _load([owner.pk]).fields_by_key[field.name].resource_revision == before
    CustomField.objects.filter(pk=field.pk).update(label="Changed label")
    assert _load([owner.pk]).fields_by_key[field.name].resource_revision != before
    print("REAL_ORM_QUERIES", len(queries))


def test_real_query_count_is_flat_within_chunk_and_bounded_across_chunks():
    manufacturer = Manufacturer.objects.create(name="Loader Batch", slug="loader-batch")
    group = CustomFieldset.objects.create(namespace="local", slug="loader-batch", label="Batch")
    field = _field("loader_batch_field", activation=CustomField.ACTIVATION_COMPOSED)
    CustomFieldsetField.objects.create(fieldset=group, custom_field=field, position=1)
    types = [
        AssetType.objects.create(manufacturer=manufacturer, model=f"Batch {index}", slug=f"loader-batch-{index}")
        for index in range(loader._BATCH_SIZE + 5)
    ]
    for owner in types:
        AssetTypeFieldset.objects.create(asset_type=owner, fieldset=group, position=1)
    counts = []
    for owners in (types[:1], types[:30], types[:30] * 4, types):
        with CaptureQueriesContext(connection) as queries:
            graph = _load([owner.pk for owner in owners])
        assert len(graph.type_memberships) == len({owner.pk for owner in owners})
        assert tuple(graph.fields_by_key) == (field.name,)
        counts.append(len(queries))
    assert counts[0] == counts[1] == counts[2]
    assert counts[3] <= counts[0] + 3
    print("REAL_CHUNK_QUERY_COUNTS", counts)


def test_real_multiple_fieldsets_share_choices_without_per_field_queries():
    manufacturer = Manufacturer.objects.create(name="Loader Choices", slug="loader-choices")
    owner = AssetType.objects.create(manufacturer=manufacturer, model="Choices", slug="loader-choices")
    choices = CustomFieldChoiceSet.objects.create(namespace="local", slug="loader-choices", label="Choices")
    option = CustomFieldChoice.objects.create(choice_set=choices, key="one", label="One", position=1)
    counts = []
    field_names = []
    for index in range(12):
        group = CustomFieldset.objects.create(namespace="local", slug=f"choice-group-{index}", label=f"Group {index}")
        field = CustomField.objects.create(
            namespace="local",
            name=f"loader_choice_{index}",
            label=f"Choice {index}",
            field_type=CustomField.FIELD_TYPE_SINGLE_SELECT,
            activation=CustomField.ACTIVATION_COMPOSED,
            choice_set=choices,
            max_values=1,
        )
        field.object_types.add(ContentType.objects.get_for_model(AssetType))
        field_names.append(field.name)
        CustomFieldsetField.objects.create(fieldset=group, custom_field=field, position=1)
        AssetTypeFieldset.objects.create(asset_type=owner, fieldset=group, position=index + 1)
        if index in (0, 11):
            with CaptureQueriesContext(connection) as queries:
                graph = _load([owner.pk])
            counts.append(len(queries))
    assert counts[0] == counts[1]
    first_choices = graph.fields_by_key[field_names[0]].choice_set
    assert all(graph.fields_by_key[name].choice_set is first_choices for name in field_names)
    before = graph.fieldsets_by_identity["local/choice-group-0"].resource_revision
    CustomFieldChoice.objects.filter(pk=option.pk).update(label="Changed option")
    later = _load([owner.pk])
    assert later.fieldsets_by_identity["local/choice-group-0"].resource_revision != before
    assert later.fields_by_key[field_names[0]].choice_set.choices[0].label == "Changed option"
    print("REAL_FIELDSET_QUERY_COUNTS", counts)
