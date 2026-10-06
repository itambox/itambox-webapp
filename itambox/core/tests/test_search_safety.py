"""Phase 0 hardening tests.

Covers:
- G4: BulkImportForm rejects payloads exceeding MAX_IMPORT_ROWS.
- G5: the search view's lookup allowlist no longer admits the ReDoS-prone
  'iregex'/'regex' lookups.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import RequestFactory, TestCase

from assets.models import Manufacturer
from core.forms.import_forms import MAX_IMPORT_ROWS, BulkImportForm


class _MfrImportForm(BulkImportForm):
    model = Manufacturer
    required_fields = ["name"]
    optional_fields = ["slug"]


@pytest.mark.django_db
class ImportRowCapTests(TestCase):
    """G4: more than MAX_IMPORT_ROWS data rows is a validation error."""

    def _build_csv(self, n_rows):
        lines = ["name,slug"]
        lines += [f"mfr{i},mfr-{i}" for i in range(n_rows)]
        return "\n".join(lines)

    def test_over_limit_csv_is_invalid(self):
        form = _MfrImportForm(
            data={
                "import_format": "csv",
                "active_tab": "editor",
                "delimiter": ",",
                "import_text": self._build_csv(MAX_IMPORT_ROWS + 1),
            }
        )
        self.assertFalse(form.is_valid())
        joined = " ".join(form.non_field_errors())
        self.assertIn("maximum", joined.lower())
        self.assertIn(str(MAX_IMPORT_ROWS), joined)

    def test_at_limit_csv_is_accepted(self):
        form = _MfrImportForm(
            data={
                "import_format": "csv",
                "active_tab": "editor",
                "delimiter": ",",
                "import_text": self._build_csv(MAX_IMPORT_ROWS),
            }
        )
        # Exactly MAX_IMPORT_ROWS rows must not trip the cap.
        self.assertTrue(form.is_valid(), form.errors)


class SearchLookupAllowlistTests(TestCase):
    """G5: unsafe regex lookups fall back before reaching the search backend."""

    def test_regex_lookup_is_replaced_before_backend_invocation(self):
        from itambox.views.utility import SearchView

        calls = []

        class Backend:
            def search(self, query, *, user, obj_types, lookup):
                calls.append((query, user, obj_types, lookup))
                return {}

        request = RequestFactory().get("/search/?q=needle&lookup=regex&obj_type=Asset")
        request.user = SimpleNamespace(is_authenticated=True)
        with patch("itambox.views.utility.import_string", return_value=Backend):
            response = SearchView().get(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [("needle", request.user, ["Asset"], "icontains")])
