#!/usr/bin/env python
"""Shared measurement policy for ITAMbox's coverage quality ratchets.

Three gates read this module so they cannot drift apart:

* ``scripts/check_coverage_baseline.py`` -- global line and branch ratchet.
* ``scripts/check_diff_coverage.py``     -- differential gate for changed code.
* ``scripts/check_test_report.py``       -- duration/skip reporting ratchet.

The module owns three things:

1. **What counts as production code.** ``OMIT_PATTERNS`` is the single
   declaration of what is deliberately not measured (generated migrations, test
   code, process entry points). ``[tool.coverage.run] omit`` in ``pyproject.toml``
   must match it exactly, and ``verify_measurement_policy`` refuses to run when
   it does not. Silently omitting an application package would otherwise raise
   every reported rate without any test being written.

2. **How a coverage report is validated.** A report that was produced without
   branch measurement, or that measured nothing, is not a weaker signal -- it is
   an unusable one. Every loader path here fails closed (exit code 2) rather
   than reporting a comfortable number from a broken run.

3. **The policy fingerprint.** The recorded baseline is bound by SHA-256 to the
   effective policy (schema, omit patterns, line exclusions, differential
   target, exemptions) and to the measuring coverage.py version. Changing any of
   them invalidates the baseline and forces a reviewed regeneration, so a policy
   relaxation can never be mistaken for a genuine improvement.

Stdlib only, and deliberately so: these gates must run in CI, in pre-commit, and
on a contributor's checkout without an application environment.
"""

import collections
import fnmatch
import hashlib
import json
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = REPO_ROOT / "scripts" / "coverage_baseline.json"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

SCHEMA_VERSION = 1
CANONICAL_PYTHON = (3, 12)

# Coverage is measured from ``itambox/`` (where ``pytest`` runs), so measured
# paths are relative to that directory while git reports repository-relative
# paths. Every conversion between the two goes through ``to_coverage_path`` /
# ``to_repo_path``.
COVERAGE_ROOT = "itambox"

# The exclusion policy, mirrored by ``[tool.coverage.run] omit``. Generated
# code, test code, and process entry points only -- never hand-written
# application code. Keep the two lists identical; the gate enforces it.
OMIT_PATTERNS = (
    "*/migrations/*",
    "*/tests/*",
    "*/tests.py",
    "*/conftest.py",
    "manage.py",
    "core/wsgi.py",
    "core/asgi.py",
    "itambox/wsgi.py",
    "itambox/asgi.py",
)

# Mirrored by ``[tool.coverage.report] exclude_also``. Each pattern hides real
# source lines from every rate below, so the list stays short and is limited to
# constructs that carry no runtime behaviour.
EXCLUDE_ALSO_PATTERNS = (
    "if TYPE_CHECKING:",
    "raise NotImplementedError",
    r"@(abc\.)?abstractmethod",
    "if __name__ == .__main__.:",
)

# Differential target for changed production code. A changed executable line
# counts as covered only when it was executed *and* is not the origin of an
# untaken branch -- a half-taken conditional on new code is exactly the case
# line coverage flatters (see docs/development/test-coverage-policy.md).
DIFF_COVERAGE_TARGET = 85.0

# Changed files that the Django pytest run structurally cannot measure. Each
# needs a reason and a gate that covers it instead; anything without one is a
# hard failure, never a silent pass.
DIFF_COVERAGE_EXEMPTIONS = (
    (
        "scripts/*",
        "repository tooling, executed outside the Django coverage session; "
        "covered by the scripts/tests/* unittest suites CI runs separately",
    ),
    (
        "itambox/tests/e2e/*",
        "Playwright suite, run by the e2e workflow against a live server",
    ),
    (
        "itambox/static/*",
        "compiled and vendored frontend assets, type-checked and linted by the frontend job",
    ),
    (
        "itambox/core/reports/legacy.py",
        "temporary compatibility provider moved ~95% verbatim from core/reports/compiler.py "
        "by issue #83; covered by the existing per-report integration suites "
        "(core/tests/test_report_*.py) and the report-characterization matrix, both part of the test job",
    ),
)

