"""PostgreSQL contract tests for the current canonical vocabulary consumer."""

from __future__ import annotations

import io
import json
from decimal import Decimal
from pathlib import Path

import pytest

from assets.models import AssetType, Category, CategoryDefaultFieldset, Manufacturer
from core.management.commands._seed.catalog import _translate_legacy_demo_specs
from core.management.commands.seed_data import Command as SeedDataCommand
from extras.models import CustomField, CustomFieldChoiceSet, CustomFieldset, CustomFieldsetField

pytestmark = pytest.mark.django_db(transaction=True)

FIXTURE_DIR = Path(__file__).resolve().parents[3] / "scripts" / "tests" / "fixtures" / "specification_vocabulary"


def _load_fixture(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _identity_slug(identity):
    return identity.rsplit("/", 1)[1]


def _expected_object_type_models(targets):
    return {"assettype" if target == "asset_type" else target for target in targets}


def _seed_catalog():
    command = SeedDataCommand(stdout=io.StringIO(), stderr=io.StringIO())
    command._seed_catalog()
    return command


def test_legacy_demo_translation_preserves_history_without_generic_poe_inference():
    assert _translate_legacy_demo_specs({"storage_type": "NVMe", "port_count": 48, "poe_budget_w": 740}) == {
        "storage_medium": "nvme_ssd",
        "ethernet_port_count": 48,
        "poe_budget": "740.000",
    }
    assert _translate_legacy_demo_specs({"storage_type": "SSD RAID", "port_count": 8, "poe_budget_w": 0}) == {
        "storage_medium": "ssd",
        "ethernet_port_count": 8,
        "poe_budget": "0.000",
    }
    assert _translate_legacy_demo_specs({"port_count": 8, "poe_port_count": 4}) == {
        "ethernet_port_count": 8,
        "poe_port_count": 4,
    }


def test_runtime_seed_refuses_local_fieldset_membership_collision():
    _seed_catalog()
    fieldset = CustomFieldset.objects.get(namespace="itambox", slug="compute-memory")
    local_field = CustomField.objects.create(
        name="runtime_local_field",
        namespace="local",
        label="Runtime local field",
        field_type="text",
        activation="composed",
        management_kind="local",
        lifecycle="active",
    )
    membership = CustomFieldsetField.objects.create(fieldset=fieldset, custom_field=local_field, position=999)

    with pytest.raises(ValueError, match="Core fieldset membership ownership collision"):
        _seed_catalog()

    assert CustomFieldsetField.objects.filter(pk=membership.pk).exists()


def test_runtime_seed_refuses_local_category_default_collision():
    _seed_catalog()
    local_fieldset = CustomFieldset.objects.create(
        namespace="local",
        slug="runtime-local-default",
        label="Runtime local default",
        description="Local default must not be deleted.",
        management_kind="local",
        lifecycle="active",
    )
    category = Category.objects.get(slug="laptops")
    membership = CategoryDefaultFieldset.objects.create(category=category, fieldset=local_fieldset, position=999)

    with pytest.raises(ValueError, match="Core category default ownership collision"):
        _seed_catalog()

    assert CategoryDefaultFieldset.objects.filter(pk=membership.pk).exists()


def _assert_field_matches(field, expected):
    validation = expected["validation"]
    assert field.namespace == expected["namespace"]
    assert field.label == expected["label"]
    assert field.help_text == expected["help_text"]
    assert field.activation == expected["activation"]
    assert field.field_type == expected["field_type"]
    assert field.quantity_kind == expected["quantity_kind"]
    assert field.canonical_unit == expected["canonical_unit"]
    assert field.required is expected["required"]
    assert field.nullable is expected["nullable"]
    assert field.lifecycle == expected["lifecycle"]
    assert set(field.object_types.values_list("model", flat=True)) == _expected_object_type_models(expected["targets"])
    assert field.choice_set_id == (
        CustomFieldChoiceSet.objects.get(namespace="itambox", slug=_identity_slug(expected["choice_set"])).pk
        if expected["choice_set"]
        else None
    )

    assert field.minimum_value == (Decimal(validation["minimum"]) if "minimum" in validation else None)
    assert field.maximum_value == (Decimal(validation["maximum"]) if "maximum" in validation else None)
    assert field.regex == validation.get("regex")
    assert field.decimal_scale == validation.get("scale")
    assert field.max_values == validation.get("max_values")
    assert field.text_max_length == validation.get("max_length")
    assert field.validation_rule == validation.get("rule")


def test_runtime_seed_matches_complete_normalized_vocabulary():
    canonical = _load_fixture("canonical-target.json")
    _seed_catalog()

    expected_active = {row["key"]: row for row in canonical["active_fields"]}
    expected_retired = {row["key"]: row for row in canonical["reserved_retired_fields"]}
    expected_fields = {**expected_active, **expected_retired}
    runtime_fields = {
        field.name: field
        for field in CustomField.objects.filter(namespace="itambox", management_kind=CustomField.MANAGEMENT_CORE)
    }

    assert set(runtime_fields) == set(expected_fields)
    assert set(field.name for field in runtime_fields.values() if field.lifecycle == "active") == set(expected_active)
    assert set(field.name for field in runtime_fields.values() if field.lifecycle == "deprecated") == set(
        expected_retired
    )
    for key, expected in expected_fields.items():
        _assert_field_matches(runtime_fields[key], expected)

    expected_sections = {row["identity"]: row for row in canonical["sections"]}
    runtime_sections = {
        f"{fieldset.namespace}/{fieldset.slug}": fieldset
        for fieldset in CustomFieldset.objects.filter(
            namespace="itambox", management_kind=CustomFieldset.MANAGEMENT_CORE
        )
    }
    assert set(runtime_sections) == set(expected_sections)
    for identity, expected in expected_sections.items():
        fieldset = runtime_sections[identity]
        assert fieldset.label == expected["label"]
        assert fieldset.description == expected["description"]
        assert fieldset.lifecycle == expected["lifecycle"]
        assert list(fieldset.field_memberships.values_list("custom_field__name", "position")) == [
            (_identity_slug(member["field"]), member["position"]) for member in expected["memberships"]
        ]

    expected_choice_sets = {row["identity"]: row for row in canonical["choice_sets"]}
    runtime_choice_sets = {
        f"{choice_set.namespace}/{choice_set.slug}": choice_set
        for choice_set in CustomFieldChoiceSet.objects.filter(namespace="itambox")
    }
    assert set(runtime_choice_sets) == set(expected_choice_sets)
    for identity, expected in expected_choice_sets.items():
        choice_set = runtime_choice_sets[identity]
        assert choice_set.label == expected["label"]
        assert choice_set.lifecycle == expected["lifecycle"]
        assert list(choice_set.choices.values_list("key", "label", "position", "lifecycle")) == [
            (choice["key"], choice["label"], choice["position"], choice["lifecycle"]) for choice in expected["choices"]
        ]


def test_runtime_seed_matches_category_defaults_and_preserves_local_categories():
    canonical = _load_fixture("canonical-target.json")
    foundation = _load_fixture("foundation-baseline.json")
    _seed_catalog()

    expected_categories = {row["identity"]: row for row in canonical["categories"]}
    canonical_slugs = {_identity_slug(identity) for identity in expected_categories}
    for _identity, expected in expected_categories.items():
        category = Category.objects.get(slug=expected["slug"])
        assert category.name == expected["label"]
        assert category.description == expected["description"]
        assert category.applies_to == {target: True for target in expected["applies_to"]}
        assert list(
            category.default_fieldset_memberships.values_list("fieldset__namespace", "fieldset__slug", "position")
        ) == [("itambox", _identity_slug(item["fieldset"]), item["position"]) for item in expected["default_fieldsets"]]

    foundation_categories = {row["identity"]: row for row in foundation["categories"]}
    local_categories = foundation_categories.keys() - expected_categories.keys()
    assert local_categories
    for identity in local_categories:
        expected = foundation_categories[identity]
        category = Category.objects.get(slug=expected["slug"])
        assert category.name == expected["label"]
        assert category.color == expected["color"]
        assert category.applies_to == {target: True for target in expected["applies_to"]}
        assert category.slug not in canonical_slugs


def test_runtime_seed_is_idempotent_without_catalogue_duplication_or_type_propagation():
    _seed_catalog()
    first_counts = {
        "asset_types": AssetType.objects.count(),
        "manufacturers": Manufacturer.objects.count(),
        "categories": Category.objects.count(),
    }
    first_type_memberships = {
        asset_type.slug: list(asset_type.fieldset_memberships.values_list("fieldset__slug", "position"))
        for asset_type in AssetType.objects.all()
    }

    _seed_catalog()

    assert AssetType.objects.count() == first_counts["asset_types"]
    assert Manufacturer.objects.count() == first_counts["manufacturers"]
    assert Category.objects.count() == first_counts["categories"]
    assert {
        asset_type.slug: list(asset_type.fieldset_memberships.values_list("fieldset__slug", "position"))
        for asset_type in AssetType.objects.all()
    } == first_type_memberships
