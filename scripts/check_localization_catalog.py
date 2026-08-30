"""Validate source/catalog parity and localization contracts.

This is intentionally a small, dependency-free extractor for the product
language surfaces used by ITAMbox. It does not regenerate catalogs. Django
uses ``%(name)s`` identities for blocktranslate variables, so the extractor
normalizes those identities before comparing them with the PO files.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PO_PATHS = {
    "django": ROOT / "itambox/locale/de/LC_MESSAGES/django.po",
    "djangojs": ROOT / "itambox/locale/de/LC_MESSAGES/djangojs.po",
}
PY_CALLS = {"_", "gettext", "gettext_lazy", "gettext_noop", "ngettext", "pgettext", "npgettext"}
JS_CALL_START = re.compile(r"\b(gettext|ngettext|pgettext|npgettext)\s*\(")
BLOCK = re.compile(
    r"{%[-+]?\s*blocktrans(?:late)?(?P<opts>[^%]*)%}(?P<body>.*?){%[-+]?\s*endblocktrans(?:late)?\s*[-+]?%}",
    re.S,
)
TRANS = re.compile(r"{%[-+]?\s*(?:trans|translate)\s+(?P<value>['\"])(?P<text>.*?)(?P=value)[^%]*%}", re.S)
PLACEHOLDER = re.compile(r"%(?:\([^)]+\)|[0-9]+)?[a-zA-Z%]")
BRACE_PLACEHOLDER = re.compile(r"(?<!{){([A-Za-z_]\w*(?:![rsa])?(?::[^{}]+)?)\}(?!})")
ESCAPE_SEQUENCE = re.compile(r"\r\n|\r|\n|\\[nrt\\]")
HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")


def html_skeleton(value: str) -> list[str]:
    return [re.sub(r"\s+", " ", tag).strip() for tag in HTML_TAG.findall(value)]


# These labels are served by the JS catalog to the existing form/runtime
# contract even though they are not all literal gettext calls in static/src.
JS_RUNTIME_KEYS = {
    "Supplier",
    "Start Date",
    "Renewal Date",
    "Covered Assets",
    "Warranty Type",
    "Warranty Cost",
    "Reference",
    "Sanitization Certificate",
    "Recipient",
    "Notes",
    "Item Type",
    "Name",
    "Category",
    "Part Number",
    "Total Stock",
    "Available",
    "Safety Threshold",
    "Stock Status",
    "Holder",
    "Signature Provider",
    "Created Date",
    "IP Address",
    "Matching",
    "Mismatch",
    "Surprise",
    "This asset cannot be audited.",
    "loading",
    "no_results",
    "not_loading",
    "option_create",
}


def unquote(value: str) -> str:
    return ast.literal_eval(value.strip())


def parse_po_line(line: str, fields: dict, flags: set[str], section: str | None) -> str | None:
    if not line:
        return section
    if line.startswith("#, "):
        flags.update(line[3:].split(", "))
        return section
    if line.startswith("msgctxt "):
        fields["msgctxt"] = unquote(line[8:])
        return "msgctxt"
    if line.startswith("msgid_plural "):
        fields["msgid_plural"] = unquote(line[13:])
        return "msgid_plural"
    if line.startswith("msgid "):
        fields["msgid"] = unquote(line[6:])
        return "msgid"
    if line.startswith("msgstr["):
        end = line.index("]")
        section = line[7:end]
        fields[section] = unquote(line[end + 1 :])
        return section
    if line.startswith("msgstr "):
        fields["msgstr"] = unquote(line[7:])
        return "msgstr"
    if line.startswith('"') and section is not None:
        fields[section] = str(fields.get(section, "")) + unquote(line)
    return section


def parse_po_block(block: str) -> tuple[dict, set[str]]:
    fields: dict[str, str | list[str]] = {"msgstr": ""}
    flags: set[str] = set()
    section = None
    for raw in block.splitlines():
        section = parse_po_line(raw.strip(), fields, flags, section)
    return fields, flags


def parse_po(path: Path) -> tuple[dict[str, dict], list[str]]:
    entries: dict[str, dict] = {}
    duplicates: list[str] = []
    for block in path.read_text(encoding="utf-8-sig").split("\n\n"):
        if not block.strip() or block.lstrip().startswith("#~"):
            continue
        fields, flags = parse_po_block(block)
        msgid = fields.get("msgid")
        if not isinstance(msgid, str) or not msgid:
            continue
        context = fields.get("msgctxt")
        key = f"{context}\x04{msgid}" if context else msgid
        fields["flags"] = flags
        if key in entries:
            duplicates.append(key)
        else:
            entries[key] = fields
    return entries, duplicates


def source_key(msgid: str, context: str | None = None) -> str:
    return f"{context}\x04{msgid}" if context else msgid


def add_source(
    sources: dict[str, dict],
    domain: str,
    msgid: str,
    *,
    plural: str | None = None,
    context: str | None = None,
    path: str = "",
) -> None:
    if not msgid:
        return
    identity = (domain, source_key(msgid, context))
    record = sources.setdefault(
        identity, {"domain": domain, "key": source_key(msgid, context), "msgid": msgid, "plural": plural, "paths": []}
    )
    if plural:
        record["plural"] = plural
    if path and path not in record["paths"]:
        record["paths"].append(path)


def source_python(path: Path, relative: str, sources: dict[str, dict]) -> None:
    parts = set(Path(relative).parts)
    if parts & {"tests", "migrations", "docs", "locale"} or relative.startswith("scripts/"):
        return
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=relative)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id not in PY_CALLS:
            continue
        args = [constant_python_text(arg) for arg in node.args]
        if node.func.id in {"pgettext", "npgettext"}:
            if len(args) > 1 and args[1] is not None:
                add_source(
                    sources,
                    "django",
                    args[1],
                    plural=args[2] if node.func.id == "npgettext" and len(args) > 2 else None,
                    context=args[0],
                    path=relative,
                )
        elif args and args[0] is not None:
            add_source(
                sources,
                "django",
                args[0],
                plural=args[1] if node.func.id == "ngettext" and len(args) > 1 else None,
                path=relative,
            )


def constant_python_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = constant_python_text(node.left)
        right = constant_python_text(node.right)
        return left + right if left is not None and right is not None else None
    if isinstance(node, ast.JoinedStr):
        values = [constant_python_text(value) for value in node.values]
        if all(value is not None for value in values):
            return "".join(values)  # type: ignore[arg-type]
    return None


def block_identity(body: str, trimmed: bool) -> tuple[str, str | None]:
    parts = re.split(r"{%[-+]?\s*plural\s*[-+]?%}", body, maxsplit=1)

    def convert(value: str) -> str:
        value = re.sub(r"{{\s*([A-Za-z_]\w*)\s*}}", r"%(\1)s", value)
        return re.sub(r"%(?!\([^)]+\)[a-zA-Z]|[a-zA-Z]|%)", "%%", value)

    values = [convert(value) for value in parts]
    if trimmed:
        values = [" ".join(value.split()) for value in values]
    return values[0], values[1] if len(values) == 2 else None


def source_templates(path: Path, relative: str, sources: dict[str, dict]) -> None:
    if set(Path(relative).parts) & {"tests", "docs", "locale"}:
        return
    text = strip_template_comments(path.read_text(encoding="utf-8-sig"))
    for match in TRANS.finditer(text):
        add_source(sources, "django", match.group("text"), path=relative)
    for match in BLOCK.finditer(text):
        singular, plural = block_identity(match.group("body"), "trimmed" in match.group("opts"))
        context_match = re.search(r"\bcontext\s+['\"](.*?)['\"]", match.group("opts"))
        add_source(
            sources,
            "django",
            singular,
            plural=plural,
            context=context_match.group(1) if context_match else None,
            path=relative,
        )


def strip_template_comments(source: str) -> str:
    for pattern in (
        r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}",
        r"{#.*?#}",
        r"<!--.*?-->",
    ):
        source = re.sub(pattern, lambda match: "\n" * match.group(0).count("\n"), source, flags=re.S)
    return source


def iter_js_string_literals(text: str):
    """Yield quoted JS string literals with linear-time scanning."""
    index = 0
    while index < len(text):
        if text[index] not in {"'", '"'}:
            index += 1
            continue
        quote = text[index]
        start = index
        index += 1
        escaped = False
        while index < len(text):
            char = text[index]
            index += 1
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                yield text[start:index]
                break


def update_js_quote(char: str, quote: str | None, escaped: bool) -> tuple[str | None, bool]:
    if escaped:
        return quote, False
    if char == "\\":
        return quote, True
    if char == quote:
        return None, False
    return quote, False


def find_js_call_end(text: str, start: int) -> int | None:
    depth = 1
    quote = None
    escaped = False
    index = start
    while index < len(text) and depth:
        char = text[index]
        if quote:
            quote, escaped = update_js_quote(char, quote, escaped)
        elif char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        index += 1
    return index if depth == 0 else None


def iter_js_calls(text: str):
    for match in JS_CALL_START.finditer(text):
        end = find_js_call_end(text, match.end())
        if end is not None:
            yield match, text[match.end() : end - 1]


def split_js_arguments(text: str) -> list[str]:
    arguments = []
    start = 0
    depth = 0
    quote = None
    escaped = False
    for index, char in enumerate(text):
        if quote:
            quote, escaped = update_js_quote(char, quote, escaped)
        elif char in {"'", '"', "`"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}" and depth:
            depth -= 1
        elif char == "," and depth == 0:
            arguments.append(text[start:index])
            start = index + 1
    arguments.append(text[start:])
    return arguments


def constant_js_text(argument: str) -> str | None:
    literals = list(iter_js_string_literals(argument))
    if not literals:
        return None
    remainder = argument
    values = []
    for literal in literals:
        if literal.startswith("`"):
            if "${" in literal:
                return None
            value = literal[1:-1]
        else:
            try:
                value = ast.literal_eval(literal)
            except (SyntaxError, ValueError):
                return None
        values.append(value)
        remainder = remainder.replace(literal, "", 1)
    if re.sub(r"[+\s]", "", remainder):
        return None
    return "".join(values)


def source_javascript(path: Path, relative: str, sources: dict[str, dict]) -> None:
    text = path.read_text(encoding="utf-8-sig")
    for call, body in iter_js_calls(text):
        arguments = [constant_js_text(argument) for argument in split_js_arguments(body)]
        name = call.group(1)
        if name in {"pgettext", "npgettext"} and len(arguments) >= 2 and arguments[0] and arguments[1]:
            add_source(
                sources,
                "djangojs",
                arguments[1],
                plural=arguments[2] if name == "npgettext" and len(arguments) > 2 else None,
                context=arguments[0],
                path=relative,
            )
        elif arguments and arguments[0]:
            add_source(
                sources,
                "djangojs",
                arguments[0],
                plural=arguments[1] if name == "ngettext" and len(arguments) > 1 else None,
                path=relative,
            )


def all_sources() -> dict[str, dict]:
    sources: dict[str, dict] = {}
    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    for relative in tracked:
        relative = relative.replace("\\", "/")
        path = ROOT / relative
        if not path.is_file():
            continue
        if relative.endswith(".py"):
            source_python(path, relative, sources)
        elif relative.endswith(".html"):
            source_templates(path, relative, sources)
        elif relative.startswith("itambox/static/src/") and path.suffix in {".js", ".jsx", ".ts", ".tsx"}:
            source_javascript(path, relative, sources)
    return sources


def placeholders(value: str) -> Counter[str]:
    values = [item for item in PLACEHOLDER.findall(value) if item != "%%"]
    values.extend(f"{{{item}}}" for item in BRACE_PLACEHOLDER.findall(value))
    return Counter(values)


def escape_shape(value: str) -> tuple[str, ...]:
    return tuple(ESCAPE_SEQUENCE.findall(value))


def translated_values(entry: dict) -> list[str]:
    if "msgid_plural" not in entry:
        return [str(entry.get("msgstr", ""))]
    return [str(entry.get("0", "")), str(entry.get("1", ""))]


def entry_failures(domain: str, key: str, entry: dict, source: dict) -> list[str]:
    failures = []
    if "fuzzy" in entry.get("flags", set()):
        failures.append(f"{domain}: fuzzy {key!r}")
    expected_plural = source.get("plural")
    actual_plural = entry.get("msgid_plural")
    if expected_plural != actual_plural:
        failures.append(f"{domain}: plural identity mismatch {key!r}")
        return failures
    values = translated_values(entry)
    if any(value == "" for value in values):
        failures.append(f"{domain}: empty {key!r}")
    source_values = [source["msgid"]]
    if source.get("plural"):
        source_values.append(source["plural"])
    for source_value, translated in zip(source_values, values, strict=True):
        if not translated:
            continue
        if placeholders(source_value) != placeholders(translated):
            failures.append(f"{domain}: placeholder mismatch {key!r}")
        if escape_shape(source_value) != escape_shape(translated):
            failures.append(f"{domain}: escape/newline mismatch {key!r}")
        if html_skeleton(source_value) != html_skeleton(translated):
            failures.append(f"{domain}: HTML mismatch {key!r}")
    return failures


def catalog_failures(domain: str, entries: dict, duplicates: list[str], sources: dict) -> list[str]:
    failures = [f"{domain}: duplicate keys: {sorted(set(duplicates))}"] if duplicates else []
    expected = {record["key"] for record in sources.values() if record["domain"] == domain}
    source_by_key = {record["key"]: record for record in sources.values() if record["domain"] == domain}
    actual = set(entries)
    allowed = JS_RUNTIME_KEYS if domain == "djangojs" else set()
    missing = sorted(expected - actual)
    stale = sorted(actual - expected - allowed)
    if domain == "djangojs":
        runtime_missing = sorted(JS_RUNTIME_KEYS - actual)
        if runtime_missing:
            failures.append(f"{domain}: missing documented runtime keys {runtime_missing}")
        runtime_only = sorted((JS_RUNTIME_KEYS & actual) - expected)
        for key in runtime_only:
            failures.extend(entry_failures(domain, key, entries[key], {"msgid": key, "plural": None}))
    if missing:
        failures.append(f"{domain}: missing {missing[:10]}" + (" ..." if len(missing) > 10 else ""))
    if stale:
        failures.append(f"{domain}: stale {stale[:10]}" + (" ..." if len(stale) > 10 else ""))
    for key in sorted(expected & actual):
        failures.extend(entry_failures(domain, key, entries[key], source_by_key[key]))
    return failures


def main() -> int:
    sources = all_sources()
    catalogs = {domain: parse_po(path) for domain, path in PO_PATHS.items()}
    failures = []
    for domain, (entries, duplicates) in catalogs.items():
        failures.extend(catalog_failures(domain, entries, duplicates, sources))
    if failures:
        print("Localization catalog gate failed:")
        print("\n".join(f"- {failure}" for failure in failures))
        return 1
    active_catalogs = sum(len(entries) for entries, _ in catalogs.values())
    print(
        f"Localization catalog gate passed: {len(sources)} source keys, "
        f"{active_catalogs} active catalog keys, {len(JS_RUNTIME_KEYS)} documented JS runtime keys."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
