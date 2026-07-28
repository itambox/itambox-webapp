#!/usr/bin/env python
"""Fail-closed, identity-based ratchet for broad and pass-only exception handlers.

ITAMbox catches what it can name. A handler that catches ``Exception`` -- or
that catches anything at all and then does nothing -- hides a failure from the
caller, and in a security path it converts a refusal into an approval. This gate
holds every such handler against a checked-in identity baseline, and refuses
them outright in the paths where silence is never acceptable.

The gate is deterministic and AST-based; it never imports application modules.

Three properties matter, and each is enforced separately:

* **New debt is a regression.** The baseline records path, enclosing scope,
  normalised exception type, and the *classification* of the body. Physical row
  numbers are deliberately excluded, so inserting a line above existing debt is
  not a finding -- but a handler that quietly stops logging is, because the
  classification is part of its identity.
* **Security silence is not baselinable.** Crypto, authorization, tenant
  resolution, and configuration load must propagate. Authentication and
  lexically transactional code may isolate only a reviewed external/cache or
  per-item boundary, and only when the handler logs and carries the category
  permitted for that domain. ``pass`` and silent fallbacks remain impossible to
  annotate into compliance. This check runs independently of the ratchet.
* **Removed debt must be recorded.** Stale entries make the baseline stale and
  require a reviewed update, so paid-down debt never becomes headroom for new
  debt. Whole-file refactors are paired and reported as ``moved:`` so a reviewer
  can tell a relocation from genuinely new debt -- a diagnostic only; a move
  still requires the reviewed baseline update.

Bare handlers (``except:``) belong to Flake8 E722, which is already selected in
``setup.cfg`` with an empty baseline. This gate reports them only to cross-check
that the division of labour still holds; it is not the ratchet for them.

The canonical baseline is generated with Python 3.12. The gate refuses to run on
any other interpreter: ``ast.unparse`` normalisation is version-sensitive, so
results from a non-canonical interpreter are not comparable to the baseline.
"""

import argparse
import ast
import collections
import json
import sys
from pathlib import Path

try:
    from scripts.exception_policy import (
        ANNOTATION_RE,
        ATOMIC_NAME,
        CANONICAL_PYTHON,
        DEFAULT_TARGETS,
        EXCLUDED_DIRECTORY_NAMES,
        EXCLUDED_FILE_NAMES,
        EXCLUDED_FILE_PREFIXES,
        MARKER_RE,
        POLICY_CATEGORIES,
        PROPAGATING_CLASSIFICATIONS,
        SCHEMA_VERSION,
        SUPPRESS_NAME,
        HandlerIdentity,
        IdentityError,
        PolicyError,
        classify_handler,
        compute_policy_fingerprint,
        is_in_gate_scope,
        is_prohibited_violation,
        normalise_handler_type,
        normalise_suppress_type,
        resolve_layer,
        resolve_prohibited_domains,
        structural_body_sha256,
    )
except ModuleNotFoundError:  # direct execution puts scripts/ on sys.path, not the repository root
    from exception_policy import (
        ANNOTATION_RE,
        ATOMIC_NAME,
        CANONICAL_PYTHON,
        DEFAULT_TARGETS,
        EXCLUDED_DIRECTORY_NAMES,
        EXCLUDED_FILE_NAMES,
        EXCLUDED_FILE_PREFIXES,
        MARKER_RE,
        POLICY_CATEGORIES,
        PROPAGATING_CLASSIFICATIONS,
        SCHEMA_VERSION,
        SUPPRESS_NAME,
        HandlerIdentity,
        IdentityError,
        PolicyError,
        classify_handler,
        compute_policy_fingerprint,
        is_in_gate_scope,
        is_prohibited_violation,
        normalise_handler_type,
        normalise_suppress_type,
        resolve_layer,
        resolve_prohibited_domains,
        structural_body_sha256,
    )

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "scripts" / "exception_baseline.json"
BASELINE_FIELDS = ("path", "scope", "handler_type", "classification", "body_sha256", "count")

# One handler whose justification comment cannot be used.
MalformedAnnotation = collections.namedtuple("MalformedAnnotation", "path line comment problem")

