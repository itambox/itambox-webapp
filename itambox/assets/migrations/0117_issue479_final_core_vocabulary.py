from decimal import Decimal

from django.db import migrations, transaction
from django.utils import timezone

from ._t11_core_vocabulary import CORE_VOCABULARY


class MigrationConflict(RuntimeError):
    """Raised when the reviewed core delta would take ownership of local data."""


def _fail(code, detail=None):
    suffix = f":{detail}" if detail is not None else ""
    raise MigrationConflict(f"issue479:t11:{code}{suffix}")


def _key(identity):
    return identity.rsplit("/", 1)[1]


def _field_rows():
    return tuple(CORE_VOCABULARY["active_fields"]) + tuple(CORE_VOCABULARY["reserved_retired_fields"])


def _expected_field_keys():
    return {_key(row["identity"]) for row in _field_rows()}


def _expected_section_slugs():
    return {_key(row["identity"]) for row in CORE_VOCABULARY["sections"]}


def _expected_choice_set_slugs():
    return {_key(row["identity"]) for row in CORE_VOCABULARY["choice_sets"]}


def _content_type_ids(ContentType, db_alias, targets):
    model_names = {"asset_type": "assettype", "asset": "asset"}
    ids = []
    for target in targets:
        try:
            model_name = model_names[target]
        except KeyError:
            _fail("unknown_target", target)
        content_type, _ = ContentType._base_manager.using(db_alias).get_or_create(
            app_label="assets",
            model=model_name,
        )
        ids.append(content_type.pk)
    return tuple(ids)


def _ensure_choice_sets(apps, db_alias):
    ChoiceSet = apps.get_model("extras", "CustomFieldChoiceSet")
    choice_sets = {}
    expected_slugs = _expected_choice_set_slugs()
    for choice_set in ChoiceSet._base_manager.using(db_alias).filter(namespace="itambox"):
        if choice_set.management_kind == "core" and choice_set.slug not in expected_slugs:
            _fail("unexpected_core_choice_set", choice_set.slug)

    for expected in CORE_VOCABULARY["choice_sets"]:
        slug = expected["slug"]
        rows = list(
            ChoiceSet._base_manager.using(db_alias)
            .filter(namespace="itambox", slug=slug)
            .order_by("pk")
        )
        if len(rows) > 1:
            _fail("choice_set_identity_collision", slug)
        if rows:
            choice_set = rows[0]
            if (
                choice_set.management_kind != "core"
                or choice_set.lifecycle != "active"
                or choice_set.library_id is not None
            ):
                _fail("choice_set_identity_collision", slug)
        else:
            choice_set = ChoiceSet._base_manager.using(db_alias).create(
                namespace="itambox",
                slug=slug,
                label=expected["label"],
                management_kind="core",
                version=1,
                lifecycle=expected["lifecycle"],
                deprecated_at=None,
                replaced_by=None,
                library_id=None,
                connector_identity=None,
            )
        ChoiceSet._base_manager.using(db_alias).filter(pk=choice_set.pk).update(
            label=expected["label"],
            management_kind="core",
            version=1,
            lifecycle=expected["lifecycle"],
            deprecated_at=None,
            replaced_by=None,
            library_id=None,
            connector_identity=None,
        )
        choice_sets[slug] = choice_set
    return choice_sets


def _reconcile_choices(apps, db_alias, choice_sets, now):
    Choice = apps.get_model("extras", "CustomFieldChoice")
    for expected_set in CORE_VOCABULARY["choice_sets"]:
        choice_set = choice_sets[expected_set["slug"]]
        expected_choices = {choice["key"]: choice for choice in expected_set["choices"]}
        existing = list(
            Choice._base_manager.using(db_alias)
            .filter(choice_set_id=choice_set.pk)
            .order_by("pk")
        )
        existing_keys = {choice.key for choice in existing}
        unexpected_keys = existing_keys - expected_choices.keys()
        if unexpected_keys:
            _fail("choice_set_membership_collision", f"{expected_set['slug']}:{sorted(unexpected_keys)}")
        for expected_choice in expected_set["choices"]:
            key = expected_choice["key"]
            existing_choice = next((choice for choice in existing if choice.key == key), None)
            if (
                existing_choice is not None
                and expected_choice["lifecycle"] == "active"
                and existing_choice.lifecycle != "active"
            ):
                _fail("choice_lifecycle_collision", f"{expected_set['slug']}:{key}")
            deprecated_at = None
            if expected_choice["lifecycle"] == "deprecated":
                deprecated_at = (existing_choice.deprecated_at if existing_choice else None) or now
            values = {
                "label": expected_choice["label"],
                "position": expected_choice["position"],
                "version": 1,
                "lifecycle": expected_choice["lifecycle"],
                "deprecated_at": deprecated_at,
                "replaced_by": None,
            }
            if existing_choice is None:
                Choice._base_manager.using(db_alias).create(
                    choice_set_id=choice_set.pk,
                    key=key,
                    **values,
                )
            else:
                Choice._base_manager.using(db_alias).filter(pk=existing_choice.pk).update(**values)


