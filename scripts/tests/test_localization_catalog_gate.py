from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_localization_catalog.py"
spec = importlib.util.spec_from_file_location("check_localization_catalog", SCRIPT)
assert spec and spec.loader
MODULE = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = MODULE
spec.loader.exec_module(MODULE)


class LocalizationCatalogGateTests(unittest.TestCase):
    def test_blocktranslate_literal_percent_matches_gettext_identity(self):
        self.assertEqual(
            MODULE.block_identity("{{ allocated }}/{{ total }} seats ({{ pct }}%)", False)[0],
            "%(allocated)s/%(total)s seats (%(pct)s%%)",
        )

    def test_entry_failures_detect_placeholder_and_ordered_html_mismatch(self):
        source = {
            "msgid": "<strong>%(name)s</strong> <em>ready</em>",
            "plural": None,
        }
        entry = {"msgstr": "<em>%(name)s</em> <strong>bereit</strong>", "flags": set()}
        failures = MODULE.entry_failures("django", "example", entry, source)
        self.assertTrue(any("HTML mismatch" in failure for failure in failures))

        entry = {"msgstr": "<strong>bereit</strong>", "flags": set()}
        failures = MODULE.entry_failures("django", "example", entry, source)
        self.assertTrue(any("placeholder mismatch" in failure for failure in failures))

    def test_plural_entry_requires_all_active_forms(self):
        source = {"msgid": "One asset", "plural": "%(count)s assets"}
        entry = {"msgid_plural": "%(count)s assets", "0": "Ein Asset", "1": "", "flags": set()}
        failures = MODULE.entry_failures("django", "One asset", entry, source)
        self.assertEqual(failures, ["django: empty 'One asset'"])

    def test_plural_identity_mismatch_is_rejected(self):
        source = {"msgid": "One asset", "plural": "%(count)s assets"}
        entry = {
            "msgid_plural": "%(count)s items",
            "0": "Ein Asset",
            "1": "%(count)s Assets",
            "flags": set(),
        }
        failures = MODULE.entry_failures("django", "One asset", entry, source)
        self.assertIn("django: plural identity mismatch 'One asset'", failures)

    def test_placeholder_multiplicity_mismatch_is_rejected(self):
        source = {"msgid": "%(name)s: %(name)s", "plural": None}
        entry = {"msgstr": "%(name)s: Name", "flags": set()}
        failures = MODULE.entry_failures("django", "example", entry, source)
        self.assertIn("django: placeholder mismatch 'example'", failures)

    def test_brace_placeholder_multiplicity_mismatch_is_rejected(self):
        source = {"msgid": "Open {name} from {name} at {url}", "plural": None}
        entry = {"msgstr": "{name} unter {url} öffnen", "flags": set()}
        failures = MODULE.entry_failures("django", "example", entry, source)
        self.assertIn("django: placeholder mismatch 'example'", failures)

    def test_plural_shape_mismatch_does_not_crash(self):
        source = {"msgid": "One asset", "plural": "%(count)s assets"}
        entry = {"msgstr": "Ein Asset", "flags": set()}
        failures = MODULE.entry_failures("django", "One asset", entry, source)
        self.assertEqual(failures, ["django: plural identity mismatch 'One asset'"])

    def test_singular_source_with_plural_catalog_entry_does_not_crash(self):
        source = {"msgid": "Asset", "plural": None}
        entry = {"msgid_plural": "Assets", "0": "Asset", "1": "Assets", "flags": set()}
        failures = MODULE.entry_failures("django", "Asset", entry, source)
        self.assertEqual(failures, ["django: plural identity mismatch 'Asset'"])

    def test_runtime_only_entries_are_checked_for_empty_and_fuzzy_flags(self):
        entries = {key: {"msgstr": key, "flags": set()} for key in MODULE.JS_RUNTIME_KEYS}
        entries["Supplier"] = {"msgstr": "", "flags": {"fuzzy"}}
        failures = MODULE.catalog_failures("djangojs", entries, [], {})
        self.assertIn("djangojs: fuzzy 'Supplier'", failures)
        self.assertIn("djangojs: empty 'Supplier'", failures)

    def test_documented_runtime_keys_are_required(self):
        failures = MODULE.catalog_failures("djangojs", {}, [], {})
        self.assertTrue(any("missing documented runtime keys" in failure for failure in failures))

    def test_escape_and_newline_shape_mismatch_is_rejected(self):
        source = {"msgid": "Hello\nWorld", "plural": None}
        entry = {"msgstr": "Hallo\\nWelt", "flags": set()}
        failures = MODULE.entry_failures("django", "example", entry, source)
        self.assertEqual(failures, ["django: escape/newline mismatch 'example'"])

    def test_escape_shape_preserves_order_not_only_counts(self):
        source = {"msgid": "First\nSecond\\tThird", "plural": None}
        entry = {"msgstr": "Erste\\tZweite\nDritte", "flags": set()}
        failures = MODULE.entry_failures("django", "example", entry, source)
        self.assertEqual(failures, ["django: escape/newline mismatch 'example'"])

    def test_js_string_scanner_handles_escapes_without_backtracking(self):
        text = 'gettext("A \\"quoted\\" value") + gettext(\'B\\\\\\\'s value\')'
        self.assertEqual(
            list(MODULE.iter_js_string_literals(text)),
            ['"A \\"quoted\\" value"', "'B\\\\\\'s value'"],
        )

    def test_constant_source_extraction_handles_python_and_javascript_concatenation(self):
        with tempfile.TemporaryDirectory() as directory:
            tmp_path = Path(directory)
            sources = {}
            python_path = tmp_path / "copy.py"
            python_path.write_text("gettext('Stock ' + 'status')", encoding="utf-8")
            MODULE.source_python(python_path, "itambox/example/copy.py", sources)
            javascript_path = tmp_path / "copy.ts"
            javascript_path.write_text(
                "ngettext('One ' + 'day', '%(count)s ' + 'days', count)",
                encoding="utf-8",
            )
            MODULE.source_javascript(javascript_path, "itambox/static/src/copy.ts", sources)
            self.assertIn(("django", "Stock status"), sources)
            self.assertEqual(sources[("djangojs", "One day")]["plural"], "%(count)s days")

    def test_python_gettext_aliases_are_extracted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "copy.py"
            path.write_text("_lazy('Bookmarks')", encoding="utf-8")
            sources = {}

            MODULE.source_python(path, "itambox/users/views.py", sources)

            self.assertIn(("django", "Bookmarks"), sources)

    def test_template_source_extraction_ignores_non_rendered_comments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "copy.html"
            path.write_text(
                '{# {% translate "Hidden" %} #}\n<!-- {% translate "Also hidden" %} -->\n{% translate "Visible" %}',
                encoding="utf-8",
            )
            sources = {}
            MODULE.source_templates(path, "itambox/templates/copy.html", sources)
            self.assertEqual(set(sources), {("django", "Visible")})

    def test_current_catalog_passes_source_contract(self):
        self.assertEqual(MODULE.main(), 0)


if __name__ == "__main__":
    unittest.main()
