from collections import defaultdict

import django.core.validators
from django.db import migrations, models
from django.db.models import Count
from django.utils import timezone


class MigrationConflict(RuntimeError):
    """Raised when predecessor data cannot be migrated without guessing."""


def _fail(code, detail=None):
    suffix = f":{detail}" if detail else ""
    raise MigrationConflict(f"issue479:{code}{suffix}")


def _check_identity_duplicates(model, fields, db_alias):
    duplicate_groups = (
        model._base_manager.using(db_alias)
        .values(*fields)
        .annotate(identity_count=Count("pk"))
        .filter(identity_count__gt=1)
    )
    first = duplicate_groups.order_by(*fields).first()
    if first is not None:
        _fail("ambiguous_identity", f"{model._meta.db_table}:{fields}:{first}")


def _check_position_rows(model, owner_field, member_field, max_position, db_alias):
    owner_ids = (
        model._base_manager.using(db_alias)
        .values_list(owner_field, flat=True)
        .distinct()
    )
    for owner_id in owner_ids:
        rows = list(
            model._base_manager.using(db_alias)
            .filter(**{owner_field: owner_id})
            .order_by("position", member_field, "pk")
            .values("pk", "position", member_field)
        )
        if len(rows) > 1_000_000:
            _fail("position_cardinality", f"{model._meta.db_table}:{owner_id}")
        seen_members = set()
        seen_positions = set()
        for row in rows:
            position = row["position"]
            member_id = row[member_field]
            if not isinstance(position, int) or not 1 <= position <= max_position:
                _fail("invalid_position", f"{model._meta.db_table}:{row['pk']}:{position}")
            if member_id in seen_members:
                _fail("duplicate_member", f"{model._meta.db_table}:{owner_id}:{member_id}")
            if position in seen_positions:
                _fail("duplicate_position", f"{model._meta.db_table}:{owner_id}:{position}")
            seen_members.add(member_id)
            seen_positions.add(position)


def _preflight_applicability(apps, db_alias):
    CustomField = apps.get_model("extras", "CustomField")
    ContentType = apps.get_model("contenttypes", "ContentType")
    through = CustomField.object_types.through
    object_type_rows = defaultdict(list)
    for field_id, content_type_id in through._base_manager.using(db_alias).values_list(
        "customfield_id", "contenttype_id"
    ):
        object_type_rows[field_id].append(content_type_id)

    content_types = {
        content_type.pk: content_type
        for content_type in ContentType._base_manager.using(db_alias).filter(
            pk__in={content_type_id for ids in object_type_rows.values() for content_type_id in ids}
        )
    }
    expected_by_scope = {
        "asset_type": {("assets", "assettype")},
        "asset": {("assets", "asset")},
        "both": {("assets", "asset"), ("assets", "assettype")},
    }
    for field in CustomField._base_manager.using(db_alias).order_by("pk"):
        content_type_ids = object_type_rows.get(field.pk, [])
        if not content_type_ids:
            _fail("empty_object_types", field.pk)
        identities = set()
        for content_type_id in content_type_ids:
            content_type = content_types.get(content_type_id)
            if content_type is None:
                _fail("missing_content_type", f"{field.pk}:{content_type_id}")
            identity = (content_type.app_label, content_type.model)
            identities.add(identity)
            try:
                target_model = apps.get_model(*identity)
            except LookupError:
                target_model = None
            if target_model is None:
                _fail("unresolvable_object_type", f"{field.pk}:{content_type.app_label}.{content_type.model}")
        legacy_scope = field.scope
        if legacy_scope not in (None, ""):
            expected = expected_by_scope.get(legacy_scope)
            if expected is None:
                _fail("invalid_legacy_scope", f"{field.pk}:{legacy_scope}")
            if not expected.issubset(identities):
                _fail("scope_object_types_contradiction", field.pk)


