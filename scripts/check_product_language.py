"""Fail when user-facing product language contains en or em dashes.

The scan is deliberately scope-aware:
* productive Python gettext/ngettext calls only;
* rendered HTML after Django/HTML/CSS comment blocks are removed;
* active gettext message fields in the German django/djangojs catalogs.

Developer docs, comments, tests, migrations, and historical prose are not product
language and are intentionally excluded.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("\u2013", "\u2014", "&ndash;", "&mdash;", "&#8211;", "&#8212;", r"\u2013", r"\u2014")
TRANSLATION_CALLS = {
    "_": (0,),
    "gettext": (0,),
    "gettext_lazy": (0,),
    "gettext_noop": (0,),
    "ngettext": (0, 1),
    "pgettext": (1,),
    "npgettext": (1, 2),
}
JS_TRANSLATION_CALL = re.compile(r"\b(?:gettext|ngettext|pgettext|npgettext)\s*\((?P<body>[^)]*)\)", re.S)
JS_STRING = re.compile(r"'(?:\\.|[^'\\\\\\n])*'|\"(?:\\.|[^\"\\\\\\n])*\"")
EXCLUDED_PARTS = {"tests", "migrations", "docs", "static", "locale"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    field: str
    tokens: tuple[str, ...]


def forbidden_tokens(value: str) -> tuple[str, ...]:
    return tuple(token for token in FORBIDDEN if token in value)


def tracked_files() -> list[str]:
    output = subprocess.check_output(["git", "ls-files"], cwd=REPOSITORY_ROOT, text=True)
    return output.splitlines()


def scan_python(path: Path, relative: str) -> list[Finding]:
    if set(Path(relative).parts) & EXCLUDED_PARTS or relative.startswith("scripts/"):
        return []
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=relative)
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        positions = TRANSLATION_CALLS.get(node.func.id)
        if positions is None:
            continue
        for position in positions:
            if position >= len(node.args):
                continue
            argument = node.args[position]
            if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                continue
            tokens = forbidden_tokens(argument.value)
            if tokens:
                findings.append(Finding(relative, node.lineno, node.func.id, tokens))
    return findings


def strip_template_comments(source: str) -> str:
    for pattern in (
        r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}",
        r"{#.*?#}",
        r"<!--.*?-->",
        r"/\*.*?\*/",
    ):
        source = re.sub(pattern, lambda match: "\n" * match.group(0).count("\n"), source, flags=re.S)
    return source


def scan_template(path: Path, relative: str) -> list[Finding]:
    source = strip_template_comments(path.read_text(encoding="utf-8-sig"))
    findings = []
    for line_number, line in enumerate(source.splitlines(), 1):
        tokens = forbidden_tokens(line)
        if tokens:
            findings.append(Finding(relative, line_number, "rendered template", tokens))
    return findings


def scan_javascript(path: Path, relative: str) -> list[Finding]:
    source = path.read_text(encoding="utf-8-sig")
    findings = []
    for call in JS_TRANSLATION_CALL.finditer(source):
        body = call.group("body")
        line_number = source.count("\n", 0, call.start()) + 1
        for literal in JS_STRING.findall(body):
            try:
                value = ast.literal_eval(literal)
            except (SyntaxError, ValueError):
                continue
            tokens = forbidden_tokens(value)
            if tokens:
                findings.append(Finding(relative, line_number, "translation call", tokens))
    return findings


def scan_po(path: Path, relative: str) -> list[Finding]:
    findings = []
    obsolete_entry = False
    active_field = None
    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line:
            obsolete_entry = False
            active_field = None
            continue
        if line.startswith("#~"):
            obsolete_entry = True
            continue
        if obsolete_entry or line.startswith("#"):
            continue
        match = re.match(r"(msgctxt|msgid_plural|msgid|msgstr(?:\[\d+\])?)\s+(.*)$", line)
        if match:
            active_field, literal = match.groups()
        elif line.startswith('"') and active_field is not None:
            literal = line
        else:
            continue
        try:
            value = ast.literal_eval(literal)
        except (SyntaxError, ValueError):
            value = literal
        tokens = forbidden_tokens(value)
        if tokens:
            findings.append(Finding(relative, line_number, active_field, tokens))
    return findings


def scan_repository() -> tuple[list[Finding], dict[str, int]]:
    findings = []
    counts = {"python": 0, "templates": 0, "javascript": 0, "catalogs": 0}
    for relative in tracked_files():
        path = REPOSITORY_ROOT / relative
        if not path.is_file():
            continue
        if relative.endswith(".py"):
            counts["python"] += 1
            findings.extend(scan_python(path, relative))
        elif relative.endswith(".html"):
            counts["templates"] += 1
            findings.extend(scan_template(path, relative))
        elif relative.startswith("itambox/static/src/") and Path(relative).suffix in {".js", ".jsx", ".ts", ".tsx"}:
            counts["javascript"] += 1
            findings.extend(scan_javascript(path, relative))
        elif relative in {
            "itambox/locale/de/LC_MESSAGES/django.po",
            "itambox/locale/de/LC_MESSAGES/djangojs.po",
        }:
            counts["catalogs"] += 1
            findings.extend(scan_po(path, relative))
    return findings, counts


def main() -> int:
    findings, counts = scan_repository()
    if findings:
        print("User-facing product language contains forbidden en/em dash forms:")
        for finding in findings:
            print(f"  {finding.path}:{finding.line} [{finding.field}] {', '.join(finding.tokens)}")
        return 1
    print(
        "Product-language dash gate passed: "
        f"{counts['python']} Python files, {counts['templates']} templates, "
        f"{counts['javascript']} JavaScript/TypeScript files, {counts['catalogs']} catalogs scanned."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