def _field_values(expected, choice_set_id, deprecated_at):
    validation = expected["validation"]
    return {
        "namespace": "itambox",
        "label": expected["label"],
        "help_text": expected["help_text"],
        "field_type": expected["field_type"],
        "activation": expected["activation"],
        "quantity_kind": expected["quantity_kind"],
        "canonical_unit": expected["canonical_unit"],
        "minimum_value": Decimal(validation["minimum"]) if validation.get("minimum") is not None else None,
        "maximum_value": Decimal(validation["maximum"]) if validation.get("maximum") is not None else None,
        "regex": validation.get("regex"),
        "decimal_scale": validation.get("scale"),
        "max_values": validation.get("max_values"),
        "text_max_length": validation.get("max_length"),
        "validation_rule": validation.get("rule"),
        "required": expected["required"],
        "nullable": expected["nullable"],
        "mappings": [],
        "choice_set_id": choice_set_id,
        "management_kind": "core",
        "version": 1,
        "lifecycle": expected["lifecycle"],
        "deprecated_at": deprecated_at,
        "replaced_by": None,
        "library_id": None,
        "connector_identity": None,
    }


def _reconcile_fields(apps, db_alias, choice_sets, now):
    CustomField = apps.get_model("extras", "CustomField")
    ContentType = apps.get_model("contenttypes", "ContentType")
    expected_keys = _expected_field_keys()
    for field in CustomField._base_manager.using(db_alias).filter(namespace="itambox", management_kind="core"):
        if field.name not in expected_keys:
            _fail("unexpected_core_field", field.name)

    fields_by_key = {}
    for expected in _field_rows():
        key = _key(expected["identity"])
        existing_rows = list(CustomField._base_manager.using(db_alias).filter(name=key).order_by("pk"))
        if len(existing_rows) > 1:
            _fail("field_identity_collision", key)
        existing = existing_rows[0] if existing_rows else None
        if existing is not None and (
            existing.lifecycle not in {"active", "deprecated"}
            or existing.namespace != "itambox"
            or existing.management_kind != "core"
            or existing.library_id is not None
        ):
            _fail("field_identity_collision", key)
        if existing is not None and expected["lifecycle"] == "active" and existing.lifecycle != "active":
            _fail("field_lifecycle_collision", key)
        choice_set_id = None
        if expected["choice_set"] is not None:
            choice_set_id = choice_sets[_key(expected["choice_set"])].pk
        deprecated_at = None
        if expected["lifecycle"] == "deprecated":
            deprecated_at = (existing.deprecated_at if existing else None) or now
        values = _field_values(expected, choice_set_id, deprecated_at)
        if existing is None:
            field = CustomField._base_manager.using(db_alias).create(name=key, **values)
        else:
            CustomField._base_manager.using(db_alias).filter(pk=existing.pk).update(**values)
            field = existing
        fields_by_key[key] = field

        through = CustomField.object_types.through
        content_type_ids = set(_content_type_ids(ContentType, db_alias, expected["targets"]))
        through._base_manager.using(db_alias).filter(customfield_id=field.pk).exclude(
            contenttype_id__in=content_type_ids
        ).delete()
        current_ids = set(
            through._base_manager.using(db_alias)
            .filter(customfield_id=field.pk)
            .values_list("contenttype_id", flat=True)
        )
        through._base_manager.using(db_alias).bulk_create(
            [
                through(customfield_id=field.pk, contenttype_id=content_type_id)
                for content_type_id in content_type_ids - current_ids
            ]
        )
    return fields_by_key


def _ensure_fieldsets(apps, db_alias):
    Fieldset = apps.get_model("extras", "CustomFieldset")
    expected_slugs = _expected_section_slugs()
    for fieldset in Fieldset._base_manager.using(db_alias).filter(namespace="itambox"):
        if fieldset.management_kind == "core" and fieldset.slug not in expected_slugs:
            _fail("unexpected_core_fieldset", fieldset.slug)

    fieldsets = {}
    for expected in CORE_VOCABULARY["sections"]:
        slug = expected["slug"]
        rows = list(
            Fieldset._base_manager.using(db_alias)
            .filter(namespace="itambox", slug=slug)
            .order_by("pk")
        )
        if len(rows) > 1:
            _fail("fieldset_identity_collision", slug)
        if rows:
            fieldset = rows[0]
            if (
                fieldset.management_kind != "core"
                or fieldset.lifecycle != "active"
                or fieldset.library_id is not None
            ):
                _fail("fieldset_identity_collision", slug)
        else:
            fieldset = Fieldset._base_manager.using(db_alias).create(
                namespace="itambox",
                slug=slug,
                label=expected["label"],
                description=expected["description"],
                management_kind="core",
                version=1,
                lifecycle=expected["lifecycle"],
                deprecated_at=None,
                replaced_by=None,
                library_id=None,
                connector_identity=None,
            )
        Fieldset._base_manager.using(db_alias).filter(pk=fieldset.pk).update(
            label=expected["label"],
            description=expected["description"],
            management_kind="core",
            version=1,
            lifecycle=expected["lifecycle"],
            deprecated_at=None,
            replaced_by=None,
            library_id=None,
            connector_identity=None,
        )
        fieldsets[slug] = fieldset
    return fieldsets