def _preflight_lifecycle_and_identity(apps, db_alias):
    definition_models = (
        apps.get_model("extras", "CustomField"),
        apps.get_model("extras", "CustomFieldset"),
        apps.get_model("extras", "CustomFieldChoiceSet"),
        apps.get_model("extras", "CustomFieldChoice"),
    )
    identity_fields = {
        "CustomField": ("name",),
        "CustomFieldset": ("namespace", "slug"),
        "CustomFieldChoiceSet": ("namespace", "slug"),
        "CustomFieldChoice": ("choice_set_id", "key"),
    }
    for model in definition_models:
        _check_identity_duplicates(model, identity_fields[model.__name__], db_alias)
        for definition in model._base_manager.using(db_alias).all().iterator():
            if definition.lifecycle not in {"active", "deprecated", "deleted"}:
                _fail("invalid_lifecycle", f"{model.__name__}:{definition.pk}:{definition.lifecycle}")
            if definition.__class__.__name__ == "CustomFieldset" and (
                not definition.namespace or not definition.slug
            ):
                _fail("missing_fieldset_identity", definition.pk)
            if definition.lifecycle == "active" and definition.deprecated_at is not None:
                _fail("active_with_deprecated_at", f"{model.__name__}:{definition.pk}")
            if definition.deleted_at is not None and definition.deprecated_at is not None:
                if definition.deleted_at != definition.deprecated_at:
                    _fail("ambiguous_deprecation_timestamp", f"{model.__name__}:{definition.pk}")


