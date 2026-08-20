#!/usr/bin/env python
"""Fail-closed, identity-based ratchet for function-body (local) imports.

ITAMbox keeps imports at module top. A function-body import is justified only
when a module-top import would break, and the repository records that reason
next to the import as ``# inline import: <category>: <reason>`` (see
the private design-docs repository's development/python-import-policy.md).

This gate is deterministic and AST-based. It scans production Python, treats
every annotated function-body import as reviewed, and holds every unannotated
one against a checked-in identity baseline. The baseline records path, the
enclosing scope path, and the normalised import statement. Physical row numbers
are deliberately excluded: inserting an unrelated line above existing debt must
not create a false regression.

A new identity is always a regression. Removed identities make the baseline
stale and require a reviewed cleanup update, so hoisted or annotated debt never
becomes headroom for new debt.

The gate does not judge whether a recorded reason is *true* -- an AST cannot
prove that a cycle is real. It guarantees that every function-body import is
either pre-existing reviewed debt or carries an explicit, categorised
justification a reviewer can check.

The canonical baseline is generated with Python 3.12. The gate refuses to run
on any other interpreter: ``ast.unparse`` normalisation is version-sensitive,
so results from a non-canonical interpreter are not comparable to the baseline.
There are no interpreter- or OS-specific exceptions.
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
BASELINE_PATH = REPO_ROOT / "scripts" / "local_import_baseline.json"
DEFAULT_TARGETS = ["itambox", "scripts"]
SCHEMA_VERSION = 1
CANONICAL_PYTHON = (3, 12)

# The four justifications the import policy recognises. Anything else must be
# hoisted to module top rather than annotated.
POLICY_CATEGORIES = {
    "cycle": "breaks a real circular import",
    "app-registry": "avoids AppRegistryNotReady at import time",
    "optional-dependency": "dependency is absent in a supported environment",
    "heavy-import": "defers an expensive import off a hot import path",
}

# ``# inline import: <category>: <reason>`` (also accepts the plural form used
# when one comment covers a contiguous group of imports).
MARKER_PATTERN = r"\binline\s+imports?\s*:"
ANNOTATION_PATTERN = r"\binline\s+imports?\s*:\s*(?P<category>[a-z][a-z0-9-]*)\s*:\s*(?P<reason>\S.*?)\s*$"
MARKER_RE = re.compile(MARKER_PATTERN, re.IGNORECASE)
ANNOTATION_RE = re.compile(ANNOTATION_PATTERN, re.IGNORECASE)

# Generated, vendored, and test paths only -- never hand-written production
# code. ``tests`` is excluded on purpose: an inline import in a test module is
# a legitimate isolation technique (importing under a patched environment), and
# test modules are not part of the application's import graph.
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
        "tests",
    }
)
EXCLUDED_FILE_NAMES = frozenset({"conftest.py", "tests.py"})
EXCLUDED_FILE_PREFIXES = ("test_",)


class PolicyError(Exception):
    """Raised when the gate cannot produce a trustworthy result."""


# One function-body import whose justification comment is not usable.
MalformedAnnotation = collections.namedtuple("MalformedAnnotation", "path line comment problem")

# Unannotated debt, annotated justifications, and policy errors.
ScanResult = collections.namedtuple("ScanResult", "findings annotated malformed")


def is_excluded(relative_path):
    parts = relative_path.split("/")
    if set(parts[:-1]) & EXCLUDED_DIRECTORY_NAMES:
        return True
    name = parts[-1]
    return name in EXCLUDED_FILE_NAMES or name.startswith(EXCLUDED_FILE_PREFIXES)


def iter_source_files(root, targets):
    for target in targets:
        directory = root / target
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*.py")):
            relative_path = path.relative_to(root).as_posix()
            if is_excluded(relative_path):
                continue
            yield path, relative_path


def _scope_label(node):
    return f"{type(node).__name__}:{node.name}"


class _ImportCollector(ast.NodeVisitor):
    """Collect every import statement that executes inside a function body."""

    def __init__(self):
        self.scope = []
        self.imports = []

    def _visit_scope(self, node):
        self.scope.append(node)
        self.generic_visit(node)
        self.scope.pop()

    visit_ClassDef = _visit_scope
    visit_FunctionDef = _visit_scope
    visit_AsyncFunctionDef = _visit_scope

    def _visit_import(self, node):
        if not any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) for item in self.scope):
            return
        context = "/".join(_scope_label(item) for item in self.scope)
        self.imports.append((node, context))

    visit_Import = _visit_import
    visit_ImportFrom = _visit_import


def _statement_comment(lines, node):
    """Return the comment written on the import statement's own line(s)."""
    for index in range(node.lineno - 1, node.end_lineno):
        line = lines[index]
        if "#" in line:
            return line.split("#", 1)[1].strip()
    return None