# Downward jitter allowed before a rate change is treated as a regression. Not
# an allowance for uncovered code: a change that adds untested lines is caught
# by the differential gate long before it moves a global rate this far.
TOLERANCE_PERCENTAGE_POINTS = 0.10

# Improvements above this margin must be recorded. Without it, a genuine
# improvement silently becomes headroom that later untested code can spend.
DRIFT_PERCENTAGE_POINTS = 1.00


# The environment a recorded baseline must come from. Every gate *reads* its
# baseline anywhere -- a contributor on Windows must be able to run them -- but
# a baseline *written* anywhere else records numbers CI can never reproduce.
CANONICAL_PLATFORM = "linux"

# Coverage denominator fields whose unexpected shrink must be reviewed. The
# global gate imports this declaration and the policy fingerprint serialises it,
# so removing a ratchet necessarily invalidates the reviewed baseline.
RATCHETED_COVERAGE_SIZE_FIELDS = (
    ("measured_files", "measured file(s)"),
    ("num_statements", "measured statement(s)"),
    ("num_branches", "measured branch(es)"),
)


class PolicyError(Exception):
    """Raised when a gate cannot produce a trustworthy result."""


CoverageReport = collections.namedtuple("CoverageReport", "coverage_version files totals")

MeasurementConfig = collections.namedtuple("MeasurementConfig", "branch relative_files omit exclude_also fail_under")


def normalise_path(path):
    """Return a POSIX-style path, whatever platform recorded it."""
    text = str(path).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def to_repo_path(coverage_path):
    """Map a coverage-relative path to a repository-relative one."""
    return f"{COVERAGE_ROOT}/{normalise_path(coverage_path)}"


def to_coverage_path(repo_path):
    """Map a repository-relative path into coverage space, or None if outside it."""
    normalised = normalise_path(repo_path)
    prefix = f"{COVERAGE_ROOT}/"
    if not normalised.startswith(prefix):
        return None
    return normalised[len(prefix) :]


def matches_any(path, patterns):
    """True when ``path`` matches any coverage-style glob in ``patterns``."""
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def is_omitted(coverage_path):
    """True when a coverage-relative path is excluded by the measurement policy.

    Mirrors coverage.py's own glob semantics, not plain ``fnmatch``: a leading
    ``*/`` is optional (so ``*/conftest.py`` omits a top-level ``conftest.py``,
    exactly as the measured run does) and a pattern without a separator matches
    that file name at any depth. The two must agree -- a path coverage omitted
    but this function reports as measured is a file the differential gate then
    fails on as "never measured", which is a false failure, not a safe one.
    """
    normalised = normalise_path(coverage_path)
    for pattern in OMIT_PATTERNS:
        if "/" not in pattern:
            pattern = f"*/{pattern}"
        candidates = (pattern, pattern[2:]) if pattern.startswith("*/") else (pattern,)
        if matches_any(normalised, candidates):
            return True
    return False


def exemption_reason(repo_path):
    """Return the documented reason a repository path is unmeasurable, or None."""
    normalised = normalise_path(repo_path)
    for pattern, reason in DIFF_COVERAGE_EXEMPTIONS:
        if fnmatch.fnmatch(normalised, pattern):
            return reason
    return None


def rate(covered, total):
    """Percentage covered, treating "nothing to cover" as fully covered."""
    if total == 0:
        return 100.0
    return round(covered * 100.0 / total, 2)


def line_rate(totals):
    return rate(totals["covered_lines"], totals["num_statements"])


def branch_rate(totals):
    return rate(totals["covered_branches"], totals["num_branches"])


