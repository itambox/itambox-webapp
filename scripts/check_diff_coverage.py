#!/usr/bin/env python
"""Differential coverage gate for changed production code.

The global ratchet stops coverage sliding backwards, but on a ~19k-statement
code base a few hundred untested new lines barely move a global percentage. The
differential gate is the one that actually holds new work to a high standard: it
looks only at the lines this change added or modified, and requires
``DIFF_COVERAGE_TARGET`` percent of them to be genuinely exercised.

"Genuinely exercised" is branch-aware. A changed line counts as covered only
when it was executed **and** is not the origin of an untaken branch. A new
``if`` whose ``else`` never runs is precisely the case line coverage reports as
100% and a tenancy or permission bug hides in.

Fail-closed handling of unmeasured files is the point of the gate, not a corner
case. A changed Python file that the coverage run never measured produces a
failure, never a pass -- the only exceptions are the documented, reason-carrying
patterns in ``scripts/coverage_policy.py`` (``DIFF_COVERAGE_EXEMPTIONS``), each
of which names the gate that covers that path instead. Test code, generated
migrations, and process entry points are not exemptions: they are excluded from
"production code" by the same declared policy the global gate uses.

Exit code 1 means the change is below target or touched an unmeasured file;
exit code 2 means the gate could not produce a trustworthy answer at all.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Running this file directly puts scripts/ on sys.path, not the repository root.
# The shared policy module has to import identically here and from the unittest
# suites, which address it as ``scripts.coverage_policy``.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.coverage_policy import (  # noqa: E402  -- deliberate, see the bootstrap above
    COVERAGE_ROOT,
    DIFF_COVERAGE_TARGET,
    PYPROJECT_PATH,
    REPO_ROOT,
    PolicyError,
    exemption_reason,
    is_omitted,
    load_coverage_report,
    load_measurement_config,
    normalise_path,
    rate,
    to_coverage_path,
    verify_measurement_policy,
    write_summary,
)

DEFAULT_COVERAGE_JSON = REPO_ROOT / COVERAGE_ROOT / "coverage.json"
DEFAULT_BASE_REF = "origin/main"

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@")
NEW_FILE_RE = re.compile(r"^\+\+\+ (?P<path>.+)$")


class FileVerdict:
    """Per-file outcome of the differential gate."""

    def __init__(self, path, status, detail="", executable=0, covered=0, uncovered_lines=()):
        self.path = path
        self.status = status  # measured | unmeasured | exempt | non-production
        self.detail = detail
        self.executable = executable
        self.covered = covered
        self.uncovered_lines = sorted(uncovered_lines)

    @property
    def rate(self):
        return rate(self.covered, self.executable)


def run_git(args, repo_root):
    """Run a git command, failing closed when git cannot answer."""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise PolicyError(f"cannot run git {' '.join(args)}: {exc}") from exc
    if completed.returncode != 0:
        raise PolicyError(f"git {' '.join(args)} failed: {completed.stderr.strip() or completed.stdout.strip()}")
    return completed.stdout


def resolve_diff_range(base_ref, head_ref, repo_root):
    """Return the merge base of ``base_ref`` and ``head_ref``.

    Comparing against the merge base (rather than the branch tip) attributes
    only this branch's own lines to the author, not everything that landed on
    the base branch meanwhile.
    """
    merge_base = run_git(["merge-base", base_ref, head_ref], repo_root).strip()
    if not merge_base:
        raise PolicyError(f"cannot determine a merge base between {base_ref} and {head_ref}")
    return merge_base


def collect_diff(base_ref, head_ref, repo_root):
    merge_base = resolve_diff_range(base_ref, head_ref, repo_root)
    return run_git(["diff", "--unified=0", "--no-color", "--diff-filter=d", merge_base, head_ref], repo_root)


def parse_changed_lines(diff_text):
    """Map each changed file to the set of line numbers it added or modified.

    Deleted files and pure deletions carry no new lines and therefore cannot be
    covered by anything; only lines present in the post-change file count.
    """
    changed = {}
    current_path = None
    lines = diff_text.splitlines()
    for index, line in enumerate(lines):
        # A "+++" line is a file header only where it follows the matching "---"
        # header. An added source line beginning with "++ " renders identically,
        # and treating one as a header would reattribute the rest of that file's
        # hunks to a path the gate then never checks -- a silent fail-open.
        if line.startswith("+++ ") and index and lines[index - 1].startswith("--- "):
            match = NEW_FILE_RE.match(line)
            raw_path = match.group("path") if match else "/dev/null"
            if raw_path == "/dev/null":
                current_path = None
            else:
                current_path = normalise_path(raw_path[2:] if raw_path.startswith("b/") else raw_path)
                changed.setdefault(current_path, set())
            continue
        if line.startswith("@@") and current_path is not None:
            match = HUNK_RE.match(line)
            if match is None:
                continue
            start = int(match.group("start"))
            count = int(match.group("count") or 1)
            changed[current_path].update(range(start, start + count))
    return {path: lines for path, lines in changed.items() if lines}


def _require_file_fields(path, entry):
    required = ("executed_lines", "missing_lines", "excluded_lines", "missing_branches")
    missing = [field for field in required if field not in entry]
    if missing:
        raise PolicyError(
            f"coverage entry {path!r} is missing {', '.join(missing)}; the differential gate needs "
            "per-line detail from a branch-measured JSON report"
        )


def evaluate_file(repo_path, changed_lines, report):
    """Classify one changed file and measure its changed lines."""
    reason = exemption_reason(repo_path)
    if reason is not None:
        return FileVerdict(repo_path, "exempt", reason)

    coverage_path = to_coverage_path(repo_path)
    if coverage_path is None:
        return FileVerdict(
            repo_path,
            "unmeasured",
            f"outside the measured tree ({COVERAGE_ROOT}/) and not covered by a documented exemption",
        )
    if is_omitted(coverage_path):
        return FileVerdict(repo_path, "non-production", "excluded by the declared measurement policy")

    entry = report.files.get(coverage_path)
    if entry is None:
        return FileVerdict(
            repo_path,
            "unmeasured",
            "production file absent from the coverage report; the run never measured it",
        )
    _require_file_fields(coverage_path, entry)

    executed = set(entry["executed_lines"])
    missing = set(entry["missing_lines"])
    partial_sources = {pair[0] for pair in entry["missing_branches"] if pair}
    executable = changed_lines & (executed | missing)
    covered = {line for line in executable if line in executed and line not in partial_sources}
    return FileVerdict(
        repo_path,
        "measured",
        executable=len(executable),
        covered=len(covered),
        uncovered_lines=executable - covered,
    )


def format_line_ranges(numbers):
    """Compress [1,2,3,7] to "1-3, 7" for readable output."""
    ranges = []
    for number in sorted(numbers):
        if ranges and number == ranges[-1][1] + 1:
            ranges[-1][1] = number
        else:
            ranges.append([number, number])
    return ", ".join(str(start) if start == end else f"{start}-{end}" for start, end in ranges)


def format_summary(verdicts, executable, covered, target, passed):
    measured = [verdict for verdict in verdicts if verdict.status == "measured"]
    unmeasured = [verdict for verdict in verdicts if verdict.status == "unmeasured"]
    exempt = [verdict for verdict in verdicts if verdict.status == "exempt"]
    achieved = rate(covered, executable)
    lines = [
        "### Differential coverage (changed production code)",
        "",
        f"**{achieved:.2f}%** of {executable} changed executable line(s) covered "
        f"— target {target:.2f}% — {'PASS' if passed else 'FAIL'}",
        "",
    ]
    if measured:
        lines.extend(["| File | Changed lines | Covered | Rate | Uncovered |", "| --- | ---: | ---: | ---: | --- |"])
        for verdict in sorted(measured, key=lambda item: (item.rate, item.path)):
            if verdict.executable == 0:
                continue
            uncovered = format_line_ranges(verdict.uncovered_lines) or "—"
            lines.append(
                f"| `{verdict.path}` | {verdict.executable} | {verdict.covered} | {verdict.rate:.1f}% | {uncovered} |"
            )
        lines.append("")
    if unmeasured:
        lines.append("**Unmeasured changed files (fail-closed):**")
        lines.extend(f"- `{verdict.path}` — {verdict.detail}" for verdict in unmeasured)
        lines.append("")
    if exempt:
        lines.append("**Exempt changed files (documented):**")
        lines.extend(f"- `{verdict.path}` — {verdict.detail}" for verdict in exempt)
        lines.append("")
    return "\n".join(lines).rstrip("\n")


def report_verdicts(verdicts, executable, covered, target, passed):
    unmeasured = [verdict for verdict in verdicts if verdict.status == "unmeasured"]
    below = sorted(
        (verdict for verdict in verdicts if verdict.status == "measured" and verdict.uncovered_lines),
        key=lambda item: item.path,
    )
    achieved = rate(covered, executable)

    if unmeasured:
        print("changed production file(s) the coverage run never measured:\n")
        for verdict in unmeasured:
            print(f"  {verdict.path}: {verdict.detail}")
        print(
            "\nThe gate fails closed here on purpose: an unmeasured file is an unknown, not a pass.\n"
            "Either bring the file under the pytest coverage run, or -- if it structurally cannot be\n"
            "measured there -- add it to DIFF_COVERAGE_EXEMPTIONS in scripts/coverage_policy.py with\n"
            "the reason and the gate that covers it instead.\n"
        )
    if not passed and executable:
        print(
            f"differential coverage {achieved:.2f}% is below the {target:.2f}% target "
            f"({covered}/{executable} changed executable line(s) covered):\n"
        )
        for verdict in below:
            print(f"  {verdict.path}: {format_line_ranges(verdict.uncovered_lines)}")
        print(
            "\nA changed line counts as covered only when it was executed and is not the origin of an\n"
            "untaken branch, so a half-tested conditional shows up here as uncovered."
        )


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--base-ref", default=DEFAULT_BASE_REF, help="Ref the change is measured against.")
    parser.add_argument("--head-ref", default="HEAD", help="Ref holding the change.")
    parser.add_argument(
        "--diff-file",
        type=Path,
        default=None,
        help="Read a unified diff from this file instead of invoking git.",
    )
    parser.add_argument("--coverage-json", type=Path, default=DEFAULT_COVERAGE_JSON)
    parser.add_argument("--pyproject", type=Path, default=PYPROJECT_PATH)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--target",
        type=float,
        default=DIFF_COVERAGE_TARGET,
        help=f"Percentage of changed executable lines that must be covered (policy default {DIFF_COVERAGE_TARGET}).",
    )
    parser.add_argument("--summary-file", type=Path, default=None)
    return parser.parse_args(argv)


def read_diff(args):
    """Obtain the change under test, from a file or from git."""
    if args.diff_file is not None:
        try:
            return args.diff_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise PolicyError(f"cannot read diff {args.diff_file}: {exc}") from exc
    return collect_diff(args.base_ref, args.head_ref, args.repo_root)


def report_pass(verdicts, python_changes, executable, achieved, target):
    """Print the passing result, naming every exemption that was applied."""
    exempt = [verdict for verdict in verdicts if verdict.status == "exempt"]
    if executable == 0:
        print(
            "differential coverage: no changed executable production line(s) to measure "
            f"({len(python_changes)} changed Python file(s), {len(exempt)} exempt)."
        )
    else:
        print(
            f"differential coverage: {achieved:.2f}% of {executable} changed executable "
            f"production line(s) covered (target {target:.2f}%)."
        )
    for verdict in exempt:
        print(f"  exempt: {verdict.path} -- {verdict.detail}")


def collect_verdicts(args):
    """Validate the inputs and classify every changed Python file."""
    verify_measurement_policy(load_measurement_config(args.pyproject))
    report = load_coverage_report(args.coverage_json)
    changed = parse_changed_lines(read_diff(args))
    python_changes = {path: lines for path, lines in changed.items() if path.endswith(".py")}
    verdicts = [evaluate_file(path, lines, report) for path, lines in sorted(python_changes.items())]
    return python_changes, verdicts


def main(argv=None):
    args = parse_args(argv)
    try:
        python_changes, verdicts = collect_verdicts(args)
    except PolicyError as exc:
        print(f"differential coverage gate failed: {exc}", file=sys.stderr)
        return 2

    executable = sum(verdict.executable for verdict in verdicts)
    covered = sum(verdict.covered for verdict in verdicts)
    unmeasured = [verdict for verdict in verdicts if verdict.status == "unmeasured"]
    achieved = rate(covered, executable)
    passed = not unmeasured and (executable == 0 or achieved >= args.target)

    try:
        write_summary(args.summary_file, format_summary(verdicts, executable, covered, args.target, passed))
    except PolicyError as exc:
        print(f"differential coverage gate failed: {exc}", file=sys.stderr)
        return 2

    if not passed:
        report_verdicts(verdicts, executable, covered, args.target, passed)
        return 1

    report_pass(verdicts, python_changes, executable, achieved, args.target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
