"""Pure consumer contracts for the T06 definition cutover."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.importers.snipeit.stages.custom_fields import CustomFieldImporter


class _FakeQuerySet(list):
    def select_for_update(self):
        return self

    def filter(self, **filters):
        return type(self)(row for row in self if all(getattr(row, field) == value for field, value in filters.items()))

    def order_by(self, *fields):
        ordered = list(self)
        for field in reversed(fields):
            ordered.sort(key=lambda row: getattr(row, field))
        return type(self)(ordered)

    def update(self, **values):
        for row in self:
            for field, value in values.items():
                setattr(row, field, value)
        return len(self)


class _FakeChoiceManager(_FakeQuerySet):
    def create(self, **values):
        choice_set = values.get("choice_set")
        if choice_set is not None:
            values["choice_set_id"] = choice_set.pk
        values["pk"] = max((row.pk for row in self), default=0) + 1
        row = SimpleNamespace(**values)
        self.append(row)
        return row


class _FakeChoiceModel:
    def __init__(self, rows):
        self.objects = _FakeChoiceManager(rows)


def _choice(key, position, *, lifecycle="active", pk=None):
    return SimpleNamespace(
        pk=position if pk is None else pk,
        key=key,
        label=key.title(),
        position=position,
        choice_set_id=17,
        management_kind="local",
        lifecycle=lifecycle,
    )


@pytest.mark.parametrize(
    ("existing", "desired", "expected"),
    [
        (
            [SimpleNamespace(key="a", position=1), SimpleNamespace(key="b", position=2)],
            [("b", "B")],
            ["b", "a"],
        ),
        (
            [
                SimpleNamespace(key="a", position=1),
                SimpleNamespace(key="b", position=2),
                SimpleNamespace(key="c", position=3),
            ],
            [("c", "C"), ("a", "A")],
            ["c", "a", "b"],
        ),
        (
            [
                SimpleNamespace(key="a", position=1),
                SimpleNamespace(key="b", position=2),
                SimpleNamespace(key="retired", position=3, lifecycle="deprecated"),
            ],
            [("a", "A"), ("b", "B")],
            ["a", "b", "retired"],
        ),
        (
            [
                SimpleNamespace(key="a", position=1),
                SimpleNamespace(key="retired", position=2, lifecycle="deprecated"),
            ],
            [("new", "New"), ("a", "A")],
            ["new", "a", "retired"],
        ),
    ],
)
def test_choice_reconciliation_orders_every_identity_densely(existing, desired, expected):
    ordered = CustomFieldImporter._full_choice_order(existing, desired)

    assert ordered == expected
    positions = {key: position for position, key in enumerate(ordered, start=1)}
    assert sorted(positions.values()) == list(range(1, len(expected) + 1))
    assert len(positions) == len(expected)


def test_deprecated_choice_is_not_reused_by_label_matching():
    importer = object.__new__(CustomFieldImporter)
    existing = [SimpleNamespace(pk=1, key="old_key", label="Old label", lifecycle="deprecated")]

    _existing_by_key, desired = importer._desired_choice_keys(existing, ["Old label"])

    assert desired[0][0] != "old_key"
    assert desired[0][1] == "Old label"


@pytest.mark.parametrize(
    ("existing", "remote_labels", "expected_order", "active_keys"),
    [
        ([_choice("a", 1), _choice("b", 2)], ["B"], ["b", "a"], {"b"}),
        (
            [_choice("a", 1), _choice("b", 2), _choice("c", 3)],
            ["C", "A"],
            ["c", "a", "b"],
            {"c", "a"},
        ),
        (
            [_choice("a", 1), _choice("b", 2), _choice("retired", 3, lifecycle="deprecated")],
            ["A", "B"],
            ["a", "b", "retired"],
            {"a", "b"},
        ),
        (
            [_choice("a", 1), _choice("retired", 2, lifecycle="deprecated")],
            ["New", "A"],
            ["new", "a", "retired"],
            {"new", "a"},
        ),
    ],
)
def test_choice_reconciliation_applies_dense_positions_for_every_identity(
    existing, remote_labels, expected_order, active_keys
):
    original_identity = {row.key: (row.pk, row.label) for row in existing}
    choice_model = _FakeChoiceModel(existing)
    importer = object.__new__(CustomFieldImporter)

    importer._reconcile_choice_rows(choice_model, SimpleNamespace(pk=17), remote_labels)

    rows = sorted(choice_model.objects, key=lambda row: row.position)
    assert [row.key for row in rows] == expected_order
    assert [row.position for row in rows] == list(range(1, len(expected_order) + 1))
    assert len({row.position for row in rows}) == len(rows)
    assert {row.key for row in rows if row.lifecycle == "active"} == active_keys
    assert {row.key for row in rows if row.lifecycle == "deprecated"} == set(expected_order) - active_keys
    for row in rows:
        if row.key in original_identity:
            assert row.pk == original_identity[row.key][0]
            if row.key not in active_keys:
                assert row.label == original_identity[row.key][1]


def test_definition_consumers_do_not_use_retired_schema_fields():
    root = Path(__file__).resolve().parents[2]
    catalog = (root / "core" / "management" / "commands" / "_seed" / "catalog.py").read_text()
    importer = (root / "core" / "importers" / "snipeit" / "stages" / "custom_fields.py").read_text()

    for source in (catalog, importer):
        assert "all_objects" not in source
        assert "deleted_at" not in source
    assert '"scope"' not in catalog
    assert "scope=" not in catalog
    assert '"activation"' in catalog
    assert "object_types" in catalog
    assert "position=index * 10" not in catalog


def test_seed_dense_memberships_preserve_declared_order(monkeypatch):
    from unittest.mock import Mock

    from core.management.commands._seed import catalog

    fieldset = SimpleNamespace(save=Mock(), field_memberships=Mock())
    membership = Mock(side_effect=lambda **values: SimpleNamespace(**values))
    membership._base_manager.filter.return_value.values_list.return_value = []
    monkeypatch.setattr(catalog, "_get_core_fieldset", lambda slug, label: fieldset)
    monkeypatch.setattr(catalog, "CustomFieldsetField", membership)
    fields = {"later": SimpleNamespace(pk=1), "earlier": SimpleNamespace(pk=2)}
    rows = [
        {"key": "later", "fieldset_slug": "hardware", "position": 20},
        {"key": "earlier", "fieldset_slug": "hardware", "position": 10},
    ]

    catalog._reconcile_core_fieldsets(rows, {"hardware": "Hardware"}, fields)

    created = membership.objects.bulk_create.call_args.args[0]
    assert [(row.custom_field.pk, row.position) for row in created] == [(2, 1), (1, 2)]
