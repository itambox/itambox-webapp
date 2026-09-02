import hashlib
import re
import unicodedata
from collections import Counter

import django.core.validators
from django.db import migrations, models, transaction


class MigrationConflict(RuntimeError):
    pass


def _fail(code):
    raise MigrationConflict(f"issue479:{code}")


def _encoded_component(value):
    if value is None:
        return b"\x00"
    encoded = str(value).encode("utf-8")
    return b"\x01" if not encoded else str(len(encoded)).encode("ascii") + b":" + encoded


def _stable_slug(kind, visible, values, force_hash=False):
    decomposed = unicodedata.normalize("NFKD", visible)
    ascii_value = "".join(char for char in decomposed if ord(char) < 128 and not unicodedata.combining(char))
    folded = ascii_value.casefold()
    normalized = re.sub(r"[^a-z0-9]+", "-", folded).strip("-")
    needs_hash = force_hash or normalized != folded.strip("-") or ascii_value != visible or len(normalized) > 96
    normalized = normalized[:96].rstrip("-")
    digest = hashlib.sha256(b"\x1f".join(_encoded_component(value) for value in (kind, *values))).hexdigest()[:12]
    if not normalized:
        return f"h{digest}"
    return f"{normalized[:112].rstrip('-')}-h{digest}" if needs_hash else normalized


def _choice_key(label, used):
    base = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii").casefold()
    base = re.sub(r"[^a-z0-9_]+", "_", base).strip("_")
    candidate = base[:63]
    if not candidate or candidate in used:
        digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:8]
        candidate = f"{(base[:54] or 'choice').rstrip('_')}_{digest}"
    ordinal = 0
    while candidate in used:
        ordinal += 1
        digest = hashlib.sha256(f"{label}\x1f{ordinal}".encode("utf-8")).hexdigest()[:8]
        candidate = f"{(base[:54] or 'choice').rstrip('_')}_{digest}"
    used.add(candidate)
    return candidate


def _backfill_fieldset_slugs(CustomFieldset, db_alias):
    manager = CustomFieldset._base_manager.using(db_alias)
    fieldsets = list(manager.order_by("pk"))
    pending = [fieldset for fieldset in fieldsets if not fieldset.slug]
    if not pending:
        return
    occupied = {fieldset.slug for fieldset in fieldsets if fieldset.slug}
    base_counts = Counter(_stable_slug("fieldset", fieldset.name, (fieldset.name, fieldset.pk)) for fieldset in pending)
    for fieldset in pending:
        base = _stable_slug("fieldset", fieldset.name, (fieldset.name, fieldset.pk))
        slug = _stable_slug(
            "fieldset",
            fieldset.name,
            (fieldset.name, fieldset.pk),
            force_hash=base_counts[base] > 1 or base in occupied,
        )
        if slug in occupied:
            _fail("fieldset_slug_collision")
        manager.filter(pk=fieldset.pk).update(
            namespace=fieldset.namespace or "local",
            slug=slug,
            label=fieldset.label or fieldset.name,
        )
        occupied.add(slug)


def _legacy_choice_value(value, label_to_key):
    if value in (None, ""):
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized not in label_to_key:
            _fail("unknown_choice")
        return label_to_key[normalized]
    if isinstance(value, list):
        converted = [_legacy_choice_value(item, label_to_key) for item in value]
        if len(converted) != len(set(converted)):
            _fail("duplicate_choice_value")
        return converted
    _fail("legacy_select_value_type")


def _rewrite_legacy_choice_values(apps, db_alias, conversions):
    for model in apps.get_models():
        if not any(field.name == "custom_field_data" for field in model._meta.concrete_fields):
            continue
        manager = model._base_manager.using(db_alias)
        for row in manager.all().iterator():
            data = row.custom_field_data
            if not isinstance(data, dict):
                _fail("invalid_json_store")
            rewritten = dict(data)
            changed = False
            for field_name, label_to_key in conversions.items():
                if field_name not in data:
                    continue
                value = _legacy_choice_value(data[field_name], label_to_key)
                rewritten[field_name] = value
                changed = changed or value != data[field_name]
            if changed:
                manager.filter(pk=row.pk).update(custom_field_data=rewritten)