# One handler in a scope where only PROHIBITED_ONLY_CLASSIFICATION is admissible.
ProhibitedViolation = collections.namedtuple(
    "ProhibitedViolation", "path line scope handler_type classification domains layer"
)

# One bare ``except:`` -- Flake8 E722's job; reported here as a cross-check.
BareHandler = collections.namedtuple("BareHandler", "path line scope")

# Unannotated debt, annotated justifications, policy errors, hard failures.
ScanResult = collections.namedtuple("ScanResult", "findings annotated malformed prohibited bare")

# One handler as the collector sees it, before annotation and policy resolution.
_Collected = collections.namedtuple(
    "_Collected", "lineno header_end scope_label scope_names handler_type classification body_sha256 transactional"
)


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


def _qualified_name(node, aliases):
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _is_atomic_with(node, aliases):
    """``with transaction.atomic():`` or ``with atomic():``, in any spelling."""
    return any(
        isinstance(item.context_expr, ast.Call)
        and _qualified_name(item.context_expr.func, aliases).rsplit(".", 1)[-1] == ATOMIC_NAME
        for item in node.items
    )


def _is_atomic_decorator(node, aliases):
    """Recognise ``@atomic`` and ``@transaction.atomic`` with optional call."""
    target = node.func if isinstance(node, ast.Call) else node
    return _qualified_name(target, aliases).rsplit(".", 1)[-1] == ATOMIC_NAME


def _suppress_calls(node, aliases):
    return [
        item.context_expr
        for item in node.items
        if isinstance(item.context_expr, ast.Call)
        and _qualified_name(item.context_expr.func, aliases) in {SUPPRESS_NAME, "contextlib.suppress"}
    ]


def _header_end(node):
    """Last line of a statement's header, so a trailing comment can be found.

    Body statements are excluded: a comment inside the handler body is not a
    justification for the handler.
    """
    if node.body:
        return max(node.lineno, node.body[0].lineno - 1)
    return node.end_lineno


class _HandlerCollector(ast.NodeVisitor):
    """Collect every handler the policy tracks, with its enclosing context."""

    def __init__(self):
        self.scope = []
        self.alias_scopes = [{}]
        self.atomic_depth = 0
        self.collected = []

    @property
    def aliases(self):
        return self.alias_scopes[-1]

    def visit_Import(self, node):
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".", 1)[0]
            self.aliases[local_name] = alias.name if alias.asname else local_name
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.module is not None:
            for alias in node.names:
                if alias.name == "*":
                    continue
                self.aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
        self.generic_visit(node)

    def _visit_scope(self, node):
        atomic = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            _is_atomic_decorator(decorator, self.aliases) for decorator in node.decorator_list
        )
        self.scope.append(node)
        self.alias_scopes.append(dict(self.aliases))
        if atomic:
            self.atomic_depth += 1
        self.generic_visit(node)
        if atomic:
            self.atomic_depth -= 1
        self.alias_scopes.pop()
        self.scope.pop()

    visit_ClassDef = _visit_scope
    visit_FunctionDef = _visit_scope
    visit_AsyncFunctionDef = _visit_scope

    def _context(self):
        return (
            "/".join(_scope_label(item) for item in self.scope),
            tuple(item.name for item in self.scope),
        )

    def _visit_with(self, node):
        scope_label, scope_names = self._context()
        for call in _suppress_calls(node, self.aliases):
            # ``suppress`` is a pass-only handler wearing a context manager, and
            # would otherwise be a one-line bypass of this entire gate.
            self.collected.append(
                _Collected(
                    lineno=node.lineno,
                    header_end=_header_end(node),
                    scope_label=scope_label,
                    scope_names=scope_names,
                    handler_type=normalise_suppress_type(call),
                    classification="pass-only",
                    body_sha256=structural_body_sha256(node.body),
                    transactional=self.atomic_depth > 0,
                )
            )
        atomic = _is_atomic_with(node, self.aliases)
        if atomic:
            self.atomic_depth += 1
        self.generic_visit(node)
        if atomic:
            self.atomic_depth -= 1

    visit_With = _visit_with
    visit_AsyncWith = _visit_with

    def visit_ExceptHandler(self, node):
        scope_label, scope_names = self._context()
        self.collected.append(
            _Collected(
                lineno=node.lineno,
                header_end=_header_end(node),
                scope_label=scope_label,
                scope_names=scope_names,
                handler_type=normalise_handler_type(node.type),
                classification=classify_handler(node),
                body_sha256=structural_body_sha256(node.body),
                transactional=self.atomic_depth > 0,
            )
        )
        self.generic_visit(node)


