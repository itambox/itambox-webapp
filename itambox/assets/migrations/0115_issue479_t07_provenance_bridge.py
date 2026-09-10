import hashlib
import json
import re
from copy import deepcopy

from django.db import migrations, models, transaction
from django.db.models.deletion import PROTECT
from django.utils import timezone

_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_RESERVED_LIBRARY_NAMESPACES = {"itambox", "catalog"}
_BATCH_SIZE = 1000


def _connector_identity_token(source_url, source_id):
    payload = json.dumps(
        {"source_url": source_url, "source_id": source_id},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _asset_type_connector_identity(managed_paths):
    snipeit = managed_paths.get("snipeit")
    if not isinstance(snipeit, dict):
        return None
    source_url = snipeit.get("source_url")
    source_id = snipeit.get("source_id")
    if not isinstance(source_url, str) or not source_url:
        return None
    if not isinstance(source_id, str) or not source_id:
        return None
    return _connector_identity_token(source_url, source_id)


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


def _validate_library_namespace(namespace, owner):
    _validate_namespace(namespace, owner)
    if namespace in _RESERVED_LIBRARY_NAMESPACES:
        _fail("reserved_namespace", f"{owner}:{namespace}")


def _validate_managed_paths(value, owner):
    if not isinstance(value, dict):
        _fail("unpreservable_managed_paths", owner)


def _has_source_evidence(release, source_checksum, managed_paths, reconciled_at):
    return any(value not in (None, "", {}) for value in (release, source_checksum, managed_paths, reconciled_at))


def _disposition(release, source_checksum, managed_paths, reconciled_at):
    return (
        "unreconciled"
        if _has_source_evidence(release, source_checksum, managed_paths, reconciled_at)
        else "uninitialized"
    )


def _source_tuple(row):
    return (
        getattr(row, "source_checksum", None),
        deepcopy(getattr(row, "managed_paths", {})),
        getattr(row, "last_reconciled_at", None),
    )


def _preflight(apps, db_alias):
    AssetTypeLibrary = apps.get_model("assets", "AssetTypeLibrary")
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

    old_libraries = list(AssetTypeLibrary._base_manager.using(db_alias).order_by("pk"))
    libraries_by_id = {}
    required_namespaces = set()
    for library in old_libraries:
        _validate_library_namespace(library.namespace, f"library:{library.pk}")
        _validate_managed_paths(library.managed_paths, f"library:{library.pk}")
        libraries_by_id[library.pk] = library
        required_namespaces.add(library.namespace)

    asset_types = list(AssetType._base_manager.using(db_alias).order_by("pk"))
    for asset_type in asset_types:
        if asset_type.management_kind not in {"core", "library", "local"}:
            _fail("invalid_asset_type_management_kind", asset_type.pk)
        _validate_managed_paths(asset_type.managed_paths, f"asset_type:{asset_type.pk}")
        if asset_type.management_kind == "library":
            if asset_type.library_id not in libraries_by_id:
                _fail("missing_asset_type_library", asset_type.pk)
            if not asset_type.library_definition_key:
                _fail("missing_asset_type_definition_key", asset_type.pk)
            if not re.fullmatch(r"^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*$", asset_type.library_definition_key):
                _fail("invalid_asset_type_definition_key", asset_type.pk)
        elif asset_type.library_id is not None or asset_type.library_definition_key not in (None, ""):
            _fail("asset_type_identity_management_mismatch", asset_type.pk)

    definition_rows = (
        (CustomField, "custom_field"),
        (CustomFieldset, "custom_fieldset"),
        (CustomFieldChoiceSet, "choice_set"),
    )
    for model, owner_kind in definition_rows:
        for definition in model._base_manager.using(db_alias).order_by("pk"):
            _validate_namespace(definition.namespace, f"{owner_kind}:{definition.pk}")
            _validate_managed_paths(definition.managed_paths, f"{owner_kind}:{definition.pk}")
            if definition.management_kind not in {"core", "library", "local"}:
                _fail("invalid_definition_management_kind", f"{owner_kind}:{definition.pk}")
            if definition.management_kind == "library":
                _validate_library_namespace(definition.namespace, f"{owner_kind}:{definition.pk}")
                required_namespaces.add(definition.namespace)

    choice_sets = {
        choice_set.pk: choice_set for choice_set in CustomFieldChoiceSet._base_manager.using(db_alias).order_by("pk")
    }
    for choice in CustomFieldChoice._base_manager.using(db_alias).order_by("pk"):
        choice_set = choice_sets.get(choice.choice_set_id)
        if choice_set is None:
            _fail("missing_choice_set", choice.pk)
        _validate_managed_paths(choice.managed_paths, f"choice:{choice.pk}")
        if choice.management_kind != choice_set.management_kind:
            _fail("choice_ownership_mismatch", choice.pk)
        if _source_tuple(choice) != _source_tuple(choice_set):
            _fail("choice_source_mismatch", choice.pk)

    return {
        "asset_type_libraries": old_libraries,
        "asset_types": asset_types,
        "definitions": {
            owner_kind: list(model._base_manager.using(db_alias).order_by("pk"))
            for model, owner_kind in definition_rows
        },
        "choices": list(CustomFieldChoice._base_manager.using(db_alias).order_by("pk")),
        "choice_sets": choice_sets,
        "required_namespaces": required_namespaces,
    }


def _reset_specification_library_sequence(schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT setval(
                pg_get_serial_sequence('extras_specificationlibrary', 'id'),
                COALESCE(MAX(id), 1),
                MAX(id) IS NOT NULL
            )
            FROM extras_specificationlibrary
            """
        )


def _bulk_create(model, rows, db_alias):
    for start in range(0, len(rows), _BATCH_SIZE):
        model._base_manager.using(db_alias).bulk_create(rows[start : start + _BATCH_SIZE], batch_size=_BATCH_SIZE)


def _legacy_row(
    LegacyProvenance,
    *,
    library_id,
    owner_kind,
    owner_id,
    owner_namespace,
    release,
    source_checksum,
    managed_paths,
    reconciled_at,
    captured_at,
):
    return LegacyProvenance(
        library_id=library_id,
        owner_kind=owner_kind,
        owner_id=owner_id,
        owner_namespace=owner_namespace,
        legacy_release=release,
        legacy_source_checksum=source_checksum,
        legacy_managed_paths=deepcopy(managed_paths),
        legacy_last_reconciled_at=reconciled_at,
        disposition=_disposition(release, source_checksum, managed_paths, reconciled_at),
        captured_at=captured_at,
    )


def forward_bridge(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    with transaction.atomic(using=db_alias):
        context = _preflight(apps, db_alias)
        AssetTypeLibrary = apps.get_model("assets", "AssetTypeLibrary")
        AssetType = apps.get_model("assets", "AssetType")
        SpecificationLibrary = apps.get_model("extras", "SpecificationLibrary")
        LegacyProvenance = apps.get_model("extras", "SpecificationLibraryLegacyProvenance")
        captured_at = timezone.now()

        old_libraries = context["asset_type_libraries"]
        copied = [
            SpecificationLibrary(
                pk=library.pk,
                namespace=library.namespace,
                label="",
                installed_at=library.installed_at,
            )
            for library in old_libraries
        ]
        _bulk_create(SpecificationLibrary, copied, db_alias)
        _reset_specification_library_sequence(schema_editor)

        libraries_by_namespace = {
            library.namespace: library for library in SpecificationLibrary._base_manager.using(db_alias).order_by("pk")
        }
        created_anchor_ids = set()
        for namespace in sorted(context["required_namespaces"]):
            if namespace in libraries_by_namespace:
                continue
            library = SpecificationLibrary._base_manager.using(db_alias).create(
                namespace=namespace,
                label="",
                installed_at=captured_at,
            )
            libraries_by_namespace[namespace] = library
            created_anchor_ids.add(library.pk)

        for old_library in old_libraries:
            SpecificationLibrary._base_manager.using(db_alias).filter(pk=old_library.pk).update(
                created_at=old_library.created_at,
                updated_at=old_library.updated_at,
                installed_at=old_library.installed_at,
            )

        for owner_kind, rows in context["definitions"].items():
            model = apps.get_model(
                "extras",
                {
                    "custom_field": "CustomField",
                    "custom_fieldset": "CustomFieldset",
                    "choice_set": "CustomFieldChoiceSet",
                }[owner_kind],
            )
            for definition in rows:
                update_values = {
                    "connector_identity": (
                        definition.source_checksum if definition.management_kind == "local" else None
                    ),
                }
                if definition.management_kind == "library":
                    update_values["library_id"] = libraries_by_namespace[definition.namespace].pk
                model._base_manager.using(db_alias).filter(pk=definition.pk).update(**update_values)

        for asset_type in context["asset_types"]:
            connector_identity = (
                _asset_type_connector_identity(asset_type.managed_paths)
                if asset_type.management_kind == "local"
                else None
            )
            AssetType._base_manager.using(db_alias).filter(pk=asset_type.pk).update(
                connector_identity=connector_identity
            )

        captured_rows = []
        for library in libraries_by_namespace.values():
            if library.pk not in created_anchor_ids:
                continue
            captured_rows.append(
                _legacy_row(
                    LegacyProvenance,
                    library_id=library.pk,
                    owner_kind="library",
                    owner_id=library.pk,
                    owner_namespace=library.namespace,
                    release=None,
                    source_checksum=None,
                    managed_paths={},
                    reconciled_at=None,
                    captured_at=captured_at,
                )
            )
        for library in old_libraries:
            captured_rows.append(
                _legacy_row(
                    LegacyProvenance,
                    library_id=libraries_by_namespace[library.namespace].pk,
                    owner_kind="library",
                    owner_id=library.pk,
                    owner_namespace=library.namespace,
                    release=library.release,
                    source_checksum=library.source_checksum,
                    managed_paths=library.managed_paths,
                    reconciled_at=library.last_reconciled_at,
                    captured_at=captured_at,
                )
            )

        for asset_type in context["asset_types"]:
            library_id = None
            owner_namespace = ""
            if asset_type.library_id is not None:
                old_library = next(library for library in old_libraries if library.pk == asset_type.library_id)
                library_id = libraries_by_namespace[old_library.namespace].pk
                owner_namespace = old_library.namespace
            captured_rows.append(
                _legacy_row(
                    LegacyProvenance,
                    library_id=library_id,
                    owner_kind="asset_type",
                    owner_id=asset_type.pk,
                    owner_namespace=owner_namespace,
                    release=asset_type.library_release,
                    source_checksum=asset_type.source_checksum,
                    managed_paths=asset_type.managed_paths,
                    reconciled_at=asset_type.last_reconciled_at,
                    captured_at=captured_at,
                )
            )

        for owner_kind, rows in context["definitions"].items():
            for definition in rows:
                library_id = None
                if definition.management_kind == "library":
                    library_id = libraries_by_namespace[definition.namespace].pk
                captured_rows.append(
                    _legacy_row(
                        LegacyProvenance,
                        library_id=library_id,
                        owner_kind=owner_kind,
                        owner_id=definition.pk,
                        owner_namespace=definition.namespace,
                        release=None,
                        source_checksum=definition.source_checksum,
                        managed_paths=definition.managed_paths,
                        reconciled_at=definition.last_reconciled_at,
                        captured_at=captured_at,
                    )
                )

        for choice in context["choices"]:
            choice_set = context["choice_sets"][choice.choice_set_id]
            library_id = None
            if choice_set.management_kind == "library":
                library_id = libraries_by_namespace[choice_set.namespace].pk
            captured_rows.append(
                _legacy_row(
                    LegacyProvenance,
                    library_id=library_id,
                    owner_kind="choice",
                    owner_id=choice.pk,
                    owner_namespace=choice_set.namespace,
                    release=None,
                    source_checksum=choice.source_checksum,
                    managed_paths=choice.managed_paths,
                    reconciled_at=choice.last_reconciled_at,
                    captured_at=captured_at,
                )
            )

        _bulk_create(LegacyProvenance, captured_rows, db_alias)
        expected_count = len(captured_rows)
        actual_count = LegacyProvenance._base_manager.using(db_alias).count()
        if actual_count != expected_count:
            _fail("legacy_row_count_mismatch", f"{actual_count}!={expected_count}")


def reverse_refused(apps, schema_editor):
    raise MigrationConflict("issue479:t07:reverse_refused")


DROP_OLD_ASSET_TYPE_GUARD_SQL = """
DROP TRIGGER IF EXISTS assets_assettype_library_identity_guard ON assets_assettype;
DROP FUNCTION IF EXISTS assets_assettype_library_identity_guard();
"""

ASSET_TYPE_GUARD_SQL = """
CREATE OR REPLACE FUNCTION assettype_specification_identity_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE'
       AND (OLD.library_id IS DISTINCT FROM NEW.library_id
            OR OLD.library_definition_key IS DISTINCT FROM NEW.library_definition_key
            OR OLD.connector_identity IS DISTINCT FROM NEW.connector_identity) THEN
        RAISE EXCEPTION 'Asset Type specification identity is immutable after creation'
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' AND OLD.management_kind IN ('core', 'library') THEN
        RAISE EXCEPTION 'Core and library Asset Type identities cannot be hard-deleted'
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER assettype_specification_identity_guard
BEFORE UPDATE OF library_id, library_definition_key, connector_identity OR DELETE
ON assets_assettype
FOR EACH ROW
EXECUTE FUNCTION assettype_specification_identity_guard();
"""

ASSET_TYPE_GUARD_REVERSE_SQL = """
DROP TRIGGER IF EXISTS assettype_specification_identity_guard ON assets_assettype;
DROP FUNCTION IF EXISTS assettype_specification_identity_guard();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0114_issue479_t06_composition_schema"),
        ("extras", "0119_issue479_t07_provenance_schema"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.AddField(
            model_name="assettype",
            name="connector_identity",
            field=models.CharField(blank=True, db_index=True, max_length=71, null=True),
        ),
        migrations.RunPython(forward_bridge, reverse_code=reverse_refused),
        migrations.RemoveConstraint(
            model_name="assettype",
            name="assettype_library_identity_complete",
        ),
        migrations.RemoveConstraint(
            model_name="assettype",
            name="assettype_management_library_coherence",
        ),
        migrations.RemoveConstraint(
            model_name="assettype",
            name="unique_assettype_library_identity",
        ),
        migrations.RunSQL(DROP_OLD_ASSET_TYPE_GUARD_SQL, reverse_sql=migrations.RunSQL.noop),
        migrations.AlterField(
            model_name="assettype",
            name="library",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=PROTECT,
                related_name="asset_types",
                to="extras.specificationlibrary",
            ),
        ),
        migrations.RemoveField(
            model_name="assettype",
            name="library_release",
        ),
        migrations.RemoveField(
            model_name="assettype",
            name="source_checksum",
        ),
        migrations.RemoveField(
            model_name="assettype",
            name="managed_paths",
        ),
        migrations.RemoveField(
            model_name="assettype",
            name="last_reconciled_at",
        ),
        migrations.DeleteModel(
            name="AssetTypeLibrary",
        ),
        migrations.AddConstraint(
            model_name="assettype",
            constraint=models.UniqueConstraint(
                fields=("library", "library_definition_key"),
                name="assettype_library_identity_uq",
            ),
        ),
        migrations.AddConstraint(
            model_name="assettype",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(library__isnull=True, library_definition_key__isnull=True)
                    | models.Q(library__isnull=False, library_definition_key__isnull=False)
                ),
                name="assettype_library_identity_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="assettype",
            constraint=models.CheckConstraint(
                condition=(
                    (
                        models.Q(management_kind="library")
                        & models.Q(library__isnull=False)
                        & models.Q(library_definition_key__isnull=False)
                        & ~models.Q(library_definition_key="")
                    )
                    | (
                        models.Q(management_kind__in=["core", "local"])
                        & models.Q(library__isnull=True)
                        & models.Q(library_definition_key__isnull=True)
                    )
                ),
                name="assettype_library_mgmt_ck",
            ),
        ),
        migrations.RunSQL(ASSET_TYPE_GUARD_SQL, reverse_sql=ASSET_TYPE_GUARD_REVERSE_SQL),
    ]
