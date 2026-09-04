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