def _preceding_comment_block(lines, node):
    """Return the contiguous full-line comment block directly above an import."""
    collected = []
    index = node.lineno - 2
    while index >= 0 and lines[index].strip().startswith("#"):
        collected.append(lines[index].strip().lstrip("#").strip())
        index -= 1
    return " ".join(reversed(collected)) if collected else None


def _classify_comment(comment):
    """Map a comment to (category, problem). Both None means "not a marker"."""
    if comment is None or not MARKER_RE.search(comment):
        return None, None
    match = ANNOTATION_RE.search(comment)
    if match is None:
        return None, (
            "justification must read '# inline import: <category>: <reason>' "
            f"(categories: {', '.join(sorted(POLICY_CATEGORIES))})"
        )
    category = match.group("category").lower()
    if category not in POLICY_CATEGORIES:
        return None, (
            f"unrecognised justification category {category!r} (categories: {', '.join(sorted(POLICY_CATEGORIES))})"
        )
    return category, None


def _resolve_annotations(relative_path, lines, collected):
    """Resolve each import to a category, inheriting within a contiguous group."""
    ordered = sorted(collected, key=lambda item: item[0].lineno)
    ends = {node.end_lineno: (node, context) for node, context in ordered}
    resolved = {}
    problems = []
    for node, context in ordered:
        comment = _statement_comment(lines, node)
        category, problem = _classify_comment(comment)
        if category is None and problem is None:
            block = _preceding_comment_block(lines, node)
            category, problem = _classify_comment(block)
            comment = block if problem or category else comment
        if category is None and problem is None:
            previous, previous_context = ends.get(node.lineno - 1, (None, None))
            if previous is not None and previous_context == context:
                category = resolved.get(id(previous))
        if problem is not None:
            problems.append(MalformedAnnotation(relative_path, node.lineno, comment, problem))
        resolved[id(node)] = category
    return resolved, problems


def collect_local_imports(root, targets):
    """Scan production sources for function-body imports."""
    findings = collections.Counter()
    annotated = collections.Counter()
    malformed = []
    for path, relative_path in iter_source_files(root, targets):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise PolicyError(f"cannot read {relative_path}: {exc}") from exc
        try:
            tree = ast.parse(source, filename=relative_path)
        except SyntaxError as exc:
            raise PolicyError(f"cannot parse {relative_path}: {exc}") from exc
        collector = _ImportCollector()
        collector.visit(tree)
        if not collector.imports:
            continue
        lines = source.splitlines()
        resolved, problems = _resolve_annotations(relative_path, lines, collector.imports)
        malformed.extend(problems)
        for node, context in collector.imports:
            category = resolved.get(id(node))
            if category is not None:
                annotated[category] += 1
                continue
            findings[(relative_path, context, ast.unparse(node))] += 1
    return ScanResult(findings, annotated, malformed)


