"""Preflight applicability rules of the normalized definition conversion.

The wave-1 helper module (extras.t06_schema) and its migration were replaced
by the normalized extras chain; the pure applicability preflight now lives in
extras.0115, so this module exercises it directly with the same row fakes the
wave-1 suite used. The scope concept was removed with the normalization -
object types are the only applicability authority - so the cases cover
supported owners, owners without custom-field storage, fields without object
types and unresolvable object types.
"""

import unittest
from importlib import import_module
from types import SimpleNamespace

_t06_migration = import_module("extras.migrations.0115_asset_type_definition_conversion")


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


def _historical_preflight_apps(identities, *, owners_with_custom_field_data=()):
    content_types = [
        SimpleNamespace(pk=index, app_label=app_label, model=model_name)
        for index, (app_label, model_name) in enumerate(identities, start=1)
    ]
    custom_field = SimpleNamespace(pk=1)
    custom_field.object_types = SimpleNamespace(
        through=SimpleNamespace(
            _base_manager=_FakeRows(
                [SimpleNamespace(customfield_id=1, contenttype_id=content_type.pk) for content_type in content_types]
            )
        )
    )
    custom_field._base_manager = _FakeRows([custom_field])
    content_type_model = SimpleNamespace(_base_manager=_FakeRows(content_types))
    owner_models = {
        identity: SimpleNamespace(
            _meta=SimpleNamespace(
                concrete_fields=(
                    [SimpleNamespace(name="custom_field_data")] if identity in owners_with_custom_field_data else []
                )
            )
        )
        for identity in identities
    }
    return _FakeApps(custom_field, content_type_model, owner_models)


class T06SchemaHelperTests(unittest.TestCase):
    def test_preflight_preserves_supported_owner_with_custom_field_data(self):
        identities = (("assets", "asset"), ("organization", "assetholder"))
        apps = _historical_preflight_apps(identities, owners_with_custom_field_data=identities)

        _t06_migration._preflight_applicability(apps, "default")

    def test_preflight_rejects_resolvable_owner_without_custom_field_data(self):
        apps = _historical_preflight_apps((("extras", "tag"),))

        with self.assertRaisesRegex(_t06_migration.MigrationConflict, "missing_custom_field_data"):
            _t06_migration._preflight_applicability(apps, "default")

    def test_preflight_rejects_field_without_object_types(self):
        apps = _historical_preflight_apps(())

        with self.assertRaisesRegex(_t06_migration.MigrationConflict, "empty_object_types"):
            _t06_migration._preflight_applicability(apps, "default")

    def test_preflight_rejects_unresolvable_object_type(self):
        apps = _historical_preflight_apps((("assets", "assettype"),))
        del apps._models[("assets", "assettype")]

        with self.assertRaisesRegex(_t06_migration.MigrationConflict, "unresolvable_object_type"):
            _t06_migration._preflight_applicability(apps, "default")


if __name__ == "__main__":
    unittest.main()
