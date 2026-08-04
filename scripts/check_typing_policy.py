#!/usr/bin/env python
"""Blocking gate: every module admitted to static typing still checks clean.

ITAMbox types its backend gradually (issue #93). The set of modules that must
type-check is an explicit, append-only allowlist in
``scripts/typing_checked_modules.json`` -- not a diagnostic baseline. A module
enters the list only when it produces **zero** mypy diagnostics under the
normative flag set declared in the root ``pyproject.toml``; it leaves only as a
tombstone carrying a removal reason and an issue. There is deliberately no
write mode: admitting or withdrawing a module is a reviewed edit.

Run from the repository root::

    uv run --locked --group dev python scripts/check_typing_policy.py
    uv run --locked --group dev python scripts/check_typing_policy.py --list

The full dev group is required rather than ``--only-group dev``: the
django-stubs plugin imports the Django settings module, so the runtime
dependencies have to be present. ``--list`` prints what the gate is comparing
against and always exits 0.

Three things are checked, in this order, and the checker only runs when the
first two are clean:

1. **The policy record describes the effective policy.** The recorded checker
   versions, mypy flags, overrides, working directory, config path, and module
   lists are fingerprinted; a drift between ``pyproject.toml`` and the record
   fails. The normative flags are additionally asserted against this file --
   both their values and the set of top-level keys ``[tool.mypy]`` may declare
   at all -- so re-recording the fingerprint is not a way to relax one.
2. **The record is append-only.** Entries carry monotonic sequence numbers
   shared by the checked list and the tombstones, so a dropped row leaves either
   a hole in the run or a ``next_sequence`` that is ahead of it, and the gate
   sees that without consulting git history. It is a ledger and not a proof:
   deleting the *terminal* row, rewinding ``next_sequence``, and re-recording
   the fingerprint is self-consistent. What the ledger buys is that hiding a
   withdrawal takes three coordinated edits to a reviewed file.
3. **Checked modules explain themselves.** Every explicit ``Any`` carries a
   categorised ``# typing: <category>: <reason>`` marker, and every suppression
   is a coded ``# type: ignore[code]`` with a categorised reason.

The checker itself is verified before it runs: the distributions installed for
the interpreter that will execute ``-m mypy`` must be the exact versions the
effective policy pins, because those versions are what "clean" means.

Linux on canonical Python 3.12 is the authority. The gate refuses to run on any
other interpreter, and prints a non-authoritative banner on other platforms:
``django-auth-ldap`` and ``python-magic`` are excluded there by platform marker,
so a green local run on Windows does not mean CI is green.
"""

import argparse
import ast
import fnmatch
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess  # invokes the pinned checker, never a shell
import sys
import tomllib
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]

SCHEMA_VERSION = 1
CANONICAL_PYTHON = (3, 12)

# Repository-relative, and deliberately constants rather than record fields the
# record could redefine. mypy runs from itambox/ so that bare app imports
# (``core``, ``organization``, ...) and ``core.settings.dev`` resolve the way
# Django resolves them, and it is handed the root configuration explicitly --
# config discovery from a child directory would silently find nothing.
WORKING_DIRECTORY = "itambox"
CONFIG_FILE = "pyproject.toml"
RECORD_FILE = "scripts/typing_checked_modules.json"
PLATFORM_AUTHORITY = "linux"

# The checker triple whose exact versions decide what "clean" means.
CHECKER_DISTRIBUTIONS = ("mypy", "django-stubs", "djangorestframework-stubs")

# The normative first ratchet. Asserted against pyproject.toml on every run, so
# a relaxed flag fails even when the record is re-recorded to match it.
# `disallow_untyped_calls` is intentionally absent: with the current annotation
# coverage it would pull the whole first-party callee closure of every checked
# module into scope. It is a named later ratchet step.
REQUIRED_FLAGS = {
    "python_version": "3.12",
    "plugins": ["mypy_django_plugin.main"],
    "follow_imports": "silent",
    "disallow_untyped_defs": True,
    "disallow_incomplete_defs": True,
    "check_untyped_defs": True,
    "no_implicit_optional": True,
    "warn_return_any": True,
    "warn_unused_ignores": True,
    "warn_redundant_casts": True,
    "strict_equality": True,
    "disallow_any_generics": True,
    "disallow_any_unimported": True,
    "disallow_untyped_decorators": False,
}
REQUIRED_ERROR_CODES = ("ignore-without-code", "possibly-undefined")

