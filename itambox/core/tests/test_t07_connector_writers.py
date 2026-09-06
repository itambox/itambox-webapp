"""DB-free contract tests for the T07 connector writer seam."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.importers.snipeit.stages.asset_models import (
    _OMITTED_FIELDSET,
    AssetModelDependencies,
    AssetModelImporter,
    _connector_identity,
    _snipeit_identity,
)
from core.importers.snipeit.stages.custom_fields import CustomFieldImporter, _definition_digest


class _QuerySet:
    def __init__(self, records, calls):
        self.records = list(records)
        self.calls = calls

    def select_for_update(self):
        self.calls.append(("select_for_update", {}))
        return self

    def filter(self, **criteria):
        self.calls.append(("filter", criteria))
        return type(self)(
            [
                record
                for record in self.records
                if all(getattr(record, key, object()) == value for key, value in criteria.items())
            ],
            self.calls,
        )

    def __getitem__(self, item):
        return self.records[item]


class _AssetModel:
    def __init__(self, records=()):
        self.calls = []
        self.all_objects = _QuerySet(records, self.calls)


class _ChoiceQuerySet:
    def __init__(self, manager, records):
        self.manager = manager
        self.records = list(records)

    def select_for_update(self):
        return self

    def filter(self, **criteria):
        if "choice_set_id" in criteria:
            records = [record for record in self.records if record.choice_set_id == criteria["choice_set_id"]]
        elif "pk" in criteria:
            records = [record for record in self.records if record.pk == criteria["pk"]]
        else:
            records = self.records
        return type(self)(self.manager, records)

    def order_by(self, *fields):
        return self

    def update(self, **values):
        self.manager.updates.append((tuple(record.pk for record in self.records), values))
        for record in self.records:
            for key, value in values.items():
                setattr(record, key, value)

    def __iter__(self):
        return iter(self.records)


class _ChoiceManager:
    def __init__(self, records):
        self.records = list(records)
        self.updates = []
        self.created = []

    def select_for_update(self):
        return _ChoiceQuerySet(self, self.records)

    def filter(self, **criteria):
        return _ChoiceQuerySet(self, self.records).filter(**criteria)

    def create(self, **values):
        self.created.append(values)
        record = SimpleNamespace(pk=len(self.records) + 1, choice_set_id=values["choice_set"].pk, **values)
        self.records.append(record)
        return record


class _ChoiceModel:
    def __init__(self, records):
        self.objects = _ChoiceManager(records)


def _context(*, base_url="https://snipe.example///", tenant=None):
    return SimpleNamespace(
        client=SimpleNamespace(base_url=base_url, context=SimpleNamespace(tenant_id=None)),
        default_tenant=tenant,
    )


def test_type_connector_identity_uses_exact_json_vector_and_normalized_source_id():
    context = _context()
    source_identity = _snipeit_identity(context, 128)

    assert source_identity == {"source_url": "https://snipe.example", "source_id": "128"}
    assert _connector_identity(source_identity) == (
        "sha256:411864a3cc731dc6901cf8f7b9238cc4302d8f8eb3a3a142ffa38174105490bf"
    )


def test_type_connector_identity_uses_sorted_ascii_json_and_ignores_extra_keys():
    assert (
        _connector_identity(
            {
                "source_url": "https://snipe.example/ä",
                "source_id": "β",
                "operator": "archive-only",
            }
        )
        == "sha256:c1cbfdc911a6b519507e7be3e13c2cce652ff3e7aa111858bf823c19bd66a917"
    )


def test_definition_digest_keeps_existing_material_and_tenant_precedence():
    context = _context(tenant=SimpleNamespace(pk=1))

    assert _definition_digest(context, "custom-field", 128) == (
        "sha256:6c16db3ab8cddfbd1ee07422270d4437cf8f6067288923855546c0207924f1fb"
    )


def test_definition_digest_uses_client_context_then_source_global():
    context = _context()
    context.client.context.tenant_id = 7
    tenant_scoped = _definition_digest(context, "fieldset", 4)
    context.client.context.tenant_id = None
    source_global = _definition_digest(context, "fieldset", 4)

    assert tenant_scoped != source_global
    assert tenant_scoped.startswith("sha256:")
    assert source_global.startswith("sha256:")


def test_asset_lookup_uses_connector_identity_not_archived_metadata():
    token = _connector_identity({"source_url": "https://snipe.example", "source_id": "9"})
    owner = SimpleNamespace(
        connector_identity=token,
        model="Stage Model",
        manufacturer="Stage Maker",
        management_kind="local",
        custom_field_data={},
    )
    model = _AssetModel([owner])

    found = AssetModelImporter._find_existing(model, token, "Stage Model", "Stage Maker")

    assert found is owner
    filters = [criteria for operation, criteria in model.calls if operation == "filter"]
    assert filters[0] == {"connector_identity": token}
    assert all("managed_paths" not in criteria for criteria in filters)


def test_asset_lookup_refuses_attribute_collision_from_another_connector_identity():
    model = _AssetModel(
        [
            SimpleNamespace(
                connector_identity="sha256:foreign",
                model="Stage Model",
                manufacturer="Stage Maker",
                management_kind="local",
                custom_field_data={},
            )
        ]
    )
    token = _connector_identity({"source_url": "https://snipe.example", "source_id": "9"})

    with pytest.raises(ValueError, match="another Snipe-IT identity"):
        AssetModelImporter._find_existing(model, token, "Stage Model", "Stage Maker")


def test_asset_dry_run_writer_stores_connector_identity_without_archive_metadata():
    class DryAssetType:
        all_objects = _AssetModel().all_objects

        def __init__(self, **values):
            self.__dict__.update(values)
            self.id = values["id"]

    context = _context()
    context.dry_run = True
    context.update = False
    importer = AssetModelImporter(
        context,
        AssetModelDependencies({1: "Stage Maker"}, {}, {}, {}),
    )

    obj, outcome = importer._upsert(
        DryAssetType,
        object(),
        {"id": 9, "name": "Stage Model", "manufacturer": {"id": 1}},
    )

    assert outcome == "created"
    assert obj.connector_identity == _connector_identity({"source_url": "https://snipe.example", "source_id": "9"})
    assert "managed_paths" not in obj.__dict__


def test_choice_reconciliation_reads_owner_management_and_writes_no_choice_provenance():
    choice_set = SimpleNamespace(pk=10, management_kind="local")
    existing = SimpleNamespace(pk=1, choice_set_id=10, key="old", label="Old", lifecycle="active")
    choice_model = _ChoiceModel([existing])
    importer = CustomFieldImporter(SimpleNamespace(), SimpleNamespace())

    importer._reconcile_choice_rows(choice_model, choice_set, ["New"])

    assert all("management_kind" not in values for _pks, values in choice_model.objects.updates)
    assert choice_model.objects.created
    assert "management_kind" not in choice_model.objects.created[0]
    assert "source_checksum" not in choice_model.objects.created[0]
    assert "managed_paths" not in choice_model.objects.created[0]


def test_choice_reconciliation_refuses_managed_owner_choice_set():
    choice_set = SimpleNamespace(pk=10, management_kind="core")
    choice_model = _ChoiceModel([])
    importer = CustomFieldImporter(SimpleNamespace(), SimpleNamespace())

    with pytest.raises(ValueError, match="managed Choices"):
        importer._reconcile_choice_rows(choice_model, choice_set, [])


def test_type_update_preserves_unrequested_historical_keys():
    values = {"snipeit_id": "historical-value", "known": False, "unknown": 0}
    obj = SimpleNamespace(
        deleted_at=None,
        management_kind="local",
        custom_field_data=values,
        save=lambda: None,
    )
    importer = AssetModelImporter(
        SimpleNamespace(update=True, dry_run=False),
        AssetModelDependencies({}, {}, {}, {}),
    )
    result, outcome = importer._update_existing(
        obj,
        {"custom_field_data": {}},
        None,
        _OMITTED_FIELDSET,
    )
    assert result is obj
    assert outcome == "updated"
    assert obj.custom_field_data == values
