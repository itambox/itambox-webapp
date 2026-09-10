"""Pure history projection tests for the T05 specification seam."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from extras.services.specifications.contracts import (
    ChoiceDTO,
    ChoiceSetDTO,
    DefinitionRevision,
    FieldDefinitionDTO,
    FieldKey,
    JSONValue,
    ProjectionIssueDTO,
    QualifiedIdentity,
    ResolvedFieldDTO,
    ResolvedSectionDTO,
    SpecificationDefinitionDTO,
    SpecificationValidationDTO,
    StoredSpecificationEntryDTO,
)
from extras.services.specifications.projection import project_specification_values

HISTORY_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "assets" / "tests" / "specification_contract_fixtures" / "history.json"
)


@dataclass(frozen=True)
class ProjectionRequestFixture:
    """The canonical Assets-owned request shape, kept local until sibling integration."""

    definition: SpecificationDefinitionDTO
    stored_entries: tuple[StoredSpecificationEntryDTO, ...]
    historical_definitions_by_key: Mapping[FieldKey, FieldDefinitionDTO]


def validation(
    *,
    scale: int | None = None,
    max_values: int | None = None,
) -> SpecificationValidationDTO:
    return SpecificationValidationDTO(
        minimum=None,
        maximum=None,
        scale=scale,
        max_length=None,
        max_values=max_values,
        regex=None,
        rule=None,
    )


def choice_set(
    *,
    active: tuple[str, ...],
    deprecated: tuple[str, ...] = (),
) -> ChoiceSetDTO:
    choices = tuple(
        ChoiceDTO(key=key, label=key, lifecycle="active", position=position) for position, key in enumerate(active)
    ) + tuple(
        ChoiceDTO(key=key, label=key, lifecycle="deprecated", position=position + len(active))
        for position, key in enumerate(deprecated)
    )
    return ChoiceSetDTO(
        identity=QualifiedIdentity("test/status"),
        label="Test choices",
        resource_revision="resource-1",
        lifecycle="active",
        choices=choices,
    )


def field(
    key: str,
    field_type: str,
    *,
    required: bool = False,
    nullable: bool = False,
    choice_definition: ChoiceSetDTO | None = None,
    lifecycle: str = "active",
    scale: int | None = None,
    max_values: int | None = None,
) -> FieldDefinitionDTO:
    return FieldDefinitionDTO(
        resource_revision="resource-1",
        key=FieldKey(key),
        identity=QualifiedIdentity(f"test/{key}"),
        label=key,
        help_text="",
        targets=frozenset({"asset"}),
        activation="composed",
        field_type=field_type,
        quantity_kind=None,
        canonical_unit=None,
        validation=validation(scale=scale, max_values=max_values),
        required=required,
        nullable=nullable,
        lifecycle=lifecycle,
        choice_set=choice_definition,
    )


def resolved_field(definition: FieldDefinitionDTO, section_key: str) -> ResolvedFieldDTO:
    return ResolvedFieldDTO(
        resource_revision=definition.resource_revision,
        key=definition.key,
        identity=definition.identity,
        label=definition.label,
        help_text=definition.help_text,
        targets=definition.targets,
        activation=definition.activation,
        field_type=definition.field_type,
        quantity_kind=definition.quantity_kind,
        canonical_unit=definition.canonical_unit,
        validation=definition.validation,
        required=definition.required,
        nullable=definition.nullable,
        lifecycle=definition.lifecycle,
        choice_set=definition.choice_set,
        first_placement_section_identity=QualifiedIdentity(f"test/{section_key}"),
        contributing_section_identities=(QualifiedIdentity(f"test/{section_key}"),),
    )


def request(
    definitions: Mapping[str, FieldDefinitionDTO],
    current_keys: tuple[str, ...],
    stored: Mapping[str, JSONValue],
) -> ProjectionRequestFixture:
    sections = tuple(
        ResolvedSectionDTO(
            section_kind="persisted_fieldset",
            identity=QualifiedIdentity(f"test/section-{key}"),
            label=key,
            description="",
            persisted_ordinal=ordinal,
            fields=(resolved_field(definitions[key], key),),
        )
        for ordinal, key in enumerate(current_keys, start=1)
    )
    definition = SpecificationDefinitionDTO(
        revision=DefinitionRevision("definition-revision-1"),
        target_kind="asset",
        persisted_memberships=(),
        rendered_sections=sections,
    )
    historical = MappingProxyType({FieldKey(key): value for key, value in definitions.items()})
    entries = tuple(StoredSpecificationEntryDTO(key=FieldKey(key), value=value) for key, value in stored.items())
    return ProjectionRequestFixture(definition, entries, historical)


def json_shape(value: JSONValue):
    if isinstance(value, tuple):
        return [json_shape(item) for item in value]
    if isinstance(value, Mapping):
        return {key: json_shape(item) for key, item in value.items()}
    return value


class SpecificationProjectionImportTests(unittest.TestCase):
    def test_projection_import_is_framework_free_in_a_fresh_interpreter(self):
        probe = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                "import importlib, sys; sys.path.insert(0, sys.argv[1]); "
                "importlib.import_module('extras.services.specifications.projection'); "
                "assert not any(name == 'django' or name.startswith('django.') for name in sys.modules)",
                str(Path(__file__).resolve().parents[2]),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr)


class SpecificationProjectionFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(HISTORY_FIXTURE_PATH.read_text(encoding="utf-8"))
        cls.definitions = {
            "status": field(
                "status",
                "single_select",
                choice_definition=choice_set(active=("in_service", "retired"), deprecated=("old_status",)),
            ),
            "tags": field(
                "tags",
                "multi_select",
                choice_definition=choice_set(
                    active=("active_x", "active_y"),
                    deprecated=("old_tag_a", "old_tag_b"),
                ),
                max_values=8,
            ),
            "legacy_note": field("legacy_note", "text", lifecycle="deprecated"),
            "required_status": field(
                "required_status",
                "single_select",
                required=True,
                choice_definition=choice_set(active=("active_z",), deprecated=("old_status_z",)),
            ),
            "owner_note": field("owner_note", "text"),
            "f3_location": field("f3_location", "text"),
            "f4_cost": field("f4_cost", "decimal", scale=2),
            "cost": field("cost", "decimal", scale=2),
        }
        cls.current_keys = ("status", "tags", "required_status", "owner_note", "f3_location", "cost")

    def project(self, stored: Mapping[str, JSONValue], *, current_keys: tuple[str, ...] | None = None):
        return project_specification_values(
            request(
                self.definitions,
                self.current_keys if current_keys is None else current_keys,
                stored,
            )
        )

    def test_all_repaired_history_projection_cases_are_consumed(self):
        expected_ids = {f"T02-HIST-{number:03d}" for number in range(1, 16)}
        self.assertEqual({case["id"] for case in self.fixture["cases"]}, expected_ids)

        expected_projection_cases = {
            "T02-HIST-006",
            "T02-HIST-007",
            "T02-HIST-009",
            "T02-HIST-010",
            "T02-HIST-011",
            "T02-HIST-015",
        }
        projection_cases = {case["id"] for case in self.fixture["cases"] if case["operation"]["kind"] == "project"}
        projection_cases.add("T02-HIST-007")
        self.assertEqual(projection_cases, expected_projection_cases)

        for case in self.fixture["cases"]:
            if case["id"] not in expected_projection_cases:
                continue
            with self.subTest(case=case["id"]):
                expected = case["expected_result"]
                stored = case["operation"].get("stored", expected.get("stored", {}))
                result = self.project(stored)
                by_key = {str(entry.key): entry for entry in result.entries}
                if case["id"] == "T02-HIST-006":
                    self.assertEqual(
                        [(str(issue.field_key), issue.code) for issue in result.missing_required_issues],
                        [("required_status", "MISSING_REQUIRED")],
                    )
                    self.assertEqual(result.entries, ())
                elif case["id"] == "T02-HIST-007":
                    self.assertEqual(json_shape(by_key["cost"].value), expected["preserved_invalid"]["value"])
                    self.assertEqual(by_key["cost"].state, "invalid")
                    self.assertEqual(by_key["cost"].reason_codes, ("INVALID_STORED_VALUE",))
                    self.assertEqual(by_key["owner_note"].reason_codes, ("ACTIVE_VALUE",))
                elif case["id"] == "T02-HIST-009":
                    self.assertEqual(by_key["f4_cost"].state, "historical")
                    self.assertEqual(by_key["f4_cost"].reason_codes, ("INACTIVE_COMPOSITION",))
                    self.assertEqual(json_shape(by_key["f4_cost"].value), "12.30")
                elif case["id"] == "T02-HIST-010":
                    self.assertEqual(by_key["legacy_note"].state, "historical")
                    self.assertEqual(by_key["legacy_note"].reason_codes, ("DEPRECATED_FIELD",))
                    self.assertEqual(by_key["legacy_note"].value, "kept")
                elif case["id"] == "T02-HIST-011":
                    self.assertEqual(by_key["ghost_key"].state, "unknown")
                    self.assertEqual(by_key["ghost_key"].reason_codes, ("UNKNOWN_DEFINITION",))
                    self.assertIsNone(by_key["ghost_key"].definition)
                    self.assertEqual(by_key["ghost_key"].value, "v")
                elif case["id"] == "T02-HIST-015":
                    self.assertEqual(
                        [(str(entry.key), json_shape(entry.value)) for entry in result.entries],
                        [
                            ("status", "old_status"),
                            ("cost", "broken"),
                            ("ghost_key", "v"),
                            ("owner_note", "x"),
                        ],
                    )
                    self.assertEqual(by_key["status"].state, "historical")
                    self.assertEqual(by_key["status"].reason_codes, ("DEPRECATED_CHOICE",))
                    self.assertEqual(by_key["cost"].state, "invalid")
                    self.assertEqual(by_key["cost"].reason_codes, ("INVALID_STORED_VALUE",))
                    self.assertEqual(by_key["ghost_key"].state, "unknown")
                    self.assertEqual(by_key["owner_note"].state, "current")
                    self.assertEqual(by_key["owner_note"].reason_codes, ("ACTIVE_VALUE",))
                self.assertEqual(expected["outcome"], "accepted")

    def test_deprecated_choice_keeps_raw_order_and_is_not_repaired(self):
        raw = ("old_tag_b", "active_x")
        result = self.project({"tags": raw})
        entry = result.entries[0]
        self.assertEqual(entry.value, raw)
        self.assertEqual(entry.state, "historical")
        self.assertEqual(entry.reason_codes, ("DEPRECATED_CHOICE",))

    def test_combined_historical_and_invalid_reasons_are_ordered_without_duplicates(self):
        result = self.project(
            {
                "tags": ("old_tag_b", 7),
                "f4_cost": "not-a-decimal",
                "legacy_note": 7,
            }
        )
        entries = {str(entry.key): entry for entry in result.entries}
        self.assertEqual(entries["tags"].state, "invalid")
        self.assertEqual(entries["tags"].reason_codes, ("DEPRECATED_CHOICE", "INVALID_STORED_VALUE"))
        self.assertEqual(entries["f4_cost"].state, "invalid")
        self.assertEqual(entries["f4_cost"].reason_codes, ("INACTIVE_COMPOSITION", "INVALID_STORED_VALUE"))
        self.assertEqual(entries["legacy_note"].state, "invalid")
        self.assertEqual(entries["legacy_note"].reason_codes, ("DEPRECATED_FIELD", "INVALID_STORED_VALUE"))
        for entry in entries.values():
            self.assertEqual(len(entry.reason_codes), len(set(entry.reason_codes)))

    def test_required_diagnostics_are_separate_and_do_not_add_entries(self):
        result = self.project({"owner_note": "present"})
        self.assertEqual(result.entries[0].key, FieldKey("owner_note"))
        self.assertEqual(
            result.missing_required_issues,
            (ProjectionIssueDTO("MISSING_REQUIRED", FieldKey("required_status")),),
        )

    def test_different_raw_values_share_the_same_value_independent_definition_revision(self):
        first = self.project({"owner_note": "first"})
        second = self.project({"owner_note": "second", "ghost_key": "history"})
        self.assertEqual(first.entries[0].definition, second.entries[0].definition)
        self.assertEqual(first.entries[0].definition.key, FieldKey("owner_note"))
        self.assertEqual(first.entries[0].definition.resource_revision, "resource-1")
        self.assertEqual(
            request(self.definitions, self.current_keys, {}).definition.revision,
            request(self.definitions, self.current_keys, {"owner_note": "changed"}).definition.revision,
        )
        self.assertEqual(second.entries[1].state, "unknown")


if __name__ == "__main__":
    unittest.main()