# What ``[tool.mypy]`` may declare at the top level, and nothing else. An
# allowlist rather than a denylist: `ignore_errors`, `disable_error_code`,
# `exclude`, and `follow_imports_for_stubs` all relax or bypass the normative
# policy for every checked module at once, and a denylist would have to predict
# each new one a mypy release introduces. Per-module `overrides` are a separate
# table with their own rule (T-CFG3); this set governs the top level only.
PERMITTED_FLAG_KEYS = frozenset(REQUIRED_FLAGS) | {"enable_error_code"}

# The plugin section is intentionally narrower still. A future django-stubs
# option must be reviewed here before it can affect the meaning of a clean run;
# otherwise a permissive plugin setting could bypass the mypy flag allowlist.
PERMITTED_DJANGO_STUBS_KEYS = frozenset({"django_settings_module"})

# An override may narrow a third-party stub gap for a checked module. It may
# never relax a flag or silence the module.
ALLOWED_OVERRIDE_KEYS = frozenset({"module", "ignore_missing_imports"})

# Why an explicit `Any` is the honest annotation. Each category names something
# a reviewer can check, not a mood.
ANY_CATEGORIES = {
    "external-json": "an untrusted parsed-JSON value whose shape is validated before use",
    "sentinel": "a sentinel whose absent/null/value semantics have no union form yet",
    "third-party-untyped": "a dependency that ships no usable stubs",
    "dynamic-identifier": "an identifier with no narrower stable type across its producers",
}
# Why a diagnostic is suppressed. Platform-specific missing modules belong in a
# per-module override instead, so Linux's warn_unused_ignores stays meaningful.
IGNORE_CATEGORIES = {
    "third-party-untyped": "a dependency that ships no usable stubs",
    "django-plugin-limit": "a limit of the django-stubs plugin, not of the code",
    "external-json": "an untrusted parsed-JSON value whose shape is validated before use",
}

MARKER_PATTERN = r"\btyping\s*:"
ANNOTATION_PATTERN = r"\btyping\s*:\s*(?P<category>[a-z][a-z0-9-]*)\s*:\s*(?P<reason>\S.*?)\s*$"
IGNORE_PATTERN = r"#\s*type:\s*ignore(?P<codes>\[[^\]]*\])?"
MARKER_RE = re.compile(MARKER_PATTERN, re.IGNORECASE)
ANNOTATION_RE = re.compile(ANNOTATION_PATTERN, re.IGNORECASE)
IGNORE_RE = re.compile(IGNORE_PATTERN)

MINIMUM_WITHDRAWAL_REASON = 40
ISSUE_RE = re.compile(r"^#\d+$")

CHECKED_KEYS = {"sequence", "path", "module", "issue", "note"}
WITHDRAWN_KEYS = {"sequence", "path", "module", "issue", "reason"}
RECORD_KEYS = {
    "schema_version",
    "canonical_python",
    "next_sequence",
    "policy_sha256",
    "config",
    "checked",
    "withdrawn",
}


class PolicyError(Exception):
    """Raised when the gate cannot produce a trustworthy result at all."""


class Finding(NamedTuple):
    rule: str
    detail: str


class Policy(NamedTuple):
    config_path: Path
    flags: dict
    overrides: list
    settings_module: str
    checker: dict


def refuse_non_canonical_interpreter(version_info):
    """Return the refusal message for a non-canonical interpreter, else None."""
    if tuple(version_info[:2]) == CANONICAL_PYTHON:
        return None
    canonical = f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}"
    running = f"{version_info[0]}.{version_info[1]}"
    return (
        f"Refusing to run the typing policy gate outside Python {canonical} "
        f"(running {running}); the checked-module policy is pinned to "
        f"python_version = {canonical!r}, so diagnostics from another "
        "interpreter are not comparable to the recorded policy."
    )


