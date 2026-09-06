from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from core.mixins import suppress_custom_field_data_validation
from core.models import ChangeLoggingMixin
from core.signals import validate_custom_validators_on_save


class ChangeLoggingValidationSignalTests(TestCase):
    def test_field_limited_save_still_runs_model_clean(self):
        class DummyModel(ChangeLoggingMixin):
            pass

        instance = Mock()

        validate_custom_validators_on_save(DummyModel, instance, update_fields={"status"})

        instance.clean.assert_called_once_with()


class CustomFieldValidationSuppressionTests(TestCase):
    def test_marker_is_removed_after_successful_suppression(self):
        instance = SimpleNamespace()

        with suppress_custom_field_data_validation(instance):
            self.assertTrue(instance._skip_custom_field_data_validation)

        self.assertFalse(hasattr(instance, "_skip_custom_field_data_validation"))

    def test_marker_is_removed_after_exception(self):
        instance = SimpleNamespace()

        with self.assertRaises(RuntimeError):
            with suppress_custom_field_data_validation(instance):
                raise RuntimeError("boom")

        self.assertFalse(hasattr(instance, "_skip_custom_field_data_validation"))

    def test_nested_suppression_preserves_existing_marker_value(self):
        instance = SimpleNamespace()
        instance._skip_custom_field_data_validation = "outer-sentinel"

        with suppress_custom_field_data_validation(instance):
            with suppress_custom_field_data_validation(instance):
                self.assertTrue(instance._skip_custom_field_data_validation)
            # The inner context manager restores the outer marker state.
            self.assertTrue(instance._skip_custom_field_data_validation)
        # The outer context manager restores the original marker value.
        self.assertEqual(instance._skip_custom_field_data_validation, "outer-sentinel")

    def test_signal_preserves_existing_marker_while_invoking_clean(self):
        class DummyModel(ChangeLoggingMixin):
            pass

        instance = Mock()
        instance._skip_custom_field_data_validation = "sentinel"

        validate_custom_validators_on_save(DummyModel, instance, update_fields={"status"})

        instance.clean.assert_called_once_with()
        self.assertEqual(instance._skip_custom_field_data_validation, "sentinel")

    def test_applicability_dependency_changes_run_full_clean_other_fields_suppress(self):
        calls = []

        class DummyModel(ChangeLoggingMixin):
            custom_field_data_validation_dependencies = frozenset({"asset_type"})

            def clean(self):
                calls.append(("clean", getattr(self, "_skip_custom_field_data_validation", None)))

        instance = DummyModel()

        # Unrelated field-limited saves keep the efficient suppression path.
        validate_custom_validators_on_save(DummyModel, instance, update_fields={"status"})
        self.assertEqual(calls, [("clean", True)])

        # A declared applicability dependency must run full dynamic validation.
        calls.clear()
        validate_custom_validators_on_save(DummyModel, instance, update_fields={"asset_type"})
        self.assertEqual(calls, [("clean", None)])

        # custom_field_data itself and full saves keep the full path.
        calls.clear()
        validate_custom_validators_on_save(DummyModel, instance, update_fields={"custom_field_data"})
        self.assertEqual(calls, [("clean", None)])

        calls.clear()
        validate_custom_validators_on_save(DummyModel, instance, update_fields=None)
        self.assertEqual(calls, [("clean", None)])

    def test_dependency_check_covers_attname_spelling(self):
        from django.core.exceptions import FieldDoesNotExist

        calls = []

        class DummyModel(ChangeLoggingMixin):
            custom_field_data_validation_dependencies = frozenset({"asset_type"})

            def clean(self):
                calls.append(("clean", getattr(self, "_skip_custom_field_data_validation", None)))

        def meta_get_field(name):
            if name == "asset_type":
                return SimpleNamespace(attname="asset_type_id")
            raise FieldDoesNotExist(name)

        DummyModel._meta = SimpleNamespace(get_field=meta_get_field)
        instance = DummyModel()

        # Django accepts the ``attname`` spelling of a foreign key in
        # ``update_fields``; the dependency check must not fail open for it.
        validate_custom_validators_on_save(DummyModel, instance, update_fields={"asset_type_id"})
        self.assertEqual(calls, [("clean", None)])

        calls.clear()
        validate_custom_validators_on_save(DummyModel, instance, update_fields={"status"})
        self.assertEqual(calls, [("clean", True)])

    def test_models_without_dependencies_keep_plain_suppression_semantics(self):
        class DummyModel(ChangeLoggingMixin):
            pass

        instance = Mock()

        validate_custom_validators_on_save(DummyModel, instance, update_fields={"asset_type"})

        instance.clean.assert_called_once_with()
        self.assertTrue(instance._skip_custom_field_data_validation)
