#!/usr/bin/env python
"""Fail-closed, identity-based ratchet for the escape hatches that bypass invariants.

Three patterns let code step around tenant scoping, the provider-slot seam, or
the behaviour of the code under test. None of them is wrong in every place, but
each one removes a guarantee a reader would otherwise rely on, so a *new*
occurrence must say why in place or the gate fails:

* ``unscoped`` -- ``X._base_manager`` / ``X.all_objects`` inside a view, form,
  serializer or service module. Annotate with ``# unscoped: <reason>``.
* ``provider-slot`` -- a new ``SingleProviderSlot[...]("name")`` instance.
  Annotate with ``# provider-slot: <reason>``.
* ``source-text`` -- ``inspect.getsource`` / ``ast.parse`` inside a test module
  (``scripts/tests`` is exempt: those suites test the gates themselves).
  Annotate with ``# source-text: <reason>``.

Broad ``except`` handlers are deliberately not a rule here:
``scripts/check_exception_policy.py`` already ratchets them, and bare handlers
are refused by flake8 E722.

The design mirrors ``check_local_imports.py``. The gate is deterministic and
AST-based: an annotated occurrence is reviewed, an unannotated one is held
against a checked-in identity baseline keyed by rule, path, enclosing scope path
and the normalised expression -- never by line number. A new identity is always
a regression; a removed identity makes the baseline stale, so paid-down debt
never becomes headroom for new debt. The gate cannot judge whether a reason is
*true*, only that a reviewer can read one.

The canonical baseline is generated with Python 3.12 and the gate refuses to
run on any other interpreter, because ``ast.unparse`` output is
version-sensitive.
"""

import argparse
import ast
import collections
import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "scripts" / "escape_hatch_baseline.json"
DEFAULT_TARGETS = ["itambox", "scripts"]
SCHEMA_VERSION = 1
CANONICAL_PYTHON = (3, 12)

RULE_UNSCOPED = "unscoped"
RULE_PROVIDER_SLOT = "provider-slot"
RULE_SOURCE_TEXT = "source-text"

# rule -> what an in-place reason has to justify.
RULES = {
    RULE_UNSCOPED: "reads or writes through an unscoped manager (_base_manager / all_objects)",
    RULE_PROVIDER_SLOT: "adds a single-implementation provider slot",
    RULE_SOURCE_TEXT: "asserts on source text (inspect.getsource / ast.parse) in a test module",
}

# ``# <rule>: <reason>``
MARKER_PATTERN = r"\b(?P<rule>unscoped|provider-slot|source-text)\s*:"
ANNOTATION_PATTERN = r"\b(?P<rule>unscoped|provider-slot|source-text)\s*:\s*(?P<reason>\S.*?)\s*$"
MARKER_RE = re.compile(MARKER_PATTERN)
ANNOTATION_RE = re.compile(ANNOTATION_PATTERN)

UNSCOPED_ATTRIBUTES = frozenset({"_base_manager", "all_objects"})
# Words in a path component (split on ``_`` and ``.``) that put a module in the
# presentation or service layer the unscoped rule governs.
UNSCOPED_LAYER_WORDS = frozenset({"view", "views", "form", "forms", "serializer", "serializers", "service", "services"})
PROVIDER_SLOT_NAME = "SingleProviderSlot"
SOURCE_TEXT_ATTRIBUTES = {
    "inspect": frozenset({"getsource", "getsourcelines"}),
    "ast": frozenset({"parse"}),
}
SOURCE_TEXT_NAMES = frozenset({"getsource", "getsourcelines"})

# Generated and vendored paths. ``tests`` is handled per rule below.
EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "migrations",
        "static",
        "docs",
    }
)
TEST_DIRECTORY_NAMES = frozenset({"tests"})
TEST_FILE_NAMES = frozenset({"conftest.py", "tests.py"})
TEST_FILE_PREFIXES = ("test_",)
# Suites that test the gates themselves necessarily parse source.
SOURCE_TEXT_EXEMPT_PREFIXES = ("scripts/tests/",)


class PolicyError(Exception):
    """Raised when the gate cannot produce a trustworthy result."""


MalformedAnnotation = collections.namedtuple("MalformedAnnotation", "path line comment problem")
ScanResult = collections.namedtuple("ScanResult", "findings annotated malformed")