def _statement_comment(lines, entry):
    """Return the comment written on the handler's own header line(s)."""
    for index in range(entry.lineno - 1, entry.header_end):
        line = lines[index]
        if "#" in line:
            return line.split("#", 1)[1].strip()
    return None


def _preceding_comment_block(lines, entry):
    """Return the contiguous full-line comment block directly above a handler."""
    collected = []
    index = entry.lineno - 2
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
            "justification must read '# broad except: <category>: <reason>' "
            f"(categories: {', '.join(sorted(POLICY_CATEGORIES))})"
        )
    category = match.group("category").lower()
    if category not in POLICY_CATEGORIES:
        return None, (
            f"unrecognised justification category {category!r} (categories: {', '.join(sorted(POLICY_CATEGORIES))})"
        )
    return category, None


def _resolve_annotation(lines, entry):
    """Resolve one handler's justification.

    Unlike the inline-import policy there is no group inheritance: handlers are
    multi-line blocks, and one comment silently covering three different
    handlers would be a footgun rather than a convenience.
    """
    comment = _statement_comment(lines, entry)
    category, problem = _classify_comment(comment)
    if category is None and problem is None:
        block = _preceding_comment_block(lines, entry)
        category, problem = _classify_comment(block)
        if category or problem:
            comment = block
    return category, problem, comment


def _read_collected_handlers(path, relative_path):
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PolicyError(f"cannot read {relative_path}: {exc}") from exc
    try:
        tree = ast.parse(source, filename=relative_path)
    except SyntaxError as exc:
        raise PolicyError(f"cannot parse {relative_path}: {exc}") from exc
    collector = _HandlerCollector()
    collector.visit(tree)
    return source.splitlines(), sorted(collector.collected, key=lambda item: (item.lineno, item.handler_type))


def _evaluate_handler(relative_path, lines, entry):
    bare = BareHandler(relative_path, entry.lineno, entry.scope_label) if entry.handler_type == "<bare>" else None
    if not is_in_gate_scope(entry.handler_type, entry.classification):
        return bare, None, None, None, None

    category, problem, comment = _resolve_annotation(lines, entry)
    domains = resolve_prohibited_domains(relative_path, entry.scope_names, entry.transactional)
    prohibited = None
    if is_prohibited_violation(domains, entry.classification, category):
        prohibited = ProhibitedViolation(
            path=relative_path,
            line=entry.lineno,
            scope=entry.scope_label,
            handler_type=entry.handler_type,
            classification=entry.classification,
            domains=domains,
            layer=resolve_layer(relative_path),
        )
    if problem is not None:
        malformed = MalformedAnnotation(relative_path, entry.lineno, comment, problem)
        return bare, None, None, malformed, prohibited
    if category is not None:
        return bare, None, category, None, prohibited
    try:
        identity = HandlerIdentity(
            relative_path,
            entry.scope_label,
            entry.handler_type,
            entry.classification,
            entry.body_sha256,
        )
    except IdentityError as exc:
        raise PolicyError(f"{relative_path}:{entry.lineno}: {exc}") from exc
    return bare, identity, None, None, prohibited


def collect_handlers(root, targets):
    """Scan production sources for the handlers this policy tracks."""
    findings = collections.Counter()
    annotated = collections.Counter()
    malformed = []
    prohibited = []
    bare = []

    for path, relative_path in iter_source_files(root, targets):
        lines, entries = _read_collected_handlers(path, relative_path)
        for entry in entries:
            bare_entry, identity, category, problem, prohibited_entry = _evaluate_handler(relative_path, lines, entry)
            if bare_entry is not None:
                bare.append(bare_entry)
            if identity is not None:
                findings[identity] += 1
            if category is not None:
                annotated[category] += 1
            if problem is not None:
                malformed.append(problem)
            if prohibited_entry is not None:
                prohibited.append(prohibited_entry)

    return ScanResult(findings, annotated, malformed, prohibited, bare)


