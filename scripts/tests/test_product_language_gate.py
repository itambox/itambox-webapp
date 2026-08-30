from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "check_product_language.py"
spec = importlib.util.spec_from_file_location("check_product_language", SCRIPT)
assert spec and spec.loader
MODULE = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = MODULE
spec.loader.exec_module(MODULE)


def write(tmp_path: Path, name: str, source: str) -> Path:
    path = tmp_path / name
    path.write_text(source, encoding="utf-8")
    return path


def test_forbidden_tokens_cover_unicode_entities_and_escape_notation():
    samples = (
        "plain – dash",
        "plain — dash",
        "&NDASH;",
        "&#8212;",
        "&#x02013;",
        r"\u2014",
        r"\u{2013}",
        r"\U00002014",
        r"\N{EN DASH}",
    )
    assert all(MODULE.forbidden_tokens(sample) for sample in samples)


def test_python_scan_rejects_concatenation_chr_join_format_and_f_string(tmp_path):
    path = write(
        tmp_path,
        "copy.py",
        "\n".join(
            (
                "_('left ' + chr(0x2013) + ' right')",
                "gettext(''.join(['left &n', 'dash; right']))",
                "gettext('left &#x{:x}; right'.format(8212))",
                "format_html(f'left {chr(8211)} right')",
            )
        ),
    )
    findings = MODULE.scan_python(path, "itambox/example/copy.py")
    assert [finding.line for finding in findings] == [1, 2, 3, 4]


def test_javascript_scan_rejects_split_entities_and_codepoint_construction(tmp_path):
    path = write(
        tmp_path,
        "copy.ts",
        "gettext('left &n' + 'dash; right');\ngettext('left ' + String.fromCodePoint(0x2014) + ' right');",
    )
    findings = MODULE.scan_javascript(path, "itambox/static/src/copy.ts")
    assert [finding.line for finding in findings] == [1, 2]


def test_template_scan_ignores_comments_but_checks_rendered_entities(tmp_path):
    path = write(
        tmp_path,
        "copy.html",
        "{# hidden — copy #}\n<!-- hidden &mdash; copy -->\n<span>visible &#x2013; copy</span>",
    )
    findings = MODULE.scan_template(path, "itambox/templates/copy.html")
    assert [(finding.line, finding.field) for finding in findings] == [(3, "rendered template")]


def test_po_scan_checks_translation_even_when_exact_contract_msgid_is_allowed(tmp_path):
    relative, msgid = next(iter(MODULE.FROZEN_CONTRACT_COPY))
    del relative
    path = write(
        tmp_path,
        "django.po",
        'msgid %s\nmsgstr "Deutsch — nicht erlaubt"\n' % json.dumps(msgid, ensure_ascii=False),
    )
    findings = MODULE.scan_po(path, "itambox/locale/de/LC_MESSAGES/django.po")
    assert [(finding.field, finding.tokens) for finding in findings] == [("msgstr", ("em dash",))]


def test_frozen_metadata_allowlist_is_path_and_message_specific(tmp_path):
    relative, msgid = next(iter(MODULE.FROZEN_CONTRACT_COPY))
    path = write(tmp_path, "model.py", f"_({msgid!r})\n")
    assert MODULE.scan_python(path, relative) == []
    assert MODULE.scan_python(path, "itambox/other/models.py")


def test_current_repository_passes_product_language_gate():
    assert MODULE.main() == 0
