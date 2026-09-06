import unittest
from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace


_t06_migration = import_module("extras.migrations.0118_issue479_t06_definition_schema")


class _FakeRows:
    def __init__(self, rows):
        self.rows = list(rows)

    def using(self, _db_alias):
        return self

    def filter(self, **filters):
        rows = self.rows
        for field, expected in filters.items():
            if field.endswith("__in"):
                field = field[:-4]
                rows = [row for row in rows if getattr(row, field) in expected]
            else:
                rows = [row for row in rows if getattr(row, field) == expected]
        return type(self)(rows)

    def order_by(self, *fields):
        rows = list(self.rows)
        for field in reversed(fields):
            rows.sort(key=lambda row: getattr(row, field))
        return type(self)(rows)

    def values_list(self, *fields):
        values = [tuple(getattr(row, field) for field in fields) for row in self.rows]
        return [row[0] for row in values] if len(fields) == 1 else values

    def __iter__(self):
        return iter(self.rows)


class _FakeApps:
    def __init__(self, custom_field, content_types, owner_models):
        self._models = {
            ("extras", "CustomField"): custom_field,
            ("contenttypes", "ContentType"): content_types,
            **owner_models,
        }

    def get_model(self, app_label, model_name):
        try:
            return self._models[(app_label, model_name)]
        except KeyError as exc:
            raise LookupError((app_label, model_name)) from exc


def _historical_preflight_apps(scope, identities, *, owners_with_custom_field_data=()):
    content_types = [
        SimpleNamespace(pk=index, app_label=app_label, model=model_name)
        for index, (app_label, model_name) in enumerate(identities, start=1)
    ]
    custom_field = SimpleNamespace(
        pk=1,
        scope=scope,
    )
    custom_field.object_types = SimpleNamespace(
        through=SimpleNamespace(
            _base_manager=_FakeRows(
                [
                    SimpleNamespace(customfield_id=1, contenttype_id=content_type.pk)
                    for content_type in content_types
                ]
            )
        )
    )
    custom_field._base_manager = _FakeRows([custom_field])
    content_type_model = SimpleNamespace(_base_manager=_FakeRows(content_types))
    owner_models = {
        identity: SimpleNamespace(
            _meta=SimpleNamespace(
                concrete_fields=(
                    [SimpleNamespace(name="custom_field_data")]
                    if identity in owners_with_custom_field_data
                    else []
                )
            )
        )
        for identity in identities
    }
    return _FakeApps(custom_field, content_type_model, owner_models)


from extras.t06_schema import (
    T06SchemaConflict,
    classify_activation,
    dense_ordinals,
    normalize_lifecycle,
    validate_activation,
    validate_object_types,
)


class T06SchemaHelperTests(unittest.TestCase):
    def test_object_types_are_the_only_applicability_authority(self):
        self.assertEqual(
            validate_object_types(("assets.assettype", "organization.assetholder", "assets.assettype")),
            ("assets.assettype", "organization.assetholder"),
        )

    def test_empty_object_types_are_blocking(self):
        with self.assertRaisesRegex(T06SchemaConflict, "empty_object_types"):
            validate_object_types(())

    def test_activation_is_a_closed_vocabulary(self):
        self.assertEqual(validate_activation("global"), "global")
        with self.assertRaisesRegex(T06SchemaConflict, "invalid_activation"):
            validate_activation("membership-derived")

    def test_activation_classification_is_not_recomputed_at_runtime(self):
        self.assertEqual(classify_activation(has_memberships=True), "composed")
        self.assertEqual(classify_activation(has_memberships=False), "global")

    def test_lifecycle_preserves_deprecated_history(self):
        timestamp = datetime(2026, 9, 6, tzinfo=timezone.utc)
        lifecycle, deprecated_at = normalize_lifecycle(
            lifecycle="deleted",
            deleted_at=None,
            deprecated_at=None,
            migration_timestamp=timestamp,
        )
        self.assertEqual((lifecycle, deprecated_at), ("deprecated", timestamp))

    def test_ordinals_are_dense_and_stable(self):
        self.assertEqual(
            dense_ordinals(((30, "z"), (10, "a"), (20, "m"))),
            {"a": 1, "m": 2, "z": 3},
        )
        with self.assertRaisesRegex(T06SchemaConflict, "duplicate_member"):
            dense_ordinals(((1, "same"), (2, "same")))
    def test_preflight_requires_exact_non_null_scope_target_set(self):
        cases = (
            ("asset", (("assets", "asset"), ("assets", "assettype"))),
            ("asset", (("assets", "asset"), ("organization", "assetholder"))),
            ("both", (("assets", "asset"), ("assets", "assettype"), ("organization", "assetholder"))),
        )
        for scope, identities in cases:
            with self.subTest(scope=scope, identities=identities):
                apps = _historical_preflight_apps(
                    scope,
                    identities,
                    owners_with_custom_field_data=identities,
                )
                with self.assertRaisesRegex(_t06_migration.MigrationConflict, "scope_object_types_contradiction"):
                    _t06_migration._preflight_applicability(apps, "default")

    def test_preflight_preserves_generic_scope_for_supported_non_asset_owner(self):
        identities = (("assets", "asset"), ("organization", "assetholder"))
        apps = _historical_preflight_apps(
            None,
            identities,
            owners_with_custom_field_data=identities,
        )

        _t06_migration._preflight_applicability(apps, "default")

    def test_preflight_rejects_resolvable_owner_without_custom_field_data(self):
        apps = _historical_preflight_apps(None, (("extras", "tag"),))

        with self.assertRaisesRegex(_t06_migration.MigrationConflict, "missing_custom_field_data"):
            _t06_migration._preflight_applicability(apps, "default")


if __name__ == "__main__":
    unittest.main()
