from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_localization_catalog.py"
spec = importlib.util.spec_from_file_location("check_localization_catalog", SCRIPT)
assert spec and spec.loader
MODULE = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = MODULE
spec.loader.exec_module(MODULE)


def test_blocktranslate_literal_percent_matches_gettext_identity():
    assert MODULE.block_identity("{{ allocated }}/{{ total }} seats ({{ pct }}%)", False)[0] == (
        "%(allocated)s/%(total)s seats (%(pct)s%%)"
    )


def test_entry_failures_detect_placeholder_and_ordered_html_mismatch():
    source = {
        "msgid": "<strong>%(name)s</strong> <em>ready</em>",
        "plural": None,
    }
    entry = {"msgstr": "<em>%(name)s</em> <strong>bereit</strong>", "flags": set()}
    failures = MODULE.entry_failures("django", "example", entry, source)
    assert any("HTML mismatch" in failure for failure in failures)

    entry = {"msgstr": "<strong>bereit</strong>", "flags": set()}
    failures = MODULE.entry_failures("django", "example", entry, source)
    assert any("placeholder mismatch" in failure for failure in failures)


def test_plural_entry_requires_all_active_forms():
    source = {"msgid": "One asset", "plural": "%(count)s assets"}
    entry = {"msgid_plural": "%(count)s assets", "0": "Ein Asset", "1": "", "flags": set()}
    failures = MODULE.entry_failures("django", "One asset", entry, source)
    assert failures == ["django: empty 'One asset'"]


def test_escape_and_newline_shape_mismatch_is_rejected():
    source = {"msgid": "Hello\nWorld", "plural": None}
    entry = {"msgstr": "Hallo\\nWelt", "flags": set()}
    failures = MODULE.entry_failures("django", "example", entry, source)
    assert failures == ["django: escape/newline mismatch 'example'"]


def test_current_catalog_passes_source_contract():
    assert MODULE.main() == 0