def platform_banner(system=None):
    """Return the non-authoritative banner for advisory platforms, else None."""
    system = platform.system() if system is None else system
    if system.lower() == PLATFORM_AUTHORITY:
        return None
    return (
        f"NOT AUTHORITATIVE: this is a {system} run. django-auth-ldap and "
        "python-magic are excluded on non-Linux platforms by platform marker, so "
        "the import graph mypy sees here differs from CI's. Linux on canonical "
        f"Python {CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]} is the authority."
    )


def module_for_path(repository_path):
    """Map a repository-relative source path to the module mypy will report."""
    relative = to_working_directory_paths([repository_path])[0]
    parts = relative[: -len(".py")].split("/")
    if parts[-1] == "__init__":
        parts.pop()
    if not parts:
        raise PolicyError(f"{repository_path} does not name an importable module")
    return ".".join(parts)


def to_working_directory_paths(repository_paths):
    """Translate repository-relative paths to the directory mypy runs from."""
    prefix = f"{WORKING_DIRECTORY}/"
    translated = []
    for repository_path in repository_paths:
        if "\\" in repository_path:
            raise PolicyError(f"record paths must use '/' separators: {repository_path!r}")
        if not repository_path.startswith(prefix) or not repository_path.endswith(".py"):
            raise PolicyError(
                f"checked paths must be repository-relative Python files under {prefix}: {repository_path!r}"
            )
        translated.append(repository_path[len(prefix) :])
    return translated


def _requirement_pins(dev_requirements):
    pins = {}
    pattern = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)(?:\[[^\]]*\])?(?P<operator>[=<>!~]+)(?P<version>.+)$")
    for requirement in dev_requirements:
        match = pattern.match(str(requirement).strip())
        if match is None:
            continue
        pins[match.group("name").lower()] = (match.group("operator"), match.group("version").strip())
    return pins


def _checker_versions(data):
    pins = _requirement_pins(data.get("dependency-groups", {}).get("dev", []))
    versions = {}
    for distribution in CHECKER_DISTRIBUTIONS:
        pin = pins.get(distribution)
        if pin is None:
            raise PolicyError(f"{distribution} is not declared in the dev dependency group")
        operator, version = pin
        if operator != "==":
            raise PolicyError(
                f"{distribution} must be pinned exactly (found {operator}{version}); the checked-module "
                "policy is bound to the checker and stub versions that produced it"
            )
        versions[distribution] = version
    return versions


def load_effective_policy(root):
    """Read the effective typing policy out of the repository's own config."""
    config_path = (Path(root) / CONFIG_FILE).resolve()
    if not config_path.is_file():
        raise PolicyError(f"no typing configuration at {config_path}")
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise PolicyError(f"cannot read {config_path}: {exc}") from exc

    mypy_section = data.get("tool", {}).get("mypy")
    if not isinstance(mypy_section, dict):
        raise PolicyError(f"{config_path} declares no [tool.mypy] section to check against")
    django_stubs_section = data.get("tool", {}).get("django-stubs")
    if not isinstance(django_stubs_section, dict):
        raise PolicyError(f"{config_path} declares no [tool.django-stubs] section")
    unexpected_django_stubs_keys = sorted(set(django_stubs_section) - PERMITTED_DJANGO_STUBS_KEYS)
    if unexpected_django_stubs_keys:
        raise PolicyError(
            f"{config_path} declares unpermitted [tool.django-stubs] key(s) {unexpected_django_stubs_keys}; "
            "review and allow a plugin option deliberately before using it"
        )
    settings_module = django_stubs_section.get("django_settings_module")
    if not settings_module:
        raise PolicyError(f"{config_path} declares no [tool.django-stubs] django_settings_module")

    return Policy(
        config_path=config_path,
        flags={key: value for key, value in mypy_section.items() if key != "overrides"},
        overrides=list(mypy_section.get("overrides", [])),
        settings_module=settings_module,
        checker=_checker_versions(data),
    )


