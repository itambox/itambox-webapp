"""Pure tests for value-independent specification definition resolution."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from dataclasses import dataclass, replace
from pathlib import Path

from extras.services.specifications.composition import resolve_specification_definition
from extras.services.specifications.contracts import (
    ChoiceDTO,
    ChoiceSetDTO,
    FieldDefinitionDTO,
    FieldKey,
    LoadedSpecificationGraphDTO,
    OrderedFieldMembershipDTO,
    OrderedFieldsetMembershipDTO,
    PersistedFieldsetDTO,
    QualifiedIdentity,
    ResourceRevision,
    SpecificationDefinitionDTO,
    SpecificationValidationDTO,
)

COMPOSITION_FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "assets" / "tests" / "specification_contract_fixtures" / "composition.json"
)


@dataclass(frozen=True)
class ResolutionRequestShape:
    """The canonical Assets request shape, kept local for stdlib-only tests."""

    ordered_memberships: tuple[OrderedFieldsetMembershipDTO, ...]
    loaded_graph: LoadedSpecificationGraphDTO
    target_kind: str


def validation() -> SpecificationValidationDTO:
    return SpecificationValidationDTO(
        minimum=None,
        maximum=None,
        scale=None,
        max_length=None,
        max_values=None,
        regex=None,
        rule=None,
    )


def choice_set(
    identity: str = "itambox/status",
    *,
    rows: tuple[tuple[str, str, str, int], ...] = (
        ("active", "Active", "active", 1),
        ("retired", "Retired", "deprecated", 2),
    ),
    resource_revision: str = "choice-set-rev-1",
) -> ChoiceSetDTO:
    return ChoiceSetDTO(
        identity=QualifiedIdentity(identity),
        label="Status",
        resource_revision=ResourceRevision(resource_revision),
        lifecycle="active",
        choices=tuple(
            ChoiceDTO(key=key, label=label, lifecycle=lifecycle, position=position)
            for key, label, lifecycle, position in rows
        ),
    )


def field(
    key: str,
    *,
    label: str | None = None,
    targets: frozenset[str] = frozenset({"asset"}),
    activation: str = "composed",
    lifecycle: str = "active",
    field_type: str = "text",
    resource_revision: str = "field-rev-1",
    choice_definition: ChoiceSetDTO | None = None,
) -> FieldDefinitionDTO:
    return FieldDefinitionDTO(
        resource_revision=ResourceRevision(resource_revision),
        key=FieldKey(key),
        identity=QualifiedIdentity(f"itambox/{key}"),
        label=label or key,
        help_text=f"Help for {key}",
        targets=targets,
        activation=activation,
        field_type=field_type,
        quantity_kind=None,
        canonical_unit=None,
        validation=validation(),
        required=False,
        nullable=False,
        lifecycle=lifecycle,
        choice_set=choice_definition,
    )


def fieldset(
    identity: str,
    label: str,
    field_identities: tuple[tuple[str, int], ...],
    *,
    lifecycle: str = "active",
    resource_revision: str = "fieldset-rev-1",
) -> PersistedFieldsetDTO:
    return PersistedFieldsetDTO(
        identity=QualifiedIdentity(identity),
        label=label,
        description=f"Description for {label}",
        resource_revision=ResourceRevision(resource_revision),
        lifecycle=lifecycle,
        field_memberships=tuple(
            OrderedFieldMembershipDTO(field_identity=QualifiedIdentity(identity), ordinal=ordinal)
            for identity, ordinal in field_identities
        ),
    )


def graph(
    fields: tuple[FieldDefinitionDTO, ...],
    fieldsets: tuple[PersistedFieldsetDTO, ...],
    memberships: tuple[tuple[str, int], ...],
    *,
    global_keys: tuple[str, ...] = (),
) -> tuple[LoadedSpecificationGraphDTO, tuple[OrderedFieldsetMembershipDTO, ...]]:
    ordered_memberships = tuple(
        OrderedFieldsetMembershipDTO(fieldset_identity=QualifiedIdentity(identity), ordinal=ordinal)
        for identity, ordinal in memberships
    )
    loaded = LoadedSpecificationGraphDTO(
        type_memberships={1: ordered_memberships},
        fieldsets_by_identity={fieldset.identity: fieldset for fieldset in fieldsets},
        fields_by_key={field.key: field for field in fields},
        global_field_keys_by_target={
            "asset_type": (),
            "asset": tuple(FieldKey(key) for key in global_keys),
        },
        historical_definitions_by_key={},
    )
    return loaded, ordered_memberships


def request(
    loaded: LoadedSpecificationGraphDTO,
    memberships: tuple[OrderedFieldsetMembershipDTO, ...],
    target_kind: str = "asset",
) -> ResolutionRequestShape:
    return ResolutionRequestShape(
        ordered_memberships=memberships,
        loaded_graph=loaded,
        target_kind=target_kind,
    )


class SpecificationDefinitionResolutionTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.fields = (
            field("f1_name", label="Name"),
            field("f2_owner", label="Owner"),
            field("f3_location", label="Location"),
            field("f4_cost", label="Cost", field_type="decimal"),
            field("g1_global_note", label="Global note", activation="global"),
            field("g2_global_tag", label="Global tag", activation="global", field_type="single_select"),
            field("legacy_label", lifecycle="deprecated"),
        )
        self.fieldsets = (
            fieldset(
                "itambox/fs_personal",
                "Personal",
                (("itambox/f1_name", 1), ("itambox/f2_owner", 2), ("itambox/f3_location", 3)),
            ),
            fieldset(
                "itambox/fs_placement",
                "Placement",
                (("itambox/f3_location", 1), ("itambox/f4_cost", 2)),
            ),
            fieldset(
                "itambox/fs_deprecated",
                "Deprecated",
                (("itambox/legacy_label", 1),),
                lifecycle="deprecated",
            ),
        )

    def resolve(
        self,
        *,
        fields: tuple[FieldDefinitionDTO, ...] | None = None,
        fieldsets: tuple[PersistedFieldsetDTO, ...] | None = None,
        memberships: tuple[tuple[str, int], ...] = (
            ("itambox/fs_personal", 10),
            ("itambox/fs_placement", 20),
        ),
        global_keys: tuple[str, ...] = ("g1_global_note", "g2_global_tag"),
        target_kind: str = "asset",
    ) -> SpecificationDefinitionDTO:
        loaded, ordered_memberships = graph(
            fields if fields is not None else self.fields,
            fieldsets if fieldsets is not None else self.fieldsets,
            memberships,
            global_keys=global_keys,
        )
        return resolve_specification_definition(request(loaded, ordered_memberships, target_kind))

    def test_t02_composition_renders_ordered_sections_and_all_provenance(self):
        definition = self.resolve()

        self.assertEqual(
            definition.persisted_memberships,
            (
                OrderedFieldsetMembershipDTO(QualifiedIdentity("itambox/fs_personal"), 1),
                OrderedFieldsetMembershipDTO(QualifiedIdentity("itambox/fs_placement"), 2),
            ),
        )
        self.assertEqual(
            tuple(section.identity for section in definition.rendered_sections),
            (QualifiedIdentity("itambox/fs_personal"), QualifiedIdentity("itambox/fs_placement"), None),
        )
        self.assertEqual(
            tuple(field.key for field in definition.rendered_sections[0].fields),
            (FieldKey("f1_name"), FieldKey("f2_owner"), FieldKey("f3_location")),
        )
        self.assertEqual(
            tuple(field.key for field in definition.rendered_sections[1].fields),
            (FieldKey("f4_cost"),),
        )
        location = definition.rendered_sections[0].fields[2]
        self.assertEqual(location.first_placement_section_identity, QualifiedIdentity("itambox/fs_personal"))
        self.assertEqual(
            location.contributing_section_identities,
            (QualifiedIdentity("itambox/fs_personal"), QualifiedIdentity("itambox/fs_placement")),
        )

        additional = definition.rendered_sections[-1]
        self.assertEqual(additional.section_kind, "derived_additional")
        self.assertIsNone(additional.identity)
        self.assertIsNone(additional.persisted_ordinal)
        self.assertEqual(
            tuple(field.key for field in additional.fields),
            (FieldKey("g1_global_note"), FieldKey("g2_global_tag")),
        )

    def test_reordering_changes_first_placement_and_normalizes_sparse_ordinals(self):
        definition = self.resolve(
            memberships=(
                ("itambox/fs_placement", 30),
                ("itambox/fs_personal", 90),
            )
        )

        self.assertEqual(
            definition.persisted_memberships,
            (
                OrderedFieldsetMembershipDTO(QualifiedIdentity("itambox/fs_placement"), 1),
                OrderedFieldsetMembershipDTO(QualifiedIdentity("itambox/fs_personal"), 2),
            ),
        )
        self.assertEqual(definition.rendered_sections[0].persisted_ordinal, 1)
        self.assertEqual(
            definition.rendered_sections[0].fields[0].first_placement_section_identity,
            QualifiedIdentity("itambox/fs_placement"),
        )
        self.assertEqual(
            tuple(field.key for field in definition.rendered_sections[0].fields),
            (FieldKey("f3_location"), FieldKey("f4_cost")),
        )

    def test_deprecated_sections_and_fields_are_omitted_but_memberships_survive(self):
        definition = self.resolve(
            memberships=(
                ("itambox/fs_deprecated", 7),
                ("itambox/fs_personal", 14),
                ("itambox/fs_placement", 21),
            )
        )

        self.assertEqual(
            definition.persisted_memberships,
            (
                OrderedFieldsetMembershipDTO(QualifiedIdentity("itambox/fs_deprecated"), 1),
                OrderedFieldsetMembershipDTO(QualifiedIdentity("itambox/fs_personal"), 2),
                OrderedFieldsetMembershipDTO(QualifiedIdentity("itambox/fs_placement"), 3),
            ),
        )
        self.assertNotIn(
            QualifiedIdentity("itambox/fs_deprecated"),
            tuple(section.identity for section in definition.rendered_sections),
        )
        rendered_keys = {field.key for section in definition.rendered_sections for field in section.fields}
        self.assertNotIn(FieldKey("legacy_label"), rendered_keys)

    def test_only_requested_target_is_applicable(self):
        fields = (
            field("asset_only", targets=frozenset({"asset"})),
            field("type_only", targets=frozenset({"asset_type"})),
            field("both", targets=frozenset({"asset", "asset_type"})),
        )
        fieldsets = (
            fieldset(
                "itambox/fs_targeted",
                "Targeted",
                (("itambox/asset_only", 1), ("itambox/type_only", 2), ("itambox/both", 3)),
            ),
        )

        asset_definition = self.resolve(
            fields=fields,
            fieldsets=fieldsets,
            memberships=(("itambox/fs_targeted", 1),),
            global_keys=(),
            target_kind="asset",
        )
        type_definition = self.resolve(
            fields=fields,
            fieldsets=fieldsets,
            memberships=(("itambox/fs_targeted", 1),),
            global_keys=(),
            target_kind="asset_type",
        )
        self.assertEqual(
            tuple(field.key for field in asset_definition.rendered_sections[0].fields),
            (FieldKey("asset_only"), FieldKey("both")),
        )
        self.assertEqual(
            tuple(field.key for field in type_definition.rendered_sections[0].fields),
            (FieldKey("type_only"), FieldKey("both")),
        )

    def test_global_field_in_a_fieldset_is_an_invalid_definition(self):
        fields = (field("global_note", activation="global"),)
        fieldsets = (fieldset("itambox/fs_invalid", "Invalid", (("itambox/global_note", 1),)),)
        with self.assertRaisesRegex(ValueError, "global.*Fieldset"):
            self.resolve(
                fields=fields,
                fieldsets=fieldsets,
                memberships=(("itambox/fs_invalid", 1),),
                global_keys=("global_note",),
            )

    def test_empty_active_section_is_not_rendered(self):
        fields = (field("asset_only", targets=frozenset({"asset_type"})),)
        fieldsets = (fieldset("itambox/fs_empty", "Empty", (("itambox/asset_only", 1),)),)
        definition = self.resolve(
            fields=fields,
            fieldsets=fieldsets,
            memberships=(("itambox/fs_empty", 1),),
            global_keys=(),
        )
        self.assertEqual(definition.rendered_sections, ())
        self.assertEqual(
            definition.persisted_memberships,
            (OrderedFieldsetMembershipDTO(QualifiedIdentity("itambox/fs_empty"), 1),),
        )

    def test_additional_fields_sort_by_label_then_immutable_key(self):
        fields = (
            field("z_global", label="Alpha", activation="global"),
            field("a_global", label="Zulu", activation="global"),
        )
        definition = self.resolve(
            fields=fields,
            fieldsets=(),
            memberships=(),
            global_keys=("a_global", "z_global"),
        )
        self.assertEqual(
            tuple(item.key for item in definition.rendered_sections[0].fields),
            (FieldKey("z_global"), FieldKey("a_global")),
        )
        self.assertIsNone(definition.rendered_sections[0].identity)
        self.assertIsNone(definition.rendered_sections[0].persisted_ordinal)

    def test_field_membership_order_is_dense_and_ordinal_driven(self):
        fields = (field("first"), field("second"))
        fieldsets = (
            fieldset(
                "itambox/fs_ordered",
                "Ordered",
                (("itambox/second", 20), ("itambox/first", 10)),
            ),
        )
        definition = self.resolve(
            fields=fields,
            fieldsets=fieldsets,
            memberships=(("itambox/fs_ordered", 40),),
            global_keys=(),
        )
        self.assertEqual(
            tuple(item.key for item in definition.rendered_sections[0].fields),
            (FieldKey("first"), FieldKey("second")),
        )

    def test_definition_revision_changes_for_effective_metadata_not_values(self):
        base_definition = self.resolve()
        changed_field = tuple(
            replace(item, resource_revision=ResourceRevision("field-rev-2"))
            if item.key == FieldKey("f1_name")
            else item
            for item in self.fields
        )
        changed_field_revision = self.resolve(fields=changed_field)
        self.assertNotEqual(base_definition.revision, changed_field_revision.revision)

        choices = choice_set(rows=(("active", "Active", "active", 1), ("retired", "Retired", "deprecated", 2)))
        select_field = field("select", field_type="single_select", choice_definition=choices)
        select_fieldset = fieldset("itambox/fs_select", "Select", (("itambox/select", 1),))
        choice_base = self.resolve(
            fields=(select_field,),
            fieldsets=(select_fieldset,),
            memberships=(("itambox/fs_select", 1),),
            global_keys=(),
        )
        changed_choices = replace(
            choices,
            choices=(replace(choices.choices[0], label="Renamed"), choices.choices[1]),
        )
        choice_changed_field = replace(select_field, choice_set=changed_choices)
        choice_changed = self.resolve(
            fields=(choice_changed_field,),
            fieldsets=(select_fieldset,),
            memberships=(("itambox/fs_select", 1),),
            global_keys=(),
        )
        self.assertNotEqual(choice_base.revision, choice_changed.revision)
        position_changed_choices = replace(
            choices,
            choices=(choices.choices[0], replace(choices.choices[1], position=9)),
        )
        position_changed = self.resolve(
            fields=(replace(select_field, choice_set=position_changed_choices),),
            fieldsets=(select_fieldset,),
            memberships=(("itambox/fs_select", 1),),
            global_keys=(),
        )
        self.assertNotEqual(choice_base.revision, position_changed.revision)

        reordered = self.resolve(
            memberships=(
                ("itambox/fs_placement", 1),
                ("itambox/fs_personal", 2),
            )
        )
        self.assertNotEqual(base_definition.revision, reordered.revision)

        # Resolution has no stored-value input. Supplying different external values
        # therefore cannot alter the same graph's definition revision.
        self.assertEqual(base_definition.revision, self.resolve().revision)

    def test_composition_import_is_pure_in_a_fresh_stdlib_interpreter(self):
        package_root = str(Path(__file__).resolve().parents[2])
        probe = (
            "import importlib, json, sys; "
            "sys.path.insert(0, sys.argv[1]); "
            "importlib.import_module('extras.services.specifications.composition'); "
            "blocked = {'django', 'assets', 'itambox', 'organization'}; "
            "print(json.dumps(sorted(name for name in sys.modules "
            "if name.split('.', 1)[0] in blocked)))"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", probe, package_root],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [])

    def test_t02_composition_oracle_resolution_cases(self):
        document = json.loads(COMPOSITION_FIXTURE_PATH.read_text(encoding="utf-8"))
        raw_fields = document["context"]["fields"]
        fixture_fields = tuple(
            replace(
                field(
                    key,
                    targets=frozenset(specification["targets"]),
                    activation=specification["activation"],
                    lifecycle=specification.get("lifecycle", "active"),
                    field_type=specification.get("type", "text"),
                ),
                identity=QualifiedIdentity(key),
            )
            for key, specification in raw_fields.items()
        )
        fixture_fieldsets = tuple(
            fieldset(
                identity,
                identity,
                tuple((field_key, ordinal) for field_key, ordinal in specification["ordinals"].items()),
                lifecycle=specification.get("lifecycle", "active"),
            )
            for identity, specification in document["context"]["fieldsets"].items()
        )
        candidates = document["context"]["ordered_membership_candidates"]

        def resolve_candidate(name):
            return self.resolve(
                fields=fixture_fields,
                fieldsets=fixture_fieldsets,
                memberships=tuple(tuple(item) for item in candidates[name]),
                global_keys=("g1_global_note", "g2_global_tag"),
            )

        expected_section_identities = {
            "A": ("fs_personal", "fs_placement", None),
            "A_reordered": ("fs_placement", "fs_personal", None),
            "with_deprecated": ("fs_personal", "fs_placement", None),
        }
        for candidate, expected in expected_section_identities.items():
            with self.subTest(candidate=candidate):
                result = resolve_candidate(candidate)
                self.assertEqual(
                    tuple(
                        str(section.identity) if section.identity is not None else None
                        for section in result.rendered_sections
                    ),
                    expected,
                )

        duplicate = resolve_candidate("A")
        location = next(
            item
            for section in duplicate.rendered_sections
            for item in section.fields
            if item.key == FieldKey("f3_location")
        )
        self.assertEqual(
            location.contributing_section_identities,
            (QualifiedIdentity("fs_personal"), QualifiedIdentity("fs_placement")),
        )
        self.assertEqual(
            tuple(item.key for item in duplicate.rendered_sections[-1].fields),
            (FieldKey("g1_global_note"), FieldKey("g2_global_tag")),
        )

        with self.assertRaises(ValueError):
            resolve_candidate("duplicate_ordinals")


if __name__ == "__main__":
    unittest.main()