def combined_rate(totals):
    """The single percentage coverage.py itself reports and gates ``fail_under`` on.

    With branch measurement enabled, coverage.py folds branches into one figure
    rather than reporting the line rate. The ratchet holds the two rates apart
    (a branch regression must not be maskable by a line improvement), but any
    comparison against ``fail_under`` has to use coverage's own metric.
    """
    return rate(
        totals["covered_lines"] + totals["covered_branches"],
        totals["num_statements"] + totals["num_branches"],
    )


def load_measurement_config(pyproject_path=PYPROJECT_PATH):
    """Read the effective coverage configuration out of pyproject.toml."""
    try:
        raw = tomllib.loads(Path(pyproject_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise PolicyError(f"cannot read {pyproject_path}: {exc}") from exc
    coverage_config = raw.get("tool", {}).get("coverage", {})
    run_config = coverage_config.get("run", {})
    report_config = coverage_config.get("report", {})
    return MeasurementConfig(
        branch=run_config.get("branch", False),
        relative_files=run_config.get("relative_files", False),
        omit=tuple(run_config.get("omit", ())),
        exclude_also=tuple(report_config.get("exclude_also", ())),
        fail_under=report_config.get("fail_under", 0),
    )


def verify_measurement_policy(config):
    """Refuse to gate a run whose measurement configuration was weakened."""
    problems = []
    if not config.branch:
        problems.append("[tool.coverage.run] branch must be true -- branch coverage is part of the ratchet")
    if not config.relative_files:
        problems.append("[tool.coverage.run] relative_files must be true -- absolute paths are not comparable")
    if config.omit != OMIT_PATTERNS:
        problems.append(
            "[tool.coverage.run] omit does not match the declared exclusion policy in "
            "scripts/coverage_policy.py (OMIT_PATTERNS); an unreviewed omit entry hides "
            "production code from every rate"
        )
    if config.exclude_also != EXCLUDE_ALSO_PATTERNS:
        problems.append(
            "[tool.coverage.report] exclude_also does not match the declared line-exclusion "
            "policy in scripts/coverage_policy.py (EXCLUDE_ALSO_PATTERNS)"
        )
    if problems:
        raise PolicyError("measurement policy mismatch:\n  - " + "\n  - ".join(problems))


def _require_summary_fields(path, summary):
    required = {
        "covered_lines",
        "num_statements",
        "excluded_lines",
        "num_branches",
        "covered_branches",
        "num_partial_branches",
    }
    missing = sorted(required - set(summary))
    if missing:
        raise PolicyError(f"coverage report entry {path!r} is missing summary field(s): {', '.join(missing)}")


def _read_report_json(path):
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PolicyError(
            f"no coverage report at {path}; the gate cannot pass without a measured run "
            "(produce one with `pytest --cov=. --cov-report=json:coverage.json`)"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"cannot read coverage report {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError(f"coverage report {path} is not a JSON object")
    return raw


def _validate_meta(path, raw):
    """Return the measuring coverage.py version, refusing an incomparable run."""
    meta = raw.get("meta")
    if not isinstance(meta, dict):
        raise PolicyError(f"coverage report {path} has no meta section")
    if meta.get("branch_coverage") is not True:
        raise PolicyError(
            f"coverage report {path} was produced without branch measurement; "
            "the recorded baseline holds both line and branch rates, so a line-only "
            "report cannot be compared against it. The usual cause is a `pytest --cov` "
            "run without `--cov-config=../pyproject.toml`: coverage.py reads its "
            "configuration from the current directory, and pytest runs from itambox/, "
            "so the root configuration is ignored entirely -- no branches, and "
            "migrations and test files counted as covered source"
        )
    coverage_version = meta.get("version")
    if not isinstance(coverage_version, str) or not coverage_version:
        raise PolicyError(f"coverage report {path} does not record the coverage.py version")
    return coverage_version


def _validate_totals_section(path, raw):
    totals = raw.get("totals")
    if not isinstance(totals, dict):
        raise PolicyError(f"coverage report {path} has no totals section")
    _require_summary_fields("totals", totals)
    if totals["num_statements"] <= 0:
        raise PolicyError(f"coverage report {path} recorded zero statements")
    return totals


def load_coverage_report(coverage_json_path):
    """Load and validate a coverage.py JSON report.

    Fails closed on every unusable shape: unreadable file, missing branch
    measurement, or an empty measured set. A run that measured nothing must not
    be reported as a run that covered everything.
    """
    path = Path(coverage_json_path)
    raw = _read_report_json(path)
    coverage_version = _validate_meta(path, raw)

    files = raw.get("files")
    if not isinstance(files, dict) or not files:
        raise PolicyError(f"coverage report {path} measured no files")
    totals = _validate_totals_section(path, raw)

    normalised_files = {}
    for measured_path, entry in files.items():
        if not isinstance(entry, dict):
            raise PolicyError(f"coverage report entry {measured_path!r} is not an object")
        summary = entry.get("summary")
        if not isinstance(summary, dict):
            raise PolicyError(f"coverage report entry {measured_path!r} has no summary")
        _require_summary_fields(measured_path, summary)
        normalised_files[normalise_path(measured_path)] = entry

    return CoverageReport(coverage_version=coverage_version, files=normalised_files, totals=totals)


def coverage_series(coverage_version):
    """The ``major.minor`` series a baseline is comparable within."""
    parts = coverage_version.split(".")
    if len(parts) < 2:
        raise PolicyError(f"unrecognised coverage.py version {coverage_version!r}")
    return f"{parts[0]}.{parts[1]}"


def compute_policy_fingerprint(coverage_version_series):
    """Bind a baseline to the policy and the tool version that produced it."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "canonical_python": f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}",
        "canonical_platform": CANONICAL_PLATFORM,
        "coverage_series": coverage_version_series,
        "coverage_root": COVERAGE_ROOT,
        "omit": list(OMIT_PATTERNS),
        "exclude_also": list(EXCLUDE_ALSO_PATTERNS),
        "diff_target": DIFF_COVERAGE_TARGET,
        "diff_exemptions": [list(item) for item in DIFF_COVERAGE_EXEMPTIONS],
        "tolerance": TOLERANCE_PERCENTAGE_POINTS,
        "drift": DRIFT_PERCENTAGE_POINTS,
        "ratcheted_size_fields": [field for field, _label in RATCHETED_COVERAGE_SIZE_FIELDS],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_baseline_write_environment(version_info=None, platform_name=None):
    """Refuse to record a baseline outside the environment CI measures in.

    A baseline is a number every later run is held to, so it has to come from
    the run everyone else reproduces. Both the interpreter and the platform
    move the measurement: a different Python takes different branches through
    version-conditional code, and a different platform changes which optional
    dependencies import at all (``python-ldap`` and ``python-magic`` are absent
    on Windows by declared policy, and their fallbacks are different code).
    Recording either would ratchet the project against numbers CI can never
    reach again.

    Reading a baseline is unrestricted; only writing one is pinned.
    """
    version_info = tuple(version_info or sys.version_info[:2])
    platform_name = platform_name if platform_name is not None else sys.platform
    problems = []
    if version_info != CANONICAL_PYTHON:
        problems.append(
            f"Python {CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]} is required "
            f"(running {version_info[0]}.{version_info[1]})"
        )
    if not platform_name.startswith(CANONICAL_PLATFORM):
        problems.append(f"{CANONICAL_PLATFORM} is required (running on {platform_name})")
    if problems:
        raise PolicyError(
            "refusing to record a baseline outside the canonical measurement environment: "
            + "; ".join(problems)
            + ". Record it from a clean CI run instead, so the recorded numbers are the ones "
            "every later run is compared against"
        )


def write_summary(summary_file, markdown):
    """Append a markdown block to a job-summary file, when one was requested."""
    if not summary_file:
        return
    path = Path(summary_file)
    try:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(markdown.rstrip("\n") + "\n\n")
    except OSError as exc:
        raise PolicyError(f"cannot write summary to {path}: {exc}") from exc