def derived_config(policy):
    """The configuration metadata the record must mirror, in record shape."""
    return {
        "config_file": CONFIG_FILE,
        "working_directory": WORKING_DIRECTORY,
        "settings_module": policy.settings_module,
        "platform_authority": PLATFORM_AUTHORITY,
        "checker": dict(sorted(policy.checker.items())),
        "flags": policy.flags,
        "overrides": policy.overrides,
    }


def _fingerprint(config, checked, withdrawn):
    payload = {
        "schema_version": SCHEMA_VERSION,
        "canonical_python": f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}",
        "config": config,
        "required_flags": REQUIRED_FLAGS,
        "permitted_flag_keys": sorted(PERMITTED_FLAG_KEYS),
        "allowed_override_keys": sorted(ALLOWED_OVERRIDE_KEYS),
        "permitted_django_stubs_keys": sorted(PERMITTED_DJANGO_STUBS_KEYS),
        "required_error_codes": list(REQUIRED_ERROR_CODES),
        "any_categories": sorted(ANY_CATEGORIES),
        "ignore_categories": sorted(IGNORE_CATEGORIES),
        "marker_pattern": ANNOTATION_PATTERN,
        "ignore_pattern": IGNORE_PATTERN,
        "checked": [entry["path"] for entry in checked],
        "withdrawn": [entry["path"] for entry in withdrawn],
        "modules": {entry["path"]: entry["module"] for entry in [*checked, *withdrawn]},
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def compute_policy_fingerprint(policy, checked, withdrawn):
    """Bind a record to the policy that admitted its modules."""
    return _fingerprint(derived_config(policy), checked, withdrawn)


def _validate_entries(entries, label, required_keys):
    if not isinstance(entries, list):
        raise PolicyError(f"record {label!r} must be a list")
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or set(entry) != required_keys:
            raise PolicyError(f"record {label}[{index}] must have exactly the fields {sorted(required_keys)}")
        if not isinstance(entry["sequence"], int) or isinstance(entry["sequence"], bool):
            raise PolicyError(f"record {label}[{index}] has a non-integer sequence")
        for key in ("path", "module", "issue"):
            if not isinstance(entry[key], str):
                raise PolicyError(f"record {label}[{index}] has a non-string {key}")


def load_record(root):
    """Read and structurally validate the checked-module record."""
    record_path = Path(root) / RECORD_FILE
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read {RECORD_FILE}: {exc}") from exc
    if not isinstance(record, dict) or set(record) != RECORD_KEYS:
        raise PolicyError(f"{RECORD_FILE} must have exactly the fields {sorted(RECORD_KEYS)}")
    if record["schema_version"] != SCHEMA_VERSION:
        raise PolicyError(f"expected typing record schema {SCHEMA_VERSION}")
    if record["canonical_python"] != f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}":
        raise PolicyError(f"record canonical_python must be '{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}'")
    if not isinstance(record["config"], dict) or not isinstance(record["policy_sha256"], str):
        raise PolicyError(f"{RECORD_FILE} has an invalid config or policy_sha256 field")
    _validate_entries(record["checked"], "checked", CHECKED_KEYS)
    _validate_entries(record["withdrawn"], "withdrawn", WITHDRAWN_KEYS)
    return record


def _check_flags(policy):
    findings = []
    for flag, expected in sorted(REQUIRED_FLAGS.items()):
        actual = policy.flags.get(flag, "<absent>")
        if actual != expected:
            findings.append(Finding("T-CFG2", f"[tool.mypy] {flag} must be {expected!r}, found {actual!r}"))
    enabled = policy.flags.get("enable_error_code", [])
    for code in REQUIRED_ERROR_CODES:
        if code not in enabled:
            findings.append(Finding("T-CFG2", f"[tool.mypy] enable_error_code must contain {code!r}"))
    unexpected = sorted(set(policy.flags) - PERMITTED_FLAG_KEYS)
    if unexpected:
        findings.append(
            Finding(
                "T-CFG2",
                f"[tool.mypy] declares {unexpected}, which the typing policy does not permit at the top "
                f"level; only the normative flags and enable_error_code may be set there, because a key "
                "like ignore_errors or disable_error_code relaxes every checked module at once",
            )
        )
    return findings