# --------------------------------------------------------------------------
# Baseline
# --------------------------------------------------------------------------


def _validate_baseline_header(raw, expected_policy_fingerprint, allow_policy_drift=False):
    required_top_level = {"schema_version", "canonical_python", "policy_sha256", "findings"}
    if not isinstance(raw, dict) or set(raw) != required_top_level:
        raise PolicyError("baseline has invalid top-level fields")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise PolicyError(f"expected exception-policy baseline schema {SCHEMA_VERSION}")
    if raw["canonical_python"] != f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}":
        raise PolicyError(f"baseline canonical_python must be '{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}'")
    if raw["policy_sha256"] != expected_policy_fingerprint:
        if not allow_policy_drift:
            raise PolicyError("baseline policy_sha256 does not match the effective exception policy")
        return True
    return False


def _parse_baseline_rows(rows):
    if not isinstance(rows, list):
        raise PolicyError("baseline findings must be a list")
    baseline = collections.Counter()
    ordered_identities = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != set(BASELINE_FIELDS):
            raise PolicyError(f"baseline finding {index} has invalid fields")
        count = row["count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise PolicyError(f"baseline finding {index} has invalid count")
        try:
            identity = HandlerIdentity(
                row["path"],
                row["scope"],
                row["handler_type"],
                row["classification"],
                row["body_sha256"],
            )
        except IdentityError as exc:
            raise PolicyError(f"baseline finding {index} has an invalid identity: {exc}") from exc
        if identity in baseline:
            raise PolicyError(f"baseline finding {index} duplicates an identity")
        baseline[identity] = count
        ordered_identities.append(identity)
    if ordered_identities != sorted(ordered_identities):
        raise PolicyError("baseline findings must be sorted by identity")
    return baseline


def load_baseline(baseline_path, expected_policy_fingerprint, allow_policy_drift=False):
    """Read the recorded identities.

    ``allow_policy_drift`` is used only when regenerating. Changing the policy
    must not become an amnesty for debt added in the same commit: the recorded
    *rows* stay valid when only the fingerprint moved, so the ratchet still runs
    against them and still refuses net-new identities. Everything else about the
    document is validated exactly as strictly as in a read-only run.
    """
    try:
        raw = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read baseline {baseline_path}: {exc}") from exc
    drifted = _validate_baseline_header(raw, expected_policy_fingerprint, allow_policy_drift)
    if drifted:
        print(
            "exception policy: baseline policy fingerprint changed; regenerating against the "
            "recorded identities so newly added debt is still refused."
        )
    return _parse_baseline_rows(raw["findings"])


def _parse_v1_baseline_rows(rows):
    fields = {"path", "scope", "handler_type", "classification", "count"}
    baseline = collections.Counter()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != fields:
            raise PolicyError(f"schema-v1 baseline finding {index} has invalid fields")
        count = row["count"]
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise PolicyError(f"schema-v1 baseline finding {index} has invalid count")
        identity = (row["path"], row["scope"], row["handler_type"], row["classification"])
        try:
            HandlerIdentity(*identity, "0" * 64)
        except IdentityError as exc:
            raise PolicyError(f"schema-v1 baseline finding {index} has an invalid identity: {exc}") from exc
        if identity in baseline:
            raise PolicyError(f"schema-v1 baseline finding {index} duplicates an identity")
        baseline[identity] = count
    return baseline


def load_v1_baseline_for_migration(baseline_path):
    """Load schema v1's coarse identities for a guarded one-way v2 migration."""
    try:
        raw = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read baseline {baseline_path}: {exc}") from exc
    required_top_level = {"schema_version", "canonical_python", "policy_sha256", "findings"}
    if not isinstance(raw, dict) or set(raw) != required_top_level or raw["schema_version"] != 1:
        return None
    if raw["canonical_python"] != f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}":
        raise PolicyError(f"baseline canonical_python must be '{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}'")
    if not isinstance(raw["findings"], list):
        raise PolicyError("baseline findings must be a list")
    return _parse_v1_baseline_rows(raw["findings"])