def is_test_path(relative_path):
    parts = relative_path.split("/")
    name = parts[-1]
    return (
        bool(set(parts[:-1]) & TEST_DIRECTORY_NAMES) or name in TEST_FILE_NAMES or name.startswith(TEST_FILE_PREFIXES)
    )


def is_unscoped_layer(relative_path):
    parts = relative_path.split("/")
    parts[-1] = parts[-1].removesuffix(".py")
    words = set()
    for part in parts:
        words.update(re.split(r"[_.]", part))
    return bool(words & UNSCOPED_LAYER_WORDS)


def iter_source_files(root, targets):
    for target in targets:
        directory = root / target
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            relative_path = path.relative_to(root).as_posix()
            if set(relative_path.split("/")[:-1]) & EXCLUDED_DIRECTORY_NAMES:
                continue
            yield path, relative_path


def _scope_label(node):
    return f"{type(node).__name__}:{node.name}"


def _statement_span(statement):
    """Lines a trailing annotation may sit on: the header of a compound statement."""
    body = getattr(statement, "body", None)
    if isinstance(body, list) and body:
        return statement.lineno, max(statement.lineno, body[0].lineno - 1)
    return statement.lineno, statement.end_lineno


class _HatchCollector(ast.NodeVisitor):
    """Collect every rule hit with its enclosing scope path and statement."""

    def __init__(self, check_unscoped, check_provider_slot, check_source_text):
        self.check_unscoped = check_unscoped
        self.check_provider_slot = check_provider_slot
        self.check_source_text = check_source_text
        self.scope = []
        self.statements = []
        self.hits = []

    def visit(self, node):
        is_statement = isinstance(node, ast.stmt)
        if is_statement:
            self.statements.append(node)
        is_scope = isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        if is_scope:
            self.scope.append(node)
        super().visit(node)
        if is_scope:
            self.scope.pop()
        if is_statement:
            self.statements.pop()

    def _record(self, rule, node):
        context = "/".join(_scope_label(item) for item in self.scope)
        self.hits.append((rule, node, context, self.statements[-1] if self.statements else None))

    def visit_Attribute(self, node):
        if self.check_unscoped and node.attr in UNSCOPED_ATTRIBUTES:
            self._record(RULE_UNSCOPED, node)
        if self.check_source_text and self._is_source_text(node):
            self._record(RULE_SOURCE_TEXT, node)
        self.generic_visit(node)

    def visit_Call(self, node):
        func = node.func
        if self.check_provider_slot:
            target = func.value if isinstance(func, ast.Subscript) else func
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
            if name is not None and name.lstrip("_") == PROVIDER_SLOT_NAME:
                self._record(RULE_PROVIDER_SLOT, node)
        self.generic_visit(node)

    def _is_source_text(self, node):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return node.attr in SOURCE_TEXT_ATTRIBUTES.get(node.value.id, ())
        return isinstance(node, ast.Name) and node.id in SOURCE_TEXT_NAMES

    def visit_Name(self, node):
        if self.check_source_text and self._is_source_text(node):
            self._record(RULE_SOURCE_TEXT, node)
        self.generic_visit(node)


def _statement_comment(lines, statement):
    """Return the comment text written on the statement's own line(s)."""
    first, last = _statement_span(statement)
    for index in range(first - 1, last):
        if "#" in lines[index]:
            return lines[index].split("#", 1)[1].strip()
    return None


def _preceding_comment_block(lines, statement):
    collected = []
    index = statement.lineno - 2
    while index >= 0 and lines[index].strip().startswith("#"):
        collected.append(lines[index].strip().lstrip("#").strip())
        index -= 1
    return " ".join(reversed(collected)) if collected else None


def _classify_comment(comment, rule):
    """Return (annotated, problem) for a comment against one rule."""
    if comment is None:
        return False, None
    for marker in MARKER_RE.finditer(comment):
        if marker.group("rule") != rule:
            continue
        match = ANNOTATION_RE.search(comment[marker.start() :])
        if match is None:
            return False, f"justification must read '# {rule}: <reason>'"
        return True, None
    return False, None


def _find_annotation(lines, statement, rule):
    if statement is None:
        return False, None, None
    for comment in (_statement_comment(lines, statement), _preceding_comment_block(lines, statement)):
        annotated, problem = _classify_comment(comment, rule)
        if annotated or problem:
            return annotated, problem, comment
    return False, None, None


