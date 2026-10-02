#!/usr/bin/env python
"""Fail-closed, identity-based ratchet for the escape hatches that bypass invariants.

The architecture and exception gates are satisfied partly through indirection:
an unscoped manager read, a single-implementation provider slot, a test that
asserts on source text, or a broad ``except`` each routes around an invariant
without any gate noticing. This gate holds three of those patterns against a
checked-in identity baseline, and a *new* occurrence needs an in-place,
categorised annotation or it fails:

``unscoped``
    ``_base_manager`` / ``all_objects`` in views, forms, serializers and
    services (``# unscoped: <category>: <reason>``).
``provider-slot``
    A ``SingleProviderSlot`` instantiation in production code
    (``# provider-slot: <category>: <reason>``).
``source-text``
    ``inspect.getsource`` / ``ast.parse`` inside test modules
    (``# source-text: <category>: <reason>``). ``scripts/tests`` is out of scope:
    the repository gates are themselves AST tools.

The fourth pattern, broad ``except`` handlers, is owned by
``scripts/check_exception_policy.py`` (``except Exception`` / ``BaseException``
are ratcheted there against ``scripts/exception_baseline.json``). It is not
duplicated here; ``scripts/tests/test_check_escape_hatches.py`` pins that the
division of labour still holds.

The gate is deterministic and AST-based and never imports application modules.
An annotated occurrence is reviewed; an unannotated one must match the
baseline, which records rule, path, enclosing scope and normalised expression --
never a row number, so an unrelated edit above existing debt is not a finding.
A new identity is always a regression. A removed identity makes the baseline
stale and requires a reviewed update, so paid-down debt never becomes headroom.

The gate cannot judge whether a recorded reason is *true*; it guarantees that
every occurrence is either pre-existing reviewed debt or carries an explicit,
categorised justification a reviewer can check.

The canonical baseline is generated with Python 3.12. The gate refuses to run
on any other interpreter: ``ast.unparse`` normalisation is version-sensitive.
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
DEFAULT_TARGETS = ["itambox"]
SCHEMA_VERSION = 1
CANONICAL_PYTHON = (3, 12)

RULE_UNSCOPED = "unscoped"
RULE_PROVIDER_SLOT = "provider-slot"
RULE_SOURCE_TEXT = "source-text"
RULES = (RULE_UNSCOPED, RULE_PROVIDER_SLOT, RULE_SOURCE_TEXT)

# Every rule's accepted justification categories. Anything else is refused.
POLICY_CATEGORIES = {
    RULE_UNSCOPED: {
        "tenant-resolution": "resolves the tenant or membership before a tenant scope exists",
        "recycle-bin": "reads or restores soft-deleted rows by design",
        "cross-tenant": "deliberate platform-level read across tenants",
        "row-lock": "re-reads a row under select_for_update, which the scoped manager cannot lock",
        "system-task": "background or management work that runs with no ambient tenant",
    },
    RULE_PROVIDER_SLOT: {
        "plugin-seam": "a plugin or second app supplies the provider",
        "multi-implementation": "more than one implementation of the contract exists",
    },
    RULE_SOURCE_TEXT: {
        "static-contract": "pins a structural contract with no runtime-observable form",
        "tooling": "tests a source-reading tool itself",
    },
}

MARKER_PATTERN = r"\b(?P<rule>unscoped|provider-slot|source-text)\s*:"
ANNOTATION_PATTERN = (
    r"\b(?P<rule>unscoped|provider-slot|source-text)\s*:\s*"
    r"(?P<category>[a-z][a-z0-9-]*)\s*:\s*(?P<reason>\S.*?)\s*$"
)
MARKER_RE = re.compile(MARKER_PATTERN)
ANNOTATION_RE = re.compile(ANNOTATION_PATTERN)

UNSCOPED_ATTRIBUTES = frozenset({"_base_manager", "all_objects"})
SLOT_NAME = "SingleProviderSlot"
SOURCE_TEXT_CALLS = {
    "inspect": frozenset({"getsource", "getsourcelines", "getsourcefile"}),
    "ast": frozenset({"parse"}),
}

# Layers the ``unscoped`` rule covers: a directory or a module stem that is
# (or starts with) one of these. Models, managers and the kernel own scoping
# and legitimately reach for the unscoped manager.
UNSCOPED_LAYER_NAMES = ("views", "forms", "serializers", "services")

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


class PolicyError(Exception):
    """Raised when the gate cannot produce a trustworthy result."""


MalformedAnnotation = collections.namedtuple("MalformedAnnotation", "path line comment problem")
ScanResult = collections.namedtuple("ScanResult", "findings annotated malformed")


def is_test_module(relative_path):
    parts = relative_path.split("/")
    name = parts[-1]
    return (
        bool(set(parts[:-1]) & TEST_DIRECTORY_NAMES) or name in TEST_FILE_NAMES or name.startswith(TEST_FILE_PREFIXES)
    )


def is_unscoped_layer(relative_path):
    parts = relative_path.split("/")
    stems = [part[:-3] if part.endswith(".py") else part for part in parts]
    return any(
        stem == layer or stem.startswith((layer + "_", "_" + layer)) for stem in stems for layer in UNSCOPED_LAYER_NAMES
    )


def iter_source_files(root, targets):
    for target in targets:
        directory = root / target
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            relative_path = path.relative_to(root).as_posix()
            if set(relative_path.split("/")[:-1]) & EXCLUDED_DIRECTORY_NAMES:
                continue
            # scripts/tests holds the repository gates, which are AST tools.
            if relative_path.startswith("scripts/tests/"):
                continue
            yield path, relative_path


def _scope_label(node):
    return f"{type(node).__name__}:{node.name}"


class _Collector(ast.NodeVisitor):
    """Collect candidate nodes for every rule that applies to one module."""

    def __init__(self, rules):
        self.rules = rules
        self.scope = []
        self.statements = []
        self.hits = []  # (rule, node, anchor statement, scope context)
        self.module_aliases = {}  # local name -> module (inspect / ast)
        self.function_aliases = {}  # local name -> (module, attribute)
        self.slot_aliases = {SLOT_NAME}

    def _context(self):
        return "/".join(_scope_label(item) for item in self.scope) or "<module>"

    def visit(self, node):
        # Track the innermost enclosing statement as the annotation anchor.
        is_statement = isinstance(node, ast.stmt)
        if is_statement:
            self.statements.append(node)
        scoped = isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        if scoped:
            self.scope.append(node)
        super().visit(node)
        if scoped:
            self.scope.pop()
        if is_statement:
            self.statements.pop()

    def visit_Import(self, node):
        for alias in node.names:
            if alias.name in SOURCE_TEXT_CALLS:
                self.module_aliases[alias.asname or alias.name] = alias.name
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module in SOURCE_TEXT_CALLS:
            for alias in node.names:
                if alias.name in SOURCE_TEXT_CALLS[node.module]:
                    self.function_aliases[alias.asname or alias.name] = (node.module, alias.name)
        for alias in node.names:
            if alias.name == SLOT_NAME:
                self.slot_aliases.add(alias.asname or alias.name)
        self.generic_visit(node)

    def _record(self, rule, node):
        anchor = self.statements[-1] if self.statements else node
        self.hits.append((rule, node, anchor, self._context()))

    def visit_Attribute(self, node):
        if RULE_UNSCOPED in self.rules and node.attr in UNSCOPED_ATTRIBUTES:
            self._record(RULE_UNSCOPED, node)
        self.generic_visit(node)

    def visit_Call(self, node):
        func = node.func
        if RULE_PROVIDER_SLOT in self.rules:
            target = func.value if isinstance(func, ast.Subscript) else func
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
            if name in self.slot_aliases:
                self._record(RULE_PROVIDER_SLOT, node)
        if RULE_SOURCE_TEXT in self.rules and self._is_source_text_call(func):
            self._record(RULE_SOURCE_TEXT, node)
        self.generic_visit(node)

    def _is_source_text_call(self, func):
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            module = self.module_aliases.get(func.value.id)
            return module is not None and func.attr in SOURCE_TEXT_CALLS[module]
        if isinstance(func, ast.Name):
            return func.id in self.function_aliases
        return False


def _own_comment(lines, first, last):
    for index in range(first - 1, last):
        if "#" in lines[index]:
            return lines[index].split("#", 1)[1].strip()
    return None


def _preceding_comment_block(lines, line):
    collected = []
    index = line - 2
    while index >= 0 and lines[index].strip().startswith("#"):
        collected.append(lines[index].strip().lstrip("#").strip())
        index -= 1
    return " ".join(reversed(collected)) if collected else None


def _classify(rule, comment):
    """Return (category, problem); both None means no marker for this rule."""
    if comment is None:
        return None, None
    categories = POLICY_CATEGORIES[rule]
    for match in MARKER_RE.finditer(comment):
        if match.group("rule") != rule:
            continue
        annotation = ANNOTATION_RE.search(comment, match.start())
        if annotation is None or annotation.group("rule") != rule:
            return (
                None,
                f"justification must read '# {rule}: <category>: <reason>' (categories: {', '.join(sorted(categories))})",
            )
        category = annotation.group("category")
        if category not in categories:
            return None, f"unrecognised category {category!r} (categories: {', '.join(sorted(categories))})"
        return category, None
    return None, None


def _resolve(rule, lines, node, anchor):
    """Look for the justification on the node, its statement, or the block above."""
    candidates = [
        _own_comment(lines, node.lineno, node.end_lineno),
        _own_comment(lines, anchor.lineno, anchor.lineno),
        _preceding_comment_block(lines, anchor.lineno),
    ]
    for comment in candidates:
        category, problem = _classify(rule, comment)
        if category is not None or problem is not None:
            return category, problem, comment
    return None, None, None


def rules_for(relative_path):
    rules = set()
    if is_test_module(relative_path):
        rules.add(RULE_SOURCE_TEXT)
    else:
        rules.add(RULE_PROVIDER_SLOT)
        if is_unscoped_layer(relative_path):
            rules.add(RULE_UNSCOPED)
    return rules


def collect_escape_hatches(root, targets):
    """Scan sources for every governed escape hatch."""
    findings = collections.Counter()
    annotated = collections.Counter()
    malformed = []
    for path, relative_path in iter_source_files(root, targets):
        rules = rules_for(relative_path)
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise PolicyError(f"cannot read {relative_path}: {exc}") from exc
        try:
            tree = ast.parse(source, filename=relative_path)
        except SyntaxError as exc:
            raise PolicyError(f"cannot parse {relative_path}: {exc}") from exc
        collector = _Collector(rules)
        collector.visit(tree)
        if not collector.hits:
            continue
        lines = source.splitlines()
        for rule, node, anchor, context in collector.hits:
            category, problem, comment = _resolve(rule, lines, node, anchor)
            if problem is not None:
                malformed.append(MalformedAnnotation(relative_path, node.lineno, comment, problem))
            elif category is not None:
                annotated[(rule, category)] += 1
            else:
                findings[(rule, relative_path, context, ast.unparse(node))] += 1
    return ScanResult(findings, annotated, malformed)


def compute_policy_fingerprint(targets):
    """Bind a baseline to the policy that produced it."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "canonical_python": f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}",
        "rules": list(RULES),
        "categories": {rule: sorted(values) for rule, values in sorted(POLICY_CATEGORIES.items())},
        "marker_pattern": MARKER_PATTERN,
        "annotation_pattern": ANNOTATION_PATTERN,
        "unscoped_attributes": sorted(UNSCOPED_ATTRIBUTES),
        "unscoped_layer_names": list(UNSCOPED_LAYER_NAMES),
        "slot_name": SLOT_NAME,
        "source_text_calls": {module: sorted(values) for module, values in sorted(SOURCE_TEXT_CALLS.items())},
        "excluded_directory_names": sorted(EXCLUDED_DIRECTORY_NAMES),
        "test_directory_names": sorted(TEST_DIRECTORY_NAMES),
        "test_file_names": sorted(TEST_FILE_NAMES),
        "test_file_prefixes": sorted(TEST_FILE_PREFIXES),
        "targets": list(targets),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_header(raw, expected_policy_fingerprint):
    if not isinstance(raw, dict) or set(raw) != {"schema_version", "canonical_python", "policy_sha256", "findings"}:
        raise PolicyError("baseline has invalid top-level fields")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise PolicyError(f"expected escape-hatch baseline schema {SCHEMA_VERSION}")
    if raw["canonical_python"] != f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}":
        raise PolicyError(f"baseline canonical_python must be '{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}'")
    if raw["policy_sha256"] != expected_policy_fingerprint:
        raise PolicyError("baseline policy_sha256 does not match the effective escape-hatch policy")
    if not isinstance(raw["findings"], list):
        raise PolicyError("baseline findings must be a list")


