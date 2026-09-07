import inspect
import json
import os
import signal
import subprocess
import sys
import unittest
from decimal import Decimal
from functools import wraps
from pathlib import Path
from uuid import uuid4

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

_ISOLATED_MIGRATION_ENV = "ITAMBOX_ISSUE479_T11_MIGRATION_CHILD"


def _migration_test_nodeid(test_case, method):
    test_file = Path(inspect.getfile(type(test_case))).resolve()
    cwd = Path.cwd().resolve()
    try:
        relative_file = test_file.relative_to(cwd)
    except ValueError:
        relative_file = Path(os.path.relpath(test_file, cwd))
    return f"{relative_file.as_posix()}::{type(test_case).__name__}::{method.__name__}"


def _isolate_migration_test(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        if os.environ.get(_ISOLATED_MIGRATION_ENV) == "1":
            return method(self, *args, **kwargs)

        nodeid = _migration_test_nodeid(self, method)
        child_env = os.environ.copy()
        child_env[_ISOLATED_MIGRATION_ENV] = "1"
        timeout = float(os.environ.get("ITAMBOX_ISSUE479_T11_MIGRATION_TIMEOUT", "900"))
        popen_kwargs = {
            "cwd": os.getcwd(),
            "env": child_env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        child = subprocess.Popen([sys.executable, "-m", "pytest", nodeid], **popen_kwargs)
        try:
            output, _ = child.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                child.kill()
            else:
                os.killpg(child.pid, signal.SIGTERM)
            output, _ = child.communicate()
            self.fail(f"isolated migration test timed out after {timeout:g}s: {nodeid}\n{output}")
        if child.returncode:
            self.fail(f"isolated migration test exited {child.returncode}: {nodeid}\n{output}")

    return wrapper


def _isolate_migration_tests(test_class):
    for name, method in tuple(vars(test_class).items()):
        if name.startswith("test_"):
            setattr(test_class, name, _isolate_migration_test(method))
    return test_class


@_isolate_migration_tests
@pytest.mark.serial_only
class CoreVocabularyMigrationTests(TransactionTestCase):
    databases = {"default"} if os.environ.get(_ISOLATED_MIGRATION_ENV) == "1" else set()
    predecessor = [
        ("assets", "0116_assettypeimagestage"),
        ("extras", "0120_issue479_t07_provenance_cutover"),
    ]
    target = [
        ("assets", "0117_issue479_final_core_vocabulary"),
        ("extras", "0120_issue479_t07_provenance_cutover"),
    ]

    @staticmethod
    def _is_isolated_child():
        return os.environ.get(_ISOLATED_MIGRATION_ENV) == "1"

    @classmethod
    def _pre_setup(cls):
        if cls._is_isolated_child():
            super()._pre_setup()

    def setUp(self):
        if not self._is_isolated_child():
            return
        super().setUp()
        self._schema_name = f"issue479_t11_{os.getpid()}_{uuid4().hex[:12]}"
        quoted_schema = connection.ops.quote_name(self._schema_name)
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE SCHEMA {quoted_schema}")
            cursor.execute(f"SET search_path TO {quoted_schema}")
            MigrationRecorder(connection).ensure_schema()
            cursor.execute(f"SET search_path TO {quoted_schema}, public")

    def tearDown(self):
        if not self._is_isolated_child():
            return
        quoted_schema = connection.ops.quote_name(self._schema_name)
        try:
            super().tearDown()
        finally:
            with connection.cursor() as cursor:
                cursor.execute("SET search_path TO public")
                cursor.execute(f"DROP SCHEMA IF EXISTS {quoted_schema} CASCADE")

    def _post_teardown(self):
        if self._is_isolated_child():
            super()._post_teardown()

    @staticmethod
    def _oracle():
        repository_root = Path(__file__).resolve().parents[3]
        oracle_path = (
            repository_root
            / "scripts"
            / "tests"
            / "fixtures"
            / "specification_vocabulary"
            / "canonical-target.json"
        )
        return json.loads(oracle_path.read_text(encoding="utf-8"))

    def _migrate(self, targets):
        MigrationExecutor(connection).migrate(targets)
        return MigrationExecutor(connection).loader.project_state(targets).apps

    def _assert_exact_vocabulary(self, apps):
        oracle = self._oracle()
        CustomField = apps.get_model("extras", "CustomField")
        ChoiceSet = apps.get_model("extras", "CustomFieldChoiceSet")
        Choice = apps.get_model("extras", "CustomFieldChoice")
        Fieldset = apps.get_model("extras", "CustomFieldset")
        FieldsetField = apps.get_model("extras", "CustomFieldsetField")
        Category = apps.get_model("assets", "Category")
        CategoryDefault = apps.get_model("assets", "CategoryDefaultFieldset")
        ContentTypeModel = apps.get_model("contenttypes", "ContentType")

        expected_fields = oracle["active_fields"] + oracle["reserved_retired_fields"]
        expected_field_names = {row["key"] for row in expected_fields}
        core_fields = list(
            CustomField._base_manager.filter(namespace="itambox", management_kind="core").order_by("name")
        )
        self.assertEqual({field.name for field in core_fields}, expected_field_names)
        scope_models = {
            "asset_type": {("assets", "assettype")},
            "asset": {("assets", "asset")},
            "both": {("assets", "asset"), ("assets", "assettype")},
        }
        for expected in expected_fields:
            field = CustomField._base_manager.get(namespace="itambox", name=expected["key"])
            self.assertEqual(field.label, expected["label"])
            self.assertEqual(field.help_text, expected["help_text"])
            self.assertEqual(field.field_type, expected["field_type"])
            self.assertEqual(field.activation, expected["activation"])
            self.assertEqual(field.quantity_kind, expected["quantity_kind"])
            self.assertEqual(field.canonical_unit, expected["canonical_unit"])
            self.assertEqual(field.required, expected["required"])
            self.assertEqual(field.nullable, expected["nullable"])
            self.assertEqual(field.lifecycle, expected["lifecycle"])
            self.assertEqual(field.version, 1)
            self.assertEqual(field.library_id, None)
            self.assertEqual(field.choice_set_id is None, expected["choice_set"] is None)
            if expected["choice_set"] is not None:
                choice_set = ChoiceSet._base_manager.get(pk=field.choice_set_id)
                self.assertEqual(
                    (choice_set.namespace, choice_set.slug),
                    ("itambox", expected["choice_set"].split("/", 1)[1]),
                )
            validation = expected["validation"]
            self.assertEqual(
                None if validation.get("minimum") is None else Decimal(validation["minimum"]),
                field.minimum_value,
            )
            self.assertEqual(
                None if validation.get("maximum") is None else Decimal(validation["maximum"]),
                field.maximum_value,
            )
            self.assertEqual(field.decimal_scale, validation.get("scale"))
            self.assertEqual(field.max_values, validation.get("max_values"))
            self.assertEqual(field.text_max_length, validation.get("max_length"))
            self.assertEqual(field.regex, validation.get("regex"))
            self.assertEqual(field.validation_rule, validation.get("rule"))
            actual_types = {
                (content_type.app_label, content_type.model)
                for content_type in ContentTypeModel._base_manager.filter(
                    pk__in=field.object_types.values_list("pk", flat=True)
                )
            }
            scope_key = (
                "asset_type"
                if "asset_type" in expected["targets"] and len(expected["targets"]) == 1
                else "both"
                if len(expected["targets"]) == 2
                else "asset"
            )
            self.assertEqual(actual_types, scope_models[scope_key])

        for expected in oracle["sections"]:
            fieldset = Fieldset._base_manager.get(namespace="itambox", slug=expected["slug"])
            self.assertEqual(fieldset.label, expected["label"])
            self.assertEqual(fieldset.description, expected["description"])
            self.assertEqual(fieldset.lifecycle, expected["lifecycle"])
            self.assertEqual(fieldset.management_kind, "core")
            actual_memberships = list(
                FieldsetField._base_manager.filter(fieldset_id=fieldset.pk)
                .order_by("position")
                .values_list("custom_field__name", "position")
            )
            expected_memberships = [
                (membership["field"].rsplit("/", 1)[1], membership["position"])
                for membership in expected["memberships"]
            ]
            self.assertEqual(actual_memberships, expected_memberships)

        for expected in oracle["choice_sets"]:
            choice_set = ChoiceSet._base_manager.get(namespace="itambox", slug=expected["slug"])
            self.assertEqual(choice_set.label, expected["label"])
            self.assertEqual(choice_set.lifecycle, expected["lifecycle"])
            self.assertEqual(choice_set.management_kind, "core")
            actual_choices = list(
                Choice._base_manager.filter(choice_set_id=choice_set.pk)
                .order_by("position")
                .values_list("key", "label", "lifecycle", "position")
            )
            expected_choices = [
                (choice["key"], choice["label"], choice["lifecycle"], choice["position"])
                for choice in expected["choices"]
            ]
            self.assertEqual(actual_choices, expected_choices)

        for expected in oracle["categories"]:
            category = Category._base_manager.get(slug=expected["slug"])
            self.assertEqual(category.name, expected["label"])
            self.assertEqual(category.description, expected["description"])
            self.assertEqual(
                sorted(key for key, value in category.applies_to.items() if value),
                sorted(expected["applies_to"]),
            )
            actual_defaults = list(
                CategoryDefault._base_manager.filter(category_id=category.pk)
                .order_by("position")
                .values_list("fieldset__namespace", "fieldset__slug", "position")
            )
            expected_defaults = [
                (fieldset["fieldset"].split("/", 1)[0], fieldset["fieldset"].split("/", 1)[1], fieldset["position"])
                for fieldset in expected["default_fieldsets"]
            ]
            self.assertEqual(actual_defaults, expected_defaults)

    def test_fresh_upgrade_reconciles_complete_vocabulary_without_catalogue_rows(self):
        apps = self._migrate(self.target)
        self._assert_exact_vocabulary(apps)
        self.assertEqual(apps.get_model("assets", "Manufacturer")._base_manager.count(), 0)
        self.assertEqual(apps.get_model("assets", "AssetType")._base_manager.count(), 0)

    def test_supported_predecessor_upgrade_preserves_values_and_local_ownership(self):
        apps = self._migrate(self.predecessor)
        CustomField = apps.get_model("extras", "CustomField")
        CustomFieldset = apps.get_model("extras", "CustomFieldset")
        CustomFieldsetField = apps.get_model("extras", "CustomFieldsetField")
        ChoiceSet = apps.get_model("extras", "CustomFieldChoiceSet")
        Choice = apps.get_model("extras", "CustomFieldChoice")
        Category = apps.get_model("assets", "Category")
        CategoryDefault = apps.get_model("assets", "CategoryDefaultFieldset")
        Manufacturer = apps.get_model("assets", "Manufacturer")
        AssetType = apps.get_model("assets", "AssetType")

        local_field = CustomField._base_manager.create(
            name="local_keep",
            namespace="local",
            label="Keep local field",
            field_type="text",
            activation="composed",
            management_kind="local",
            lifecycle="active",
        )
        local_fieldset = CustomFieldset._base_manager.create(
            namespace="local",
            slug="local-keep",
            label="Keep local fieldset",
            description="Local definition must survive the core reconciliation.",
            management_kind="local",
            lifecycle="active",
        )
        CustomFieldsetField._base_manager.create(fieldset_id=local_fieldset.pk, custom_field_id=local_field.pk, position=10)
        local_choice_set = ChoiceSet._base_manager.create(
            namespace="local",
            slug="local-keep",
            label="Keep local choices",
            management_kind="local",
            lifecycle="active",
        )
        local_choice = Choice._base_manager.create(
            choice_set_id=local_choice_set.pk,
            key="local_value",
            label="Local value",
            position=10,
            lifecycle="active",
        )
        local_category = Category._base_manager.create(
            name="Keep Local Category",
            slug="local-keep",
            description="Local category must survive.",
            applies_to={"component": True},
        )
        CategoryDefault._base_manager.create(category_id=local_category.pk, fieldset_id=local_fieldset.pk, position=10)

        laptops = Category._base_manager.create(
            name="Old Laptop",
            slug="laptops",
            description="Old description",
            applies_to={"asset": True, "component": True},
        )
        old_storage = CustomFieldset._base_manager.get(namespace="itambox", slug="storage")
        CategoryDefault._base_manager.create(category_id=laptops.pk, fieldset_id=old_storage.pk, position=10)

        manufacturer = Manufacturer._base_manager.create(name="Existing Maker", slug="existing-maker")
        asset_type = AssetType._base_manager.create(
            manufacturer_id=manufacturer.pk,
            model="Existing Model",
            slug="existing-model",
            custom_field_data={
                "input_voltage": "230.0",
                "storage_medium": "nvme_ssd",
                "storage_interface": "SSD-interface",
                "poe_port_count": 8,
            },
        )
        old_asset_type_membership = apps.get_model("assets", "AssetTypeFieldset")._base_manager.create(
            asset_type_id=asset_type.pk,
            fieldset_id=old_storage.pk,
            position=10,
        )

        apps = self._migrate(self.target)
        self._assert_exact_vocabulary(apps)

        migrated_asset_type = apps.get_model("assets", "AssetType")._base_manager.get(pk=asset_type.pk)
        self.assertEqual(
            migrated_asset_type.custom_field_data,
            {
                "input_voltage": "230.0",
                "storage_medium": "nvme_ssd",
                "storage_interface": "SSD-interface",
                "poe_port_count": 8,
            },
        )
        self.assertEqual(
            apps.get_model("assets", "AssetTypeFieldset")._base_manager.get(pk=old_asset_type_membership.pk).position,
            10,
        )
        self.assertEqual(apps.get_model("assets", "Manufacturer")._base_manager.count(), 1)
        self.assertEqual(apps.get_model("assets", "AssetType")._base_manager.count(), 1)
        self.assertTrue(
            apps.get_model("extras", "CustomField")._base_manager.filter(pk=local_field.pk, namespace="local").exists()
        )
        self.assertTrue(
            apps.get_model("extras", "CustomFieldset")._base_manager.filter(pk=local_fieldset.pk, namespace="local").exists()
        )
        self.assertTrue(
            apps.get_model("extras", "CustomFieldChoiceSet")
            ._base_manager.filter(pk=local_choice_set.pk, namespace="local")
            .exists()
        )
        self.assertTrue(
            apps.get_model("extras", "CustomFieldChoice")
            ._base_manager.filter(pk=local_choice.pk, choice_set_id=local_choice_set.pk)
            .exists()
        )
        self.assertTrue(
            apps.get_model("assets", "Category")._base_manager.filter(pk=local_category.pk, slug="local-keep").exists()
        )
        self.assertEqual(
            list(
                apps.get_model("assets", "CategoryDefaultFieldset")
                ._base_manager.filter(category_id=local_category.pk)
                .values_list("fieldset_id", "position")
            ),
            [(local_fieldset.pk, 10)],
        )

        input_voltage = apps.get_model("extras", "CustomField")._base_manager.get(name="input_voltage")
        nvme = apps.get_model("extras", "CustomFieldChoice")._base_manager.get(key="nvme_ssd")
        poe = apps.get_model("extras", "CustomField")._base_manager.get(name="poe_port_count")
        self.assertEqual(input_voltage.lifecycle, "deprecated")
        self.assertEqual(nvme.lifecycle, "deprecated")
        self.assertEqual(poe.lifecycle, "active")
        self.assertTrue(input_voltage.deprecated_at)
        self.assertTrue(nvme.deprecated_at)


if __name__ == "__main__":
    unittest.main()