def _override_patterns(override):
    module = override.get("module", [])
    return [module] if isinstance(module, str) else list(module)


def _check_overrides(policy, checked):
    findings = []
    modules = [entry["module"] for entry in checked]
    for override in policy.overrides:
        relaxed = sorted(set(override) - ALLOWED_OVERRIDE_KEYS)
        if not relaxed:
            continue
        for pattern in _override_patterns(override):
            captured = [module for module in modules if fnmatch.fnmatchcase(module, pattern)]
            if captured:
                findings.append(
                    Finding(
                        "T-CFG3",
                        f"[[tool.mypy.overrides]] module = {pattern!r} sets {relaxed} for checked "
                        f"module(s) {captured}; a checked module may only take ignore_missing_imports",
                    )
                )
    return findings


def _check_sequences(record):
    entries = [*record["checked"], *record["withdrawn"]]
    sequences = sorted(entry["sequence"] for entry in entries)
    expected = list(range(1, len(sequences) + 1))
    findings = []
    if sequences != expected:
        findings.append(
            Finding(
                "T-REC3",
                f"admission sequences must be the contiguous run {expected or [0]} with no gaps or "
                f"duplicates, found {sequences}; a checked module leaves only as a tombstone that "
                "keeps its sequence",
            )
        )
    next_sequence = record["next_sequence"]
    if next_sequence != len(sequences) + 1:
        findings.append(Finding("T-REC3", f"next_sequence must be {len(sequences) + 1}, found {next_sequence}"))
    return findings


def _check_paths(root, record):
    findings = []
    seen = set()
    for entry in record["checked"]:
        if entry["path"] in seen:
            findings.append(Finding("T-REC4", f"{entry['path']} is admitted twice"))
        seen.add(entry["path"])
        if not (Path(root) / entry["path"]).is_file():
            findings.append(
                Finding(
                    "T-REC4",
                    f"checked path {entry['path']} does not exist; a moved or deleted module is a "
                    "tombstone plus a new entry, never a silent edit",
                )
            )
    for entry in [*record["checked"], *record["withdrawn"]]:
        expected = module_for_path(entry["path"])
        if entry["module"] != expected:
            findings.append(
                Finding("T-REC6", f"{entry['path']} maps to module {expected!r}, recorded as {entry['module']!r}")
            )
    if record["checked"] != sorted(record["checked"], key=lambda entry: entry["path"]):
        findings.append(Finding("T-REC4", "checked entries must be sorted by path"))
    return findings


def _check_tombstones(record):
    findings = []
    for entry in record["withdrawn"]:
        if len(entry["reason"].strip()) < MINIMUM_WITHDRAWAL_REASON:
            findings.append(
                Finding(
                    "T-REC5",
                    f"withdrawal of {entry['path']} needs a reason of at least "
                    f"{MINIMUM_WITHDRAWAL_REASON} characters explaining what could not be typed",
                )
            )
        if not ISSUE_RE.match(entry["issue"].strip()):
            findings.append(Finding("T-REC5", f"withdrawal of {entry['path']} needs a follow-up issue like '#93'"))
    return findings