def compute_policy_fingerprint(targets):
    """Bind a baseline to the policy that produced it."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "canonical_python": f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}",
        "categories": sorted(POLICY_CATEGORIES),
        "marker_pattern": MARKER_PATTERN,
        "annotation_pattern": ANNOTATION_PATTERN,
        "excluded_directory_names": sorted(EXCLUDED_DIRECTORY_NAMES),
        "excluded_file_names": sorted(EXCLUDED_FILE_NAMES),
        "excluded_file_prefixes": sorted(EXCLUDED_FILE_PREFIXES),
        "targets": list(targets),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _validate_baseline_header(raw, expected_policy_fingerprint):
    required_top_level = {
        "schema_version",
        "canonical_python",
        "policy_sha256",
        "findings",
    }
    if not isinstance(raw, dict) or set(raw) != required_top_level:
        raise PolicyError("baseline has invalid top-level fields")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise PolicyError(f"expected local-import baseline schema {SCHEMA_VERSION}")
    if raw["canonical_python"] != f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}":
        raise PolicyError(f"baseline canonical_python must be '{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}'")
    if raw["policy_sha256"] != expected_policy_fingerprint:
        raise PolicyError("baseline policy_sha256 does not match the effective import policy")


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
    required = {"path", "context", "statement", "count"}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != required:
            raise PolicyError(f"baseline finding {index} has invalid fields")
        count = row["count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise PolicyError(f"baseline finding {index} has invalid count")
        values = (row["path"], row["context"], row["statement"])
        if not all(isinstance(value, str) for value in values):
            raise PolicyError(f"baseline finding {index} has non-string identity")
        if values in baseline:
            raise PolicyError(f"baseline finding {index} duplicates an identity")
        baseline[values] = count
        ordered_identities.append(values)
    if ordered_identities != sorted(ordered_identities):
        raise PolicyError("baseline findings must be sorted by identity")
    return baseline


def write_baseline(findings, baseline_path, policy_fingerprint):
    rows = [
        {
            "path": path,
            "context": context,
            "statement": statement,
            "count": count,
        }
        for (path, context, statement), count in sorted(findings.items())
    ]
    data = {
        "schema_version": SCHEMA_VERSION,
        "canonical_python": f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}",
        "policy_sha256": policy_fingerprint,
        "findings": rows,
    }
    Path(baseline_path).write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(
        f"Wrote {len(rows)} baseline identities "
        f"({sum(findings.values())} unannotated function-body import(s)) to {baseline_path}"
    )


def compare_baseline(findings, baseline):
    current = collections.Counter(findings)
    recorded = collections.Counter(baseline)
    return current - recorded, recorded - current


def report_malformed(malformed):
    print("local-import policy: unusable justification comment(s):\n")
    for entry in sorted(malformed, key=lambda item: (item.path, item.line)):
        print(f"  {entry.path}:{entry.line}: {entry.problem}")
        print(f"    comment: {entry.comment}")
    print()
    for category, description in sorted(POLICY_CATEGORIES.items()):
        print(f"  {category:<21} {description}")
    print("\nHoist the import to module top when none of these applies.")
    return 1


def report_mismatches(regressions, stale_entries, baseline_path):
    if stale_entries:
        print("local-import baseline is stale -- hoisted or annotated import(s) must update it:\n")
        for (path, context, statement), count in sorted(stale_entries.items()):
            print(f"  {path}: {statement} ({count} occurrence(s) no longer present)")
            print(f"    scope: {context}")
        print()
    if regressions:
        print("local-import policy: new function-body import(s) introduced:\n")
        for (path, context, statement), count in sorted(regressions.items()):
            print(f"  {path}: {statement} ({count} new occurrence(s))")
            print(f"    scope: {context}")
        print()
        print("Hoist each import to module top, or justify it in place with")
        print("  # inline import: <category>: <reason>")
        for category, description in sorted(POLICY_CATEGORIES.items()):
            print(f"  {category:<21} {description}")
        print()
    print(
        "After cleanup, regenerate on canonical Python "
        f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]} with "
        "`python scripts/check_local_imports.py --write-baseline` and review "
        f"the {baseline_path} diff."
    )
    return 1


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("targets", nargs="*", default=DEFAULT_TARGETS)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Update the baseline after cleanup; new identities are refused.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=BASELINE_PATH,
        help="Path to the baseline JSON file.",
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        default=REPO_ROOT,
        help="Repository root the targets are resolved against.",
    )
    arguments = parser.parse_args(argv)
    if not arguments.targets:
        arguments.targets = list(DEFAULT_TARGETS)
    return arguments


def main(argv=None):
    args = parse_args(argv)
    if sys.version_info[:2] != CANONICAL_PYTHON:
        print(
            "Refusing to run the local-import policy gate outside Python "
            f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]} "
            f"(running {sys.version_info[0]}.{sys.version_info[1]}); "
            "import normalisation differs across interpreter versions, so "
            "findings would not be comparable to the canonical baseline.",
            file=sys.stderr,
        )
        return 2

    policy_fingerprint = compute_policy_fingerprint(args.targets)
    bootstrapping = args.write_baseline and not args.baseline.exists()
    try:
        result = collect_local_imports(args.cwd, args.targets)
        baseline = collections.Counter() if bootstrapping else load_baseline(args.baseline, policy_fingerprint)
    except PolicyError as exc:
        print(f"local-import policy gate failed: {exc}", file=sys.stderr)
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
    summary = ", ".join(f"{category}={count}" for category, count in sorted(result.annotated.items()))
    print(
        f"local imports: {sum(result.findings.values())} unannotated function-body "
        f"import(s) match the identity baseline; {justified} justified in place"
        f"{f' ({summary})' if summary else ''}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
