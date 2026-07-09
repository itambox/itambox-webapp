"""A5 (WS3-7b) regression: import error bodies are sanitized.

A row that triggers a non-ValidationError exception (e.g. a DB IntegrityError)
during import must surface a generic per-row message to the user — the raw
driver text / exception detail belongs in the server log only.
"""
import pytest
from django.db import IntegrityError
from django.test import TestCase

from core.forms.import_forms import BulkImportForm
from assets.models import Manufacturer


# A sentinel string standing in for the kind of internal/driver detail that the
# raw `str(e)` path used to leak into the user-facing error list.
_DRIVER_LEAK = 'duplicate key value violates unique constraint "secret_idx_42"'


class _ExplodingInstance:
    """Stand-in for a model instance whose save() raises a DB-style
    IntegrityError carrying sensitive driver text. full_clean() is a no-op so the
    exception reaches the import loop's generic handler, not the
    ValidationError branch."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def full_clean(self, *args, **kwargs):
        return None

    def save(self, *args, **kwargs):
        raise IntegrityError(_DRIVER_LEAK)


class _ExplodingImportForm(BulkImportForm):
    model = Manufacturer
    required_fields = ['name']
    optional_fields = ['slug']

    def _create_instance(self, mapped_data):
        return _ExplodingInstance(**mapped_data)


@pytest.mark.django_db
class ImportErrorSanitizationTests(TestCase):
    def test_integrity_error_row_message_is_sanitized(self):
        form = _ExplodingImportForm()
        # Drive import_data() directly with a single pre-parsed row.
        form._rows_data = [{'name': 'Acme', 'slug': 'acme'}]

        imported, errors = form.import_data()

        self.assertEqual(imported, 0)
        self.assertEqual(len(errors), 1)
        message = errors[0]
        # The row index is still reported so the user can locate the bad record.
        self.assertIn('Row 2', message)
        # The raw driver/exception detail must NOT leak into the user message.
        self.assertNotIn(_DRIVER_LEAK, message)
        self.assertNotIn('unique constraint', message)
        self.assertNotIn('secret_idx_42', message)
        # And it reads as a generic per-row failure.
        self.assertIn('unexpected error', message.lower())