def _row_identity(index, row):
    if not isinstance(row, dict) or set(row) != {"rule", "path", "context", "statement", "count"}:
        raise PolicyError(f"baseline finding {index} has invalid fields")
    count = row["count"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise PolicyError(f"baseline finding {index} has invalid count")
    identity = (row["rule"], row["path"], row["context"], row["statement"])
    if not all(isinstance(value, str) for value in identity):
        raise PolicyError(f"baseline finding {index} has non-string identity")
    if identity[0] not in RULES:
        raise PolicyError(f"baseline finding {index} names unknown rule {identity[0]!r}")
    return identity, count


def load_baseline(baseline_path, expected_policy_fingerprint):
    try:
        raw = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read baseline {baseline_path}: {exc}") from exc
    _validate_header(raw, expected_policy_fingerprint)

    baseline = collections.Counter()
    ordered = []
    for index, row in enumerate(raw["findings"]):
        identity, count = _row_identity(index, row)
        if identity in baseline:
            raise PolicyError(f"baseline finding {index} duplicates an identity")
        baseline[identity] = count
        ordered.append(identity)
    if ordered != sorted(ordered):
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
        f"Wrote {len(rows)} baseline identities ({sum(findings.values())} unannotated occurrence(s)) to {baseline_path}"
    )