def write_baseline(findings, baseline_path, policy_fingerprint):
    rows = []
    for identity, count in sorted(findings.items()):
        row = dict(HandlerIdentity(*identity).as_dict())
        row["count"] = count
        rows.append(row)
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
        f"({sum(findings.values())} unannotated broad/pass-only handler(s)) to {baseline_path}"
    )


def compare_baseline(findings, baseline):
    current = collections.Counter(findings)
    recorded = collections.Counter(baseline)
    return current - recorded, recorded - current


def pair_moved_identities(regressions, stale):
    """Pair relocated handlers so a refactor does not read as new debt.

    Diagnostic only. A move still fails the run and still requires a reviewed
    baseline update -- auto-accepting one would let a handler be relocated into
    a prohibited scope without review, which is precisely the attack this gate
    exists to stop.
    """
    remaining_regressions = collections.Counter(regressions)
    remaining_stale = collections.Counter(stale)
    moves = []
    for regression in sorted(remaining_regressions):
        for previous in sorted(remaining_stale):
            if remaining_regressions[regression] <= 0:
                break
            if remaining_stale[previous] <= 0:
                continue
            if previous[2:] != regression[2:]:
                continue
            if (previous[0], previous[1]) == (regression[0], regression[1]):
                continue
            paired = min(remaining_regressions[regression], remaining_stale[previous])
            moves.append((previous, regression, paired))
            remaining_regressions[regression] -= paired
            remaining_stale[previous] -= paired
    return moves, +remaining_regressions, +remaining_stale


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------


def _print_categories():
    for category, description in sorted(POLICY_CATEGORIES.items()):
        print(f"  {category:<22} {description}")


def report_malformed(malformed):
    print("exception policy: unusable justification comment(s):\n")
    for entry in sorted(malformed, key=lambda item: (item.path, item.line)):
        print(f"  {entry.path}:{entry.line}: {entry.problem}")
        print(f"    comment: {entry.comment}")
    print()
    _print_categories()
    print("\nNarrow the handler to the exceptions the call can raise when none of these applies.")
    return 1


def report_prohibited(violations):
    print("exception policy: broad or pass-only handler(s) in a prohibited scope:\n")
    for entry in sorted(violations, key=lambda item: (item.path, item.line)):
        print(f"  {entry.path}:{entry.line}: except {entry.handler_type} -> {entry.classification}")
        print(f"    prohibited: {', '.join(entry.domains)} (layer: {entry.layer})")
        if entry.scope:
            print(f"    scope: {entry.scope}")
    print()
    print(
        "These scopes admit only handlers that still tell the caller the operation failed:\n"
        f"  {', '.join(PROPAGATING_CLASSIFICATIONS)}\n"
        "\n"
        "Logging is not among them. A log line is observability, not a return value: the\n"
        "caller carries on as though the call succeeded, which is how a refusal becomes an\n"
        "approval. Re-raise after cleaning up, or raise a documented typed failure.\n"
        "\n"
        "Neither an annotation nor a baseline entry excuses these."
    )
    return 1


def report_bare(bare):
    print("exception policy: bare 'except:' handler(s) found:\n")
    for entry in sorted(bare, key=lambda item: (item.path, item.line)):
        print(f"  {entry.path}:{entry.line}" + (f"  ({entry.scope})" if entry.scope else ""))
    print(
        "\nFlake8 E722 is the ratchet for these and its baseline records none, so reaching this\n"
        "gate means the lint policy was weakened. Restore it rather than baselining here."
    )
    return 1