def _rules_for(relative_path):
    test_module = is_test_path(relative_path)
    return (
        not test_module and is_unscoped_layer(relative_path),
        not test_module,
        test_module and not relative_path.startswith(SOURCE_TEXT_EXEMPT_PREFIXES),
    )


def collect_findings(root, targets):
    """Scan sources for unannotated and annotated escape-hatch occurrences."""
    findings = collections.Counter()
    annotated = collections.Counter()
    malformed = []
    for path, relative_path in iter_source_files(root, targets):
        flags = _rules_for(relative_path)
        if not any(flags):
            continue
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise PolicyError(f"cannot read {relative_path}: {exc}") from exc
        try:
            tree = ast.parse(source, filename=relative_path)
        except SyntaxError as exc:
            raise PolicyError(f"cannot parse {relative_path}: {exc}") from exc
        collector = _HatchCollector(*flags)
        collector.visit(tree)
        lines = source.splitlines()
        for rule, node, context, statement in collector.hits:
            justified, problem, comment = _find_annotation(lines, statement, rule)
            if problem is not None:
                malformed.append(MalformedAnnotation(relative_path, node.lineno, comment, problem))
            elif justified:
                annotated[rule] += 1
            else:
                findings[(rule, relative_path, context, ast.unparse(node))] += 1
    return ScanResult(findings, annotated, malformed)