def compare_baseline(findings, baseline):
    current = collections.Counter(findings)
    recorded = collections.Counter(baseline)
    return current - recorded, recorded - current


def _print_categories(rules):
    for rule in sorted(rules):
        print(f"  # {rule}: <category>: <reason>")
        for category, description in sorted(POLICY_CATEGORIES[rule].items()):
            print(f"      {category:<21} {description}")


def report_malformed(malformed):
    print("escape-hatch policy: unusable justification comment(s):\n")
    for entry in sorted(malformed, key=lambda item: (item.path, item.line)):
        print(f"  {entry.path}:{entry.line}: {entry.problem}")
        print(f"    comment: {entry.comment}")
    print()
    _print_categories(RULES)
    return 1


def report_mismatches(regressions, stale_entries, baseline_path):
    if stale_entries:
        print("escape-hatch baseline is stale -- removed or annotated occurrence(s) must update it:\n")
        for (rule, path, context, statement), count in sorted(stale_entries.items()):
            print(f"  [{rule}] {path}: {statement} ({count} occurrence(s) no longer present)")
            print(f"    scope: {context}")
        print()
    if regressions:
        print("escape-hatch policy: new occurrence(s) introduced:\n")
        for (rule, path, context, statement), count in sorted(regressions.items()):
            print(f"  [{rule}] {path}: {statement} ({count} new occurrence(s))")
            print(f"    scope: {context}")
        print()
        print("Remove each one, or justify it in place with one of:")
        _print_categories({identity[0] for identity in regressions})
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
        result = collect_escape_hatches(args.cwd, args.targets)
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

    per_rule = collections.Counter()
    for identity, count in result.findings.items():
        per_rule[identity[0]] += count
    justified = sum(result.annotated.values())
    summary = ", ".join(f"{rule}={per_rule[rule]}" for rule in RULES)
    print(f"escape hatches: baselined {summary}; {justified} justified in place.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