def report_mismatches(moves, regressions, stale_entries, baseline_path):
    if moves:
        print("exception policy: handler(s) appear to have moved:\n")
        for previous, regression, count in moves:
            print(f"  moved: {previous[0]} {previous[1] or '<module>'}")
            print(f"      -> {regression[0]} {regression[1] or '<module>'}")
            print(f"         except {regression[2]} -> {regression[3]} ({count} occurrence(s))")
        print("\nVerify each is a relocation rather than new debt, then update the baseline.\n")
    if stale_entries:
        print("exception baseline is stale -- fixed or annotated handler(s) must update it:\n")
        for identity, count in sorted(stale_entries.items()):
            print(f"  {identity[0]}: except {identity[2]} -> {identity[3]} ({count} occurrence(s) no longer present)")
            print(f"    scope: {identity[1] or '<module>'}")
        print()
    if regressions:
        print("exception policy: new broad or pass-only handler(s) introduced:\n")
        for identity, count in sorted(regressions.items()):
            print(f"  {identity[0]}: except {identity[2]} -> {identity[3]} ({count} new occurrence(s))")
            print(f"    scope: {identity[1] or '<module>'}")
        print()
        print("Narrow the handler to the exceptions the call can raise, or justify it in place with")
        print("  # broad except: <category>: <reason>")
        _print_categories()
        print()
    print(
        "After cleanup, regenerate on canonical Python "
        f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]} with "
        "`python scripts/check_exception_policy.py --write-baseline` and review "
        f"the {baseline_path} diff."
    )
    return 1


def report_summary(result):
    layers = collections.Counter()
    for identity, count in result.findings.items():
        layers[resolve_layer(identity[0])] += count
    justified = sum(result.annotated.values())
    categories = ", ".join(f"{name}={count}" for name, count in sorted(result.annotated.items()))
    print(
        f"exception policy: {sum(result.findings.values())} unannotated broad/pass-only handler(s) "
        f"match the identity baseline; {justified} justified in place"
        f"{f' ({categories})' if categories else ''}."
    )
    if layers:
        breakdown = ", ".join(f"{layer}={count}" for layer, count in sorted(layers.items()))
        print(f"  by layer: {breakdown}")
    return 0


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("targets", nargs="*", default=list(DEFAULT_TARGETS))
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


def _is_canonical_python():
    if sys.version_info[:2] != CANONICAL_PYTHON:
        print(
            "Refusing to run the exception policy gate outside Python "
            f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]} "
            f"(running {sys.version_info[0]}.{sys.version_info[1]}); "
            "handler normalisation differs across interpreter versions, so "
            "findings would not be comparable to the canonical baseline.",
            file=sys.stderr,
        )
        return False
    return True


def _comparison_baseline(args, result, policy_fingerprint):
    bootstrapping = args.write_baseline and not args.baseline.exists()
    migrating_v1 = None
    if args.write_baseline and not bootstrapping:
        migrating_v1 = load_v1_baseline_for_migration(args.baseline)
    if bootstrapping:
        return collections.Counter(), result.findings, True
    if migrating_v1 is not None:
        comparable_findings = collections.Counter()
        for identity, count in result.findings.items():
            comparable_findings[identity[:4]] += count
        return migrating_v1, comparable_findings, False
    baseline = load_baseline(args.baseline, policy_fingerprint, allow_policy_drift=args.write_baseline)
    return baseline, result.findings, False


def _run_gate(args):
    policy_fingerprint = compute_policy_fingerprint(args.targets)
    result = collect_handlers(args.cwd, args.targets)
    baseline, comparable_findings, bootstrapping = _comparison_baseline(args, result, policy_fingerprint)

    # An unusable justification is a policy error before anything else: it must
    # never be written into a baseline as though it were reviewed debt.
    if result.malformed:
        return report_malformed(result.malformed)

    regressions, stale_entries = compare_baseline(comparable_findings, baseline)
    moves, regressions, stale_entries = pair_moved_identities(regressions, stale_entries)

    exit_code = 0
    if args.write_baseline:
        if regressions and not bootstrapping:
            return report_mismatches(moves, regressions, collections.Counter(), args.baseline)
        write_baseline(result.findings, args.baseline, policy_fingerprint)
    elif moves or regressions or stale_entries:
        exit_code = report_mismatches(moves, regressions, stale_entries, args.baseline)

    # Independent of the ratchet, and of whether a baseline was just written:
    # a bootstrap that records prohibited debt still fails, exactly like the
    # OpenAPI gate's first bootstrap run.
    if result.bare:
        exit_code = report_bare(result.bare)
    if result.prohibited:
        exit_code = report_prohibited(result.prohibited)

    if exit_code == 0:
        return report_summary(result)
    return exit_code


def main(argv=None):
    args = parse_args(argv)
    if not _is_canonical_python():
        return 2
    try:
        return _run_gate(args)
    except PolicyError as exc:
        print(f"exception policy gate failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