def _check_fingerprint(policy, record):
    checked, withdrawn = record["checked"], record["withdrawn"]
    stored = record["policy_sha256"]
    effective = compute_policy_fingerprint(policy, checked, withdrawn)
    findings = []
    # The expected value is published rather than written: there is no write
    # mode, so a reviewer updating the record by hand needs the hash in front of
    # them -- and seeing it in the diff is the point of making them paste it.
    if stored != _fingerprint(record["config"], checked, withdrawn):
        findings.append(
            Finding(
                "T-REC2",
                f"policy_sha256 does not match the record's own config and module lists (recorded "
                f"{stored}, expected {effective} once the config block describes {CONFIG_FILE})",
            )
        )
    if stored != effective:
        findings.append(
            Finding(
                "T-REC2",
                f"policy_sha256 does not match the effective policy in {CONFIG_FILE} (recorded {stored}, "
                f"expected {effective})",
            )
        )
    derived = derived_config(policy)
    if record["config"] != derived:
        differing = sorted(
            key for key in set(derived) | set(record["config"]) if derived.get(key) != record["config"].get(key)
        )
        findings.append(
            Finding("T-CFG5", f"recorded config no longer describes {CONFIG_FILE}; differing key(s): {differing}")
        )
    return findings


def check_record(root, policy, record):
    """Every rule that can be decided from the record and the configuration."""
    findings = [
        *_check_flags(policy),
        *_check_overrides(policy, record["checked"]),
        *_check_fingerprint(policy, record),
        *_check_sequences(record),
        *_check_paths(root, record),
        *_check_tombstones(record),
    ]
    return tuple(findings)


def _trailing_comment(lines, line_number):
    line = lines[line_number - 1]
    return line.split("#", 1)[1].strip() if "#" in line else None


def _comment_block_above(lines, line_number):
    collected = []
    index = line_number - 2
    while index >= 0 and lines[index].strip().startswith("#"):
        collected.append(lines[index].strip().lstrip("#").strip())
        index -= 1
    return " ".join(reversed(collected)) if collected else None


def _classify(comment, categories):
    """Map a comment to (category, problem); both None means "not a marker"."""
    if comment is None or not MARKER_RE.search(comment):
        return None, None
    match = ANNOTATION_RE.search(comment)
    if match is None:
        return None, f"marker must read '# typing: <category>: <reason>' (categories: {', '.join(sorted(categories))})"
    category = match.group("category").lower()
    if category not in categories:
        return None, f"unrecognised category {category!r} (categories: {', '.join(sorted(categories))})"
    return category, None


def _scope_chain(tree):
    """(first_line, last_line, header_line, label) for every definition.

    ``first_line`` is the first decorator when there is one, ``header_line`` the
    ``def``/``class`` line itself; a marker may sit above either.
    """
    scopes = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        first = min([node.lineno, *[item.lineno for item in node.decorator_list]])
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        scopes.append((first, node.end_lineno, node.lineno, f"{kind} `{node.name}`"))
    return scopes