def _reconcile_fieldset_memberships(apps, db_alias, fieldsets, fields_by_key):
    FieldsetField = apps.get_model("extras", "CustomFieldsetField")
    for expected in CORE_VOCABULARY["sections"]:
        fieldset = fieldsets[expected["slug"]]
        existing = list(
            FieldsetField._base_manager.using(db_alias)
            .filter(fieldset_id=fieldset.pk)
            .select_related("custom_field")
        )
        if any(membership.custom_field.management_kind != "core" for membership in existing):
            _fail("fieldset_membership_ownership_collision", expected["slug"])
        FieldsetField._base_manager.using(db_alias).filter(fieldset_id=fieldset.pk).delete()
        FieldsetField._base_manager.using(db_alias).bulk_create(
            [
                FieldsetField(
                    fieldset_id=fieldset.pk,
                    custom_field_id=fields_by_key[_key(membership["field"])].pk,
                    position=membership["position"],
                )
                for membership in expected["memberships"]
            ]
        )


def _category_row(Category, db_alias, expected):
    rows = list(Category._base_manager.using(db_alias).filter(slug=expected["slug"]).order_by("pk"))
    active_rows = [row for row in rows if row.deleted_at is None]
    if len(active_rows) > 1 or len(rows) > 1 and not active_rows:
        _fail("category_identity_collision", expected["slug"])
    if active_rows:
        category = active_rows[0]
    elif rows:
        _fail("category_identity_collision", expected["slug"])
    else:
        category = Category._base_manager.using(db_alias).create(
            name=expected["label"],
            slug=expected["slug"],
            description=expected["description"],
            applies_to={scope: True for scope in expected["applies_to"]},
        )
    Category._base_manager.using(db_alias).filter(pk=category.pk).update(
        name=expected["label"],
        description=expected["description"],
        applies_to={scope: True for scope in expected["applies_to"]},
    )
    return category


def _reconcile_categories(apps, db_alias, fieldsets):
    Category = apps.get_model("assets", "Category")
    CategoryDefault = apps.get_model("assets", "CategoryDefaultFieldset")
    categories = {}
    for expected in CORE_VOCABULARY["categories"]:
        category = _category_row(Category, db_alias, expected)
        categories[expected["slug"]] = category
        existing = list(
            CategoryDefault._base_manager.using(db_alias)
            .filter(category_id=category.pk)
            .select_related("fieldset")
        )
        if any(membership.fieldset.management_kind != "core" for membership in existing):
            _fail("category_default_ownership_collision", expected["slug"])
        CategoryDefault._base_manager.using(db_alias).filter(category_id=category.pk).delete()
        CategoryDefault._base_manager.using(db_alias).bulk_create(
            [
                CategoryDefault(
                    category_id=category.pk,
                    fieldset_id=fieldsets[_key(membership["fieldset"])].pk,
                    position=membership["position"],
                )
                for membership in expected["default_fieldsets"]
            ]
        )
    return categories


def _set_constraints_deferred(schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL DEFERRED")


def _set_constraints_immediate(schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def forward(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    now = timezone.now()
    with transaction.atomic(using=db_alias):
        _set_constraints_deferred(schema_editor)
        choice_sets = _ensure_choice_sets(apps, db_alias)
        _reconcile_choices(apps, db_alias, choice_sets, now)
        fields_by_key = _reconcile_fields(apps, db_alias, choice_sets, now)
        fieldsets = _ensure_fieldsets(apps, db_alias)
        _reconcile_fieldset_memberships(apps, db_alias, fieldsets, fields_by_key)
        _reconcile_categories(apps, db_alias, fieldsets)
        _set_constraints_immediate(schema_editor)


def reverse_refused(apps, schema_editor):
    raise MigrationConflict("issue479:t11:reverse_refused")


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0116_assettypeimagestage"),
        ("extras", "0120_issue479_t07_provenance_cutover"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [migrations.RunPython(forward, reverse_code=reverse_refused)]