def compute_policy_fingerprint(targets):
    """Bind a baseline to the policy that produced it."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "canonical_python": f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}",
        "rules": sorted(RULES),
        "marker_pattern": MARKER_PATTERN,
        "annotation_pattern": ANNOTATION_PATTERN,
        "unscoped_attributes": sorted(UNSCOPED_ATTRIBUTES),
        "unscoped_layer_words": sorted(UNSCOPED_LAYER_WORDS),
        "provider_slot_name": PROVIDER_SLOT_NAME,
        "source_text_attributes": {key: sorted(value) for key, value in sorted(SOURCE_TEXT_ATTRIBUTES.items())},
        "source_text_names": sorted(SOURCE_TEXT_NAMES),
        "excluded_directory_names": sorted(EXCLUDED_DIRECTORY_NAMES),
        "test_directory_names": sorted(TEST_DIRECTORY_NAMES),
        "test_file_names": sorted(TEST_FILE_NAMES),
        "test_file_prefixes": sorted(TEST_FILE_PREFIXES),
        "source_text_exempt_prefixes": sorted(SOURCE_TEXT_EXEMPT_PREFIXES),
        "targets": list(targets),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_baseline_header(raw, expected_policy_fingerprint):
    required_top_level = {"schema_version", "canonical_python", "policy_sha256", "findings"}
    if not isinstance(raw, dict) or set(raw) != required_top_level:
        raise PolicyError("baseline has invalid top-level fields")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise PolicyError(f"expected escape-hatch baseline schema {SCHEMA_VERSION}")
    if raw["canonical_python"] != f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}":
        raise PolicyError(f"baseline canonical_python must be '{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}'")
    if raw["policy_sha256"] != expected_policy_fingerprint:
        raise PolicyError("baseline policy_sha256 does not match the effective escape-hatch policy")


def _validate_row(index, row):
    if not isinstance(row, dict) or set(row) != {"rule", "path", "context", "statement", "count"}:
        raise PolicyError(f"baseline finding {index} has invalid fields")
    count = row["count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise PolicyError(f"baseline finding {index} has invalid count")
    values = (row["rule"], row["path"], row["context"], row["statement"])
    if not all(isinstance(value, str) for value in values):
        raise PolicyError(f"baseline finding {index} has non-string identity")
    if values[0] not in RULES:
        raise PolicyError(f"baseline finding {index} names unknown rule {values[0]!r}")
    return values


def load_baseline(baseline_path, expected_policy_fingerprint):
    try:
        raw = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read baseline {baseline_path}: {exc}") from exc
    _validate_baseline_header(raw, expected_policy_fingerprint)
    rows = raw["findings"]
    if not isinstance(rows, list):
        raise PolicyError("baseline findings must be a list")

    baseline = collections.Counter()
    ordered_identities = []
    for index, row in enumerate(rows):
        values = _validate_row(index, row)
        if values in baseline:
            raise PolicyError(f"baseline finding {index} duplicates an identity")
        baseline[values] = row["count"]
        ordered_identities.append(values)
    if ordered_identities != sorted(ordered_identities):
        raise PolicyError("baseline findings must be sorted by identity")
    return baseline


def write_baseline(findings, baseline_path, policy_fingerprint):
    rows = [
        {"rule": rule, "path": path, "context": context, "statement": statement, "count": count}
        for (rule, path, context, statement), count in sorted(findings.items())
    ]
    data = {
        "schema_version": SCHEMA_VERSION,
        "canonical_python": f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}",
        "policy_sha256": policy_fingerprint,
        "findings": rows,
    }
    Path(baseline_path).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        f"Wrote {len(rows)} baseline identities "
        f"({sum(findings.values())} unannotated escape-hatch occurrence(s)) to {baseline_path}"
    )


def compare_baseline(findings, baseline):
    current = collections.Counter(findings)
    recorded = collections.Counter(baseline)
    return current - recorded, recorded - current


def _print_rules():
    for rule, description in sorted(RULES.items()):
        print(f"  # {rule + ': <reason>':<28} {description}")


def report_malformed(malformed):
    print("escape-hatch policy: unusable justification comment(s):\n")
    for entry in sorted(malformed, key=lambda item: (item.path, item.line)):
        print(f"  {entry.path}:{entry.line}: {entry.problem}")
        print(f"    comment: {entry.comment}")
    print()
    _print_rules()
    return 1


def report_mismatches(regressions, stale_entries, baseline_path):
    if stale_entries:
        print("escape-hatch baseline is stale -- removed or annotated occurrence(s) must update it:\n")
        for (rule, path, context, statement), count in sorted(stale_entries.items()):
            print(f"  [{rule}] {path}: {statement} ({count} occurrence(s) no longer present)")
            print(f"    scope: {context}")
        print()
    if regressions:
        print("escape-hatch policy: new unjustified occurrence(s) introduced:\n")
        for (rule, path, context, statement), count in sorted(regressions.items()):
            print(f"  [{rule}] {path}: {statement} ({count} new occurrence(s))")
            print(f"    scope: {context}")
        print("\nUse the scoped alternative, or justify the occurrence in place with one of:")
        _print_rules()
        print()
    print(
        "After cleanup, regenerate on canonical Python "
        f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]} with "
        "`python scripts/check_escape_hatches.py --write-baseline` and review "
        f"the {baseline_path} diff."
    )
    return 1


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="*", default=DEFAULT_TARGETS)
    parser.add_argument(
        "--write-baseline", action="store_true", help="Update the baseline after cleanup; new identities are refused."
    )
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH, help="Path to the baseline JSON file.")
    parser.add_argument("--cwd", type=Path, default=REPO_ROOT, help="Repository root the targets are resolved against.")
    arguments = parser.parse_args(argv)
    if not arguments.targets:
        arguments.targets = list(DEFAULT_TARGETS)
    return arguments


def main(argv=None):
    args = parse_args(argv)
    if sys.version_info[:2] != CANONICAL_PYTHON:
        print(
            "Refusing to run the escape-hatch policy gate outside Python "
            f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]} "
            f"(running {sys.version_info[0]}.{sys.version_info[1]}); "
            "expression normalisation differs across interpreter versions, so "
            "findings would not be comparable to the canonical baseline.",
            file=sys.stderr,
        )
        return 2

    policy_fingerprint = compute_policy_fingerprint(args.targets)
    bootstrapping = args.write_baseline and not args.baseline.exists()
    try:
        result = collect_findings(args.cwd, args.targets)
        baseline = collections.Counter() if bootstrapping else load_baseline(args.baseline, policy_fingerprint)
    except PolicyError as exc:
        print(f"escape-hatch policy gate failed: {exc}", file=sys.stderr)
        return 2

    if result.malformed:
        return report_malformed(result.malformed)

    regressions, stale_entries = compare_baseline(result.findings, baseline)
    if args.write_baseline:
        if regressions and not bootstrapping:
            return report_mismatches(regressions, collections.Counter(), args.baseline)
        write_baseline(result.findings, args.baseline, policy_fingerprint)
        return 0

    if regressions or stale_entries:
        return report_mismatches(regressions, stale_entries, args.baseline)

    justified = sum(result.annotated.values())
    summary = ", ".join(f"{rule}={count}" for rule, count in sorted(result.annotated.items()))
    print(
        f"escape hatches: {sum(result.findings.values())} unannotated occurrence(s) match the "
        f"identity baseline; {justified} justified in place{f' ({summary})' if summary else ''}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