def _explicit_any_lines(tree):
    """The line of every explicit ``Any``; the import statement is not one."""
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "Any":
            lines.append(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "Any":
            lines.append(node.lineno)
    return sorted(set(lines))


def _candidate_comments(lines, line_number, scopes):
    """Where a marker for this occurrence may live, nearest first."""
    yield _trailing_comment(lines, line_number)
    yield _comment_block_above(lines, line_number)
    enclosing = [scope for scope in scopes if scope[0] <= line_number <= scope[1]]
    for first, _last, header, _label in sorted(enclosing, key=lambda scope: scope[0], reverse=True):
        yield _trailing_comment(lines, header)
        yield _comment_block_above(lines, first)


def _scope_label(line_number, scopes):
    enclosing = [scope for scope in scopes if scope[0] <= line_number <= scope[1]]
    return sorted(enclosing, key=lambda scope: scope[0])[-1][3] if enclosing else "module scope"


def _check_any_markers(relative_path, source, tree):
    lines = source.splitlines()
    scopes = _scope_chain(tree)
    findings = []
    resolved_lines = {}
    for line_number in _explicit_any_lines(tree):
        category, problem = None, None
        for comment in _candidate_comments(lines, line_number, scopes):
            category, problem = _classify(comment, ANY_CATEGORIES)
            if category is not None or problem is not None:
                break
        if category is None and problem is None:
            # A contiguous run of annotations (dataclass fields, a multi-line
            # signature) is covered by the marker on the line above it.
            category = resolved_lines.get(line_number - 1)
        if problem is not None:
            findings.append(Finding("T-ANY2", f"{relative_path}:{line_number}: {problem}"))
        elif category is None:
            findings.append(
                Finding(
                    "T-ANY1",
                    f"{relative_path}:{line_number}: explicit `Any` in {_scope_label(line_number, scopes)} "
                    "has no '# typing: <category>: <reason>' marker",
                )
            )
        resolved_lines[line_number] = category
    return findings


def _check_ignore_grammar(relative_path, source):
    lines = source.splitlines()
    findings = []
    for number, line in enumerate(lines, start=1):
        match = IGNORE_RE.search(line)
        if match is None:
            continue
        if not match.group("codes"):
            findings.append(
                Finding("T-IGN1", f"{relative_path}:{number}: '# type: ignore' must name its error code(s)")
            )
            continue
        category, problem = _classify(line, IGNORE_CATEGORIES)
        if category is None and problem is None:
            category, problem = _classify(_comment_block_above(lines, number), IGNORE_CATEGORIES)
        if problem is not None:
            findings.append(Finding("T-IGN2", f"{relative_path}:{number}: {problem}"))
        elif category is None:
            findings.append(
                Finding(
                    "T-IGN1",
                    f"{relative_path}:{number}: a suppression needs a categorised reason "
                    "('# typing: <category>: <reason>') on the same or the preceding line",
                )
            )
    return findings


def check_markers(root, record):
    """The explicit-``Any`` and suppression grammars, over checked modules only."""
    findings = []
    for entry in record["checked"]:
        path = Path(root) / entry["path"]
        if not path.is_file():
            continue
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=entry["path"])
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise PolicyError(f"cannot parse {entry['path']}: {exc}") from exc
        findings.extend(_check_any_markers(entry["path"], source, tree))
        findings.extend(_check_ignore_grammar(entry["path"], source))
    return tuple(findings)


def mypy_command(root, checked_paths):
    """The exact checker invocation, so tests can assert it without mypy."""
    return [
        sys.executable,
        "-m",
        "mypy",
        "--config-file",
        str((Path(root) / CONFIG_FILE).resolve()),
        "--cache-dir",
        str((Path(root) / ".mypy_cache").resolve()),
        "--no-error-summary",
        "--show-error-codes",
        *to_working_directory_paths(checked_paths),
    ]


def _checker_environment(policy):
    environment = dict(os.environ)
    # The plugin imports the settings module; pin the environment it reads
    # rather than inheriting whatever the developer happens to export.
    environment["ITAMBOX_ENV"] = "dev"
    environment["DJANGO_SETTINGS_MODULE"] = policy.settings_module
    # Prevent developer-local import paths from shadowing the checked source or
    # the settings module. Makefile/CI/pre-commit then share one resolution rule.
    environment["PYTHONPATH"] = ""
    environment["MYPYPATH"] = ""
    return environment


def verify_installed_checker(policy, version_lookup=None):
    """Fail closed unless the installed checker triple is the pinned one.

    ``uv run --locked`` normally guarantees this, but the gate is also run with
    ``--no-sync`` in CI and by hand outside uv. What "clean" means is decided by
    the checker and its stubs, so a green run under a different mypy proves
    nothing about the recorded policy.
    """
    version_lookup = importlib.metadata.version if version_lookup is None else version_lookup
    for distribution in CHECKER_DISTRIBUTIONS:
        expected = policy.checker[distribution]
        try:
            installed = version_lookup(distribution)
        except importlib.metadata.PackageNotFoundError as exc:
            raise PolicyError(
                f"{distribution}=={expected} is pinned by the typing policy but is not installed for "
                f"{sys.executable}; run `uv sync --locked --group dev`"
            ) from exc
        if installed != expected:
            raise PolicyError(
                f"{distribution}=={installed} is installed for {sys.executable}, but the typing policy "
                f"pins {distribution}=={expected}; the checked modules were admitted under the pinned "
                "version, so diagnostics from another one are not comparable"
            )
    return None