def _backfill_legacy_choices(apps, db_alias):
    CustomField = apps.get_model("extras", "CustomField")
    CustomFieldChoiceSet = apps.get_model("extras", "CustomFieldChoiceSet")
    CustomFieldChoice = apps.get_model("extras", "CustomFieldChoice")
    conversions = {}
    fields = list(
        CustomField._base_manager.using(db_alias).filter(field_type="select", choice_set__isnull=True).order_by("pk")
    )
    for field in fields:
        labels = [value.strip() for value in (field.choices or "").splitlines() if value.strip()]
        if len(labels) > 64 or len(labels) != len(set(labels)):
            _fail("choice_definition")
        slug = _stable_slug("choice-set", f"{field.name}-choices", (field.name, field.pk))
        if CustomFieldChoiceSet._base_manager.using(db_alias).filter(namespace="local", slug=slug).exists():
            _fail("choice_set_collision")
        choice_set = CustomFieldChoiceSet._base_manager.using(db_alias).create(
            namespace="local",
            slug=slug,
            label=f"{field.label} choices",
            management_kind="local",
            version=1,
            lifecycle="active",
        )
        label_to_key = {}
        used_keys = set()
        for index, label in enumerate(labels, start=1):
            key = _choice_key(label, used_keys)
            label_to_key[label] = key
            CustomFieldChoice._base_manager.using(db_alias).create(
                choice_set_id=choice_set.pk,
                key=key,
                label=label,
                position=index * 10,
                management_kind="local",
                version=1,
                lifecycle="active",
            )
        conversions[field.name] = label_to_key
        CustomField._base_manager.using(db_alias).filter(pk=field.pk).update(
            field_type="single-select",
            choice_set_id=choice_set.pk,
            max_values=1,
            nullable=field.nullable or any(not value for value in labels),
        )
    _rewrite_legacy_choice_values(apps, db_alias, conversions)


def _backfill_legacy_memberships(CustomFieldset, CustomFieldsetField, db_alias):
    for fieldset in CustomFieldset._base_manager.using(db_alias).all().iterator():
        existing_ids = set(
            CustomFieldsetField._base_manager.using(db_alias)
            .filter(fieldset_id=fieldset.pk)
            .values_list("custom_field_id", flat=True)
        )
        next_position = (
            CustomFieldsetField._base_manager.using(db_alias)
            .filter(fieldset_id=fieldset.pk)
            .order_by("-position")
            .values_list("position", flat=True)
            .first()
            or 0
        )
        legacy_ids = fieldset.legacy_fields.using(db_alias).order_by("pk").values_list("pk", flat=True)
        for field_id in legacy_ids:
            if field_id in existing_ids:
                continue
            next_position += 10
            CustomFieldsetField._base_manager.using(db_alias).create(
                fieldset_id=fieldset.pk,
                custom_field_id=field_id,
                position=next_position,
            )
            existing_ids.add(field_id)


def backfill_legacy_definition_rows(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    with transaction.atomic(using=db_alias):
        CustomFieldset = apps.get_model("extras", "CustomFieldset")
        CustomFieldsetField = apps.get_model("extras", "CustomFieldsetField")
        _backfill_fieldset_slugs(CustomFieldset, db_alias)
        _backfill_legacy_choices(apps, db_alias)
        _backfill_legacy_memberships(CustomFieldset, CustomFieldsetField, db_alias)
        if CustomFieldset._base_manager.using(db_alias).filter(slug__isnull=True).exists():
            _fail("fieldset_slug_missing")


def reverse_refused(apps, schema_editor):
    raise MigrationConflict("issue479:reverse_refused")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("assets", "0107_asset_type_library_contract"),
        ("extras", "0114_asset_type_definition_schema"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.RunPython(backfill_legacy_definition_rows, reverse_code=reverse_refused),
        migrations.AlterField(
            model_name="customfieldset",
            name="slug",
            field=models.CharField(
                max_length=127,
                validators=[django.core.validators.RegexValidator(r"^[a-z0-9][a-z0-9._-]{0,126}$")],
            ),
        ),
        migrations.RemoveConstraint(
            model_name="customfield",
            name="unique_customfield_name_active",
        ),
        migrations.AddConstraint(
            model_name="customfield",
            constraint=models.UniqueConstraint(fields=("name",), name="unique_customfield_name"),
        ),
        migrations.RemoveConstraint(
            model_name="customfieldset",
            name="unique_customfieldset_name_active",
        ),
        migrations.RemoveField(
            model_name="customfield",
            name="choices",
        ),
        migrations.RemoveField(
            model_name="customfield",
            name="legacy_source_key",
        ),
        migrations.RemoveField(
            model_name="customfield",
            name="legacy_source_signature",
        ),
        migrations.RemoveField(
            model_name="customfieldset",
            name="legacy_fields",
        ),
        migrations.RemoveField(
            model_name="customfieldset",
            name="name",
        ),
        migrations.RunPython(migrations.RunPython.noop, reverse_code=reverse_refused),
    ]