def _preflight_and_backfill(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    _preflight_applicability(apps, db_alias)
    _preflight_lifecycle_and_identity(apps, db_alias)
    _check_position_rows(
        apps.get_model("extras", "CustomFieldsetField"),
        "fieldset_id",
        "custom_field_id",
        1_000_000,
        db_alias,
    )
    _check_position_rows(
        apps.get_model("extras", "CustomFieldChoice"),
        "choice_set_id",
        "key",
        1_000_000_064,
        db_alias,
    )

    CustomField = apps.get_model("extras", "CustomField")
    CustomFieldsetField = apps.get_model("extras", "CustomFieldsetField")
    now = timezone.now()
    activation_updates = []
    for field in CustomField._base_manager.using(db_alias).all().iterator():
        activation = "composed" if CustomFieldsetField._base_manager.using(db_alias).filter(
            custom_field_id=field.pk
        ).exists() else "global"
        activation_updates.append((field.pk, activation))
    for field_id, activation in activation_updates:
        CustomField._base_manager.using(db_alias).filter(pk=field_id).update(activation=activation)

    for model_name in ("CustomField", "CustomFieldChoice", "CustomFieldChoiceSet", "CustomFieldset"):
        model = apps.get_model("extras", model_name)
        for definition in model._base_manager.using(db_alias).all().iterator():
            if definition.deleted_at is None and definition.lifecycle != "deleted":
                continue
            deprecated_at = definition.deprecated_at or definition.deleted_at or now
            model._base_manager.using(db_alias).filter(pk=definition.pk).update(
                lifecycle="deprecated",
                deprecated_at=deprecated_at,
            )


def _renumber_positions(model, owner_field, member_sort_fields, db_alias):
    owner_ids = model._base_manager.using(db_alias).values_list(owner_field, flat=True).distinct()
    for owner_id in owner_ids:
        rows = list(
            model._base_manager.using(db_alias)
            .filter(**{owner_field: owner_id})
            .order_by("position", *member_sort_fields, "pk")
        )
        for position, row in enumerate(rows, start=1):
            model._base_manager.using(db_alias).filter(pk=row.pk).update(position=position)


def _renumber_definition_positions(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    _renumber_positions(
        apps.get_model("extras", "CustomFieldsetField"),
        "fieldset_id",
        ("custom_field_id",),
        db_alias,
    )
    _renumber_positions(
        apps.get_model("extras", "CustomFieldChoice"),
        "choice_set_id",
        ("key",),
        db_alias,
    )


def refuse_reverse(apps, schema_editor):
    raise MigrationConflict("issue479:reverse_refused")


GUARDS_SQL = """
CREATE OR REPLACE FUNCTION extras_permanent_definition_delete_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'Reusable definition identities are permanent; deprecate the row instead'
        USING ERRCODE = 'check_violation';
    RETURN OLD;
END;
$$;

CREATE TRIGGER extras_customfield_permanent_delete_guard
BEFORE DELETE ON extras_customfield FOR EACH ROW
EXECUTE FUNCTION extras_permanent_definition_delete_guard();
CREATE TRIGGER extras_customfieldset_permanent_delete_guard
BEFORE DELETE ON extras_customfieldset FOR EACH ROW
EXECUTE FUNCTION extras_permanent_definition_delete_guard();
CREATE TRIGGER extras_customfieldchoiceset_permanent_delete_guard
BEFORE DELETE ON extras_customfieldchoiceset FOR EACH ROW
EXECUTE FUNCTION extras_permanent_definition_delete_guard();
CREATE TRIGGER extras_customfieldchoice_permanent_delete_guard
BEFORE DELETE ON extras_customfieldchoice FOR EACH ROW
EXECUTE FUNCTION extras_permanent_definition_delete_guard();

CREATE OR REPLACE FUNCTION extras_customfield_identity_update_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.name IS DISTINCT FROM NEW.name
       OR OLD.namespace IS DISTINCT FROM NEW.namespace THEN
        RAISE EXCEPTION 'Custom Field identity is immutable after creation'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER extras_customfield_identity_update_guard
BEFORE UPDATE OF name, namespace ON extras_customfield FOR EACH ROW
EXECUTE FUNCTION extras_customfield_identity_update_guard();

CREATE OR REPLACE FUNCTION extras_customfieldset_identity_update_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.namespace IS DISTINCT FROM NEW.namespace
       OR OLD.slug IS DISTINCT FROM NEW.slug THEN
        RAISE EXCEPTION 'Custom Fieldset identity is immutable after creation'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER extras_customfieldset_identity_update_guard
BEFORE UPDATE OF namespace, slug ON extras_customfieldset FOR EACH ROW
EXECUTE FUNCTION extras_customfieldset_identity_update_guard();

CREATE OR REPLACE FUNCTION extras_customfieldchoiceset_identity_update_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.namespace IS DISTINCT FROM NEW.namespace
       OR OLD.slug IS DISTINCT FROM NEW.slug THEN
        RAISE EXCEPTION 'Choice Set identity is immutable after creation'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER extras_customfieldchoiceset_identity_update_guard
BEFORE UPDATE OF namespace, slug ON extras_customfieldchoiceset FOR EACH ROW
EXECUTE FUNCTION extras_customfieldchoiceset_identity_update_guard();

CREATE OR REPLACE FUNCTION extras_customfieldchoice_identity_update_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.choice_set_id IS DISTINCT FROM NEW.choice_set_id
       OR OLD.key IS DISTINCT FROM NEW.key THEN
        RAISE EXCEPTION 'Choice identity is immutable after creation'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER extras_customfieldchoice_identity_update_guard
BEFORE UPDATE OF choice_set_id, key ON extras_customfieldchoice FOR EACH ROW
EXECUTE FUNCTION extras_customfieldchoice_identity_update_guard();

CREATE OR REPLACE FUNCTION extras_customfieldsetfield_global_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM extras_customfield
        WHERE id = NEW.custom_field_id AND activation = 'global'
    ) THEN
        RAISE EXCEPTION 'Global Custom Fields cannot join Fieldsets'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER extras_customfieldsetfield_global_guard
BEFORE INSERT OR UPDATE OF custom_field_id ON extras_customfieldsetfield
FOR EACH ROW EXECUTE FUNCTION extras_customfieldsetfield_global_guard();

CREATE OR REPLACE FUNCTION extras_customfield_activation_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.activation = 'global'
       AND EXISTS (
           SELECT 1 FROM extras_customfieldsetfield
           WHERE custom_field_id = NEW.id
       ) THEN
        RAISE EXCEPTION 'A Custom Field with memberships cannot become global'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER extras_customfield_activation_guard
BEFORE UPDATE OF activation ON extras_customfield
FOR EACH ROW EXECUTE FUNCTION extras_customfield_activation_guard();
"""

GUARDS_REVERSE_SQL = """
DROP TRIGGER IF EXISTS extras_customfield_activation_guard ON extras_customfield;
DROP FUNCTION IF EXISTS extras_customfield_activation_guard();
DROP TRIGGER IF EXISTS extras_customfieldsetfield_global_guard ON extras_customfieldsetfield;
DROP FUNCTION IF EXISTS extras_customfieldsetfield_global_guard();
DROP TRIGGER IF EXISTS extras_customfieldchoice_identity_update_guard ON extras_customfieldchoice;
DROP FUNCTION IF EXISTS extras_customfieldchoice_identity_update_guard();
DROP TRIGGER IF EXISTS extras_customfieldchoiceset_identity_update_guard ON extras_customfieldchoiceset;
DROP FUNCTION IF EXISTS extras_customfieldchoiceset_identity_update_guard();
DROP TRIGGER IF EXISTS extras_customfieldset_identity_update_guard ON extras_customfieldset;
DROP FUNCTION IF EXISTS extras_customfieldset_identity_update_guard();
DROP TRIGGER IF EXISTS extras_customfield_identity_update_guard ON extras_customfield;
DROP FUNCTION IF EXISTS extras_customfield_identity_update_guard();
DROP TRIGGER IF EXISTS extras_customfieldchoice_permanent_delete_guard ON extras_customfieldchoice;
DROP TRIGGER IF EXISTS extras_customfieldchoiceset_permanent_delete_guard ON extras_customfieldchoiceset;
DROP TRIGGER IF EXISTS extras_customfieldset_permanent_delete_guard ON extras_customfieldset;
DROP TRIGGER IF EXISTS extras_customfield_permanent_delete_guard ON extras_customfield;
DROP FUNCTION IF EXISTS extras_permanent_definition_delete_guard();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("extras", "0117_alter_customfieldchoice_choice_set_and_more"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.AddField(
            model_name="customfield",
            name="activation",
            field=models.CharField(
                choices=[("composed", "Composed"), ("global", "Global")],
                db_index=True,
                max_length=16,
                null=True,
                verbose_name="Activation",
            ),
        ),
        migrations.RunPython(_preflight_and_backfill, reverse_code=refuse_reverse),
        migrations.RemoveConstraint(
            model_name="customfieldchoice",
            name="unique_customfieldchoice_position",
        ),
        migrations.RemoveConstraint(
            model_name="customfieldsetfield",
            name="unique_customfieldset_position",
        ),
        migrations.RunPython(_renumber_definition_positions, reverse_code=refuse_reverse),
        migrations.RemoveConstraint(
            model_name="customfieldchoice",
            name="customfieldchoice_position_range",
        ),
        migrations.RemoveField(
            model_name="customfield",
            name="scope",
        ),
        migrations.RemoveField(
            model_name="customfield",
            name="deleted_at",
        ),
        migrations.RemoveField(
            model_name="customfieldchoice",
            name="deleted_at",
        ),
        migrations.RemoveField(
            model_name="customfieldchoiceset",
            name="deleted_at",
        ),
        migrations.RemoveField(
            model_name="customfieldset",
            name="deleted_at",
        ),
        migrations.AlterField(
            model_name="customfield",
            name="activation",
            field=models.CharField(
                choices=[("composed", "Composed"), ("global", "Global")],
                db_index=True,
                max_length=16,
                verbose_name="Activation",
            ),
        ),
        migrations.AlterField(
            model_name="customfieldset",
            name="slug",
            field=models.CharField(
                max_length=127,
                validators=[django.core.validators.RegexValidator(r"^[a-z0-9][a-z0-9._-]{0,126}$")],
            ),
        ),
        migrations.AddConstraint(
            model_name="customfield",
            constraint=models.CheckConstraint(
                condition=models.Q(activation__in=["composed", "global"]),
                name="customfield_activation_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="customfieldchoice",
            constraint=models.UniqueConstraint(
                deferrable=models.Deferrable.DEFERRED,
                fields=("choice_set", "position"),
                name="unique_customfieldchoice_position",
            ),
        ),
        migrations.AddConstraint(
            model_name="customfieldchoice",
            constraint=models.CheckConstraint(
                condition=models.Q(position__gte=1, position__lte=1000000),
                name="customfieldchoice_position_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="customfieldsetfield",
            constraint=models.UniqueConstraint(
                deferrable=models.Deferrable.DEFERRED,
                fields=("fieldset", "position"),
                name="unique_customfieldset_position",
            ),
        ),
        migrations.RunSQL(GUARDS_SQL, reverse_sql=GUARDS_REVERSE_SQL),
    ]
