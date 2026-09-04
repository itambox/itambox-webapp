from unittest import TestCase
from unittest.mock import Mock

from core.models import ChangeLoggingMixin
from core.signals import validate_custom_validators_on_save


class ChangeLoggingValidationSignalTests(TestCase):
    def test_field_limited_save_still_runs_model_clean(self):
        class DummyModel(ChangeLoggingMixin):
            pass

        instance = Mock()

        validate_custom_validators_on_save(DummyModel, instance, update_fields={"status"})

        instance.clean.assert_called_once_with()
