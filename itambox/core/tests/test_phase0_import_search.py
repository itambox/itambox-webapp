"""Phase 0 hardening tests.

Covers:
- G4: BulkImportForm rejects payloads exceeding MAX_IMPORT_ROWS.
- G5: the search view's lookup allowlist no longer admits the ReDoS-prone
  'iregex'/'regex' lookups.
"""
import inspect

import pytest
from django.test import TestCase

from core.forms.import_forms import BulkImportForm, MAX_IMPORT_ROWS
from assets.models import Manufacturer


class _MfrImportForm(BulkImportForm):
    model = Manufacturer
    required_fields = ['name']
    optional_fields = ['slug']


@pytest.mark.django_db
class ImportRowCapTests(TestCase):
    """G4: more than MAX_IMPORT_ROWS data rows is a validation error."""

    def _build_csv(self, n_rows):
        lines = ['name,slug']
        lines += [f'mfr{i},mfr-{i}' for i in range(n_rows)]
        return '\n'.join(lines)

    def test_over_limit_csv_is_invalid(self):
        form = _MfrImportForm(data={
            'import_format': 'csv',
            'active_tab': 'editor',
            'delimiter': ',',
            'import_text': self._build_csv(MAX_IMPORT_ROWS + 1),
        })
        self.assertFalse(form.is_valid())
        joined = ' '.join(form.non_field_errors())
        self.assertIn('maximum', joined.lower())
        self.assertIn(str(MAX_IMPORT_ROWS), joined)

    def test_at_limit_csv_is_accepted(self):
        form = _MfrImportForm(data={
            'import_format': 'csv',
            'active_tab': 'editor',
            'delimiter': ',',
            'import_text': self._build_csv(MAX_IMPORT_ROWS),
        })
        # Exactly MAX_IMPORT_ROWS rows must not trip the cap.
        self.assertTrue(form.is_valid(), form.errors)


class SearchLookupAllowlistTests(TestCase):
    """G5: the regex lookups are no longer reachable from the search view."""

    def test_iregex_and_regex_not_allowed(self):
        from itambox.views.utility import SearchView

        source = inspect.getsource(SearchView.get)
        # The allowlist is a local literal in SearchView.get; the ReDoS-prone
        # regex lookups must not appear in it.
        self.assertNotIn("'iregex'", source)
        self.assertNotIn("'regex'", source)
        # Sanity: the safe lookups are still present.
        self.assertIn("'icontains'", source)