def run_checker(root, policy, checked_paths, runner=None):
    """Run mypy from the working directory against the checked modules."""
    if not checked_paths:
        return ()
    if runner is None:
        # Only a real invocation can be lied to by the environment, and keeping
        # the inspection here rather than in check_all() is what lets the
        # behavioural suite run on the bare interpreter CI uses for the gate
        # suites -- where mypy is deliberately not installed yet.
        verify_installed_checker(policy)
        runner = subprocess.run
    command = mypy_command(root, checked_paths)
    completed = runner(
        command,
        cwd=str((Path(root) / WORKING_DIRECTORY).resolve()),
        env=_checker_environment(policy),
        check=False,
    )
    if completed.returncode == 0:
        return ()
    # mypy exits 1 when it checked the code and found diagnostics, and 2 or more
    # when it could not produce a result at all -- an unreadable config, a
    # plugin that failed to load, a crash. The second is not a finding about the
    # code, and reporting it as one would let a broken plugin read as a typing
    # regression a contributor is expected to fix in their own module.
    if completed.returncode != 1:
        raise PolicyError(
            f"mypy exited {completed.returncode} without producing a usable result (a configuration, "
            f"plugin, or internal error rather than type diagnostics); command: {' '.join(command)}"
        )
    return (
        Finding(
            "T-RUN1",
            "mypy reported diagnostics in the checked module(s); a module is admitted only while it "
            "checks clean -- fix the annotations or withdraw it with a tombstone",
        ),
    )


def check_all(root, runner=None):
    """Every rule, in order. The checker runs only once the record is sound."""
    policy = load_effective_policy(root)
    record = load_record(root)
    findings = [*check_record(root, policy, record), *check_markers(root, record)]
    if findings:
        return tuple(findings)
    return run_checker(root, policy, [entry["path"] for entry in record["checked"]], runner=runner)


def build_parser():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="Repository root to check.")
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the effective policy and the checked-module record; checks nothing.",
    )
    return parser


def _print_listing(root, policy, record):
    print(f"config file        {policy.config_path}")
    print(f"working directory  {(Path(root) / WORKING_DIRECTORY).resolve()}")
    print(f"settings module    {policy.settings_module}")
    print(f"platform authority {PLATFORM_AUTHORITY} / python {CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}")
    for distribution, version in sorted(policy.checker.items()):
        print(f"checker            {distribution}=={version}")
    for flag, value in sorted(policy.flags.items()):
        print(f"flag               {flag} = {value!r}")
    # Both hashes, always: the gate has no write mode, so admitting a module
    # means pasting the expected value into the record as a reviewed edit.
    print(f"policy_sha256      {record['policy_sha256']} (recorded)")
    print(
        f"policy_sha256      {compute_policy_fingerprint(policy, record['checked'], record['withdrawn'])} "
        "(expected for these module lists)"
    )
    for entry in record["checked"]:
        print(f"checked            [{entry['sequence']}] {entry['path']} -> {entry['module']}")
    for entry in record["withdrawn"]:
        print(f"withdrawn          [{entry['sequence']}] {entry['path']} ({entry['issue']})")


def main(argv=None):
    arguments = build_parser().parse_args(argv)
    refusal = refuse_non_canonical_interpreter(sys.version_info)
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    banner = platform_banner()
    if banner:
        print(banner, file=sys.stderr)

    try:
        if arguments.list:
            policy = load_effective_policy(arguments.root)
            _print_listing(arguments.root, policy, load_record(arguments.root))
            return 0
        findings = check_all(arguments.root)
    except PolicyError as exc:
        print(f"typing policy gate failed: {exc}", file=sys.stderr)
        return 2

    if findings:
        print("typing policy: the checked-module record no longer holds:\n")
        for finding in findings:
            print(f"  {finding.rule} {finding.detail}")
        print(
            f"\nAdmission and withdrawal are reviewed edits of {RECORD_FILE}; this gate has no write "
            "mode. See itambox/docs/development/typing-policy.md.\n"
        )
        return 1

    record = load_record(arguments.root)
    print(
        f"typing policy: {len(record['checked'])} checked module(s) clean under the recorded policy; "
        f"{len(record['withdrawn'])} withdrawn."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
