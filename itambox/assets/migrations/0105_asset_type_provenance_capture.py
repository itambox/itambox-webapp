"""Normalized #479 legacy provenance capture.

Captures the legacy evidence state of every existing Asset Type and reusable
definition as an append-only ``SpecificationLibraryLegacyProvenance`` record
before the transitional columns are removed.

Supported upgrade states carry no library-managed ownership and no
reconciliation evidence, so every captured record is an ``uninitialized``
archive row that freezes the owner namespace. Reverse is refused because the
captured evidence is not recoverable.
"""

import re

from django.db import migrations, transaction
from django.utils import timezone

_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_BATCH_SIZE = 1000


class MigrationConflict(RuntimeError):
    pass


def _fail(code, detail=None):
    suffix = f":{detail}" if detail is not None else ""
    raise MigrationConflict(f"issue479:t07:{code}{suffix}")


def _validate_namespace(namespace, owner):
    if not isinstance(namespace, str) or not _NAMESPACE_RE.fullmatch(namespace):
        _fail("invalid_namespace", f"{owner}:{namespace!r}")
    if len(namespace) > 62:
        _fail("namespace_too_long", owner)


def _preflight(apps, db_alias):
    AssetType = apps.get_model("assets", "AssetType")
    CustomField = apps.get_model("extras", "CustomField")
    CustomFieldset = apps.get_model("extras", "CustomFieldset")
    CustomFieldChoiceSet = apps.get_model("extras", "CustomFieldChoiceSet")
    CustomFieldChoice = apps.get_model("extras", "CustomFieldChoice")
    SpecificationLibrary = apps.get_model("extras", "SpecificationLibrary")
    SpecificationLibraryRelease = apps.get_model("extras", "SpecificationLibraryRelease")
    LegacyProvenance = apps.get_model("extras", "SpecificationLibraryLegacyProvenance")

    if SpecificationLibrary._base_manager.using(db_alias).exists():
        _fail("nonempty_new_library_table")
    if SpecificationLibraryRelease._base_manager.using(db_alias).exists():
        _fail("nonempty_new_release_table")
    if LegacyProvenance._base_manager.using(db_alias).exists():
        _fail("nonempty_new_legacy_table")

    asset_types = list(AssetType._base_manager.using(db_alias).order_by("pk"))
    for asset_type in asset_types:
        if asset_type.management_kind not in {"core", "library", "local"}:
            _fail("invalid_asset_type_management_kind", asset_type.pk)
        if asset_type.management_kind == "library":
            _fail("missing_asset_type_library", asset_type.pk)
        if asset_type.library_id is not None or asset_type.library_definition_key not in (None, ""):
            _fail("asset_type_identity_management_mismatch", asset_type.pk)

    definition_rows = (
        (CustomField, "custom_field"),
        (CustomFieldset, "custom_fieldset"),
        (CustomFieldChoiceSet, "choice_set"),
    )
    definitions = {}
    for model, owner_kind in definition_rows:
        rows = list(model._base_manager.using(db_alias).order_by("pk"))
        definitions[owner_kind] = rows
        for definition in rows:
            _validate_namespace(definition.namespace, f"{owner_kind}:{definition.pk}")
            if definition.management_kind not in {"core", "library", "local"}:
                _fail("invalid_definition_management_kind", f"{owner_kind}:{definition.pk}")
            if definition.management_kind == "library":
                _fail("missing_definition_library", f"{owner_kind}:{definition.pk}")

    choice_sets = {
        choice_set.pk: choice_set for choice_set in CustomFieldChoiceSet._base_manager.using(db_alias).order_by("pk")
    }
    choices = list(CustomFieldChoice._base_manager.using(db_alias).order_by("pk"))
    for choice in choices:
        if choice.choice_set_id not in choice_sets:
            _fail("missing_choice_set", choice.pk)

    return {
        "asset_types": asset_types,
        "definitions": definitions,
        "choices": choices,
        "choice_sets": choice_sets,
    }


def _legacy_row(LegacyProvenance, *, owner_kind, owner_id, owner_namespace, captured_at):
    return LegacyProvenance(
        library_id=None,
        owner_kind=owner_kind,
        owner_id=owner_id,
        owner_namespace=owner_namespace,
        legacy_release=None,
        legacy_source_checksum=None,
        legacy_managed_paths={},
        legacy_last_reconciled_at=None,
        disposition="uninitialized",
        captured_at=captured_at,
    )


def forward_capture(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    with transaction.atomic(using=db_alias):
        context = _preflight(apps, db_alias)
        LegacyProvenance = apps.get_model("extras", "SpecificationLibraryLegacyProvenance")
        captured_at = timezone.now()

        captured_rows = [
            _legacy_row(
                LegacyProvenance,
                owner_kind="asset_type",
                owner_id=asset_type.pk,
                owner_namespace="",
                captured_at=captured_at,
            )
            for asset_type in context["asset_types"]
        ]
        for owner_kind, rows in context["definitions"].items():
            captured_rows.extend(
                _legacy_row(
                    LegacyProvenance,
                    owner_kind=owner_kind,
                    owner_id=definition.pk,
                    owner_namespace=definition.namespace,
                    captured_at=captured_at,
                )
                for definition in rows
            )
        captured_rows.extend(
            _legacy_row(
                LegacyProvenance,
                owner_kind="choice",
                owner_id=choice.pk,
                owner_namespace=context["choice_sets"][choice.choice_set_id].namespace,
                captured_at=captured_at,
            )
            for choice in context["choices"]
        )

        for start in range(0, len(captured_rows), _BATCH_SIZE):
            LegacyProvenance._base_manager.using(db_alias).bulk_create(
                captured_rows[start : start + _BATCH_SIZE], batch_size=_BATCH_SIZE
            )
        expected_count = len(captured_rows)
        actual_count = LegacyProvenance._base_manager.using(db_alias).count()
        if actual_count != expected_count:
            _fail("legacy_row_count_mismatch", f"{actual_count}!={expected_count}")


def reverse_refused(apps, schema_editor):
    raise MigrationConflict("issue479:t07:reverse_refused")


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0104_asset_type_core_vocabulary"),
        ("extras", "0114_asset_type_definition_library_schema"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.RunPython(forward_capture, reverse_refused),
    ]
