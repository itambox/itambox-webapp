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
import collections
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

# Every file section of a git diff opens with this line, and nothing inside a
# hunk can produce one: with ``--unified=0`` every body line carries a ``+``,
# ``-`` or ``\`` prefix. It is the only trustworthy anchor in the format.
GIT_HEADER_PREFIX = "diff --git "

_PREAMBLE, _HEADER, _HUNK, _BINARY = "preamble", "header", "hunk", "binary"
_BODY_PREFIXES = ("+", "-", "\\")

# Everything git may emit between a ``diff --git`` line and the first hunk. The
# list is exhaustive on purpose: a header line the gate does not recognise is a
# section it does not understand, and a section it does not understand is one
# whose changed lines it would silently drop.
_HEADER_METADATA_PREFIXES = (
    "index ",
    "old mode ",
    "new mode ",
    "new file mode ",
    "deleted file mode ",
    "similarity index ",
    "dissimilarity index ",
    "rename from ",
    "copy from ",
    "--- ",
)

# The two shapes git uses when it will not describe a change line by line: a
# binary file, or a text file marked ``-diff`` in .gitattributes.
_BINARY_MARKERS = ("Binary files ", "GIT binary patch")

OPAQUE_REASON = (
    "changed without a line-level diff (binary content, or `-diff` in .gitattributes); "
    "the gate cannot tell which lines the change introduced, so it cannot certify them"
)

# The single-character escapes git emits in a quoted path; everything else is a
# three-digit octal byte.
_PATH_ESCAPES = {
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
    "\\": "\\",
    '"': '"',
}

# ``changed``: path -> post-change line numbers. ``opaque``: path -> why its
# changed lines could not be attributed. An opaque Python file is a change the
# gate must fail on, never one it may forget.
DiffChanges = collections.namedtuple("DiffChanges", "changed opaque")


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


def _hunk_line_numbers(line):
    """Post-change line numbers a hunk header claims, refusing an unreadable one."""
    match = HUNK_RE.match(line)
    if match is None:
        raise PolicyError(
            f"unparseable hunk header {line!r}; the gate cannot attribute the lines it "
            "introduces, and unattributed changed lines are unchecked changed lines"
        )
    start = int(match.group("start"))
    count = int(match.group("count") or 1)
    return range(start, start + count)


def _unquote_path(raw_path):
    """Decode git's C-style path quoting.

    ``core.quotePath`` is on by default, so any path with a non-ASCII or control
    character arrives wrapped in quotes with its bytes octal-escaped. Read
    literally, such a path ends in ``"`` rather than ``.py`` and drops straight
    out of the set of changed Python files -- the change disappears from the gate
    instead of failing it.
    """
    if len(raw_path) < 2 or not (raw_path.startswith('"') and raw_path.endswith('"')):
        return raw_path
    body = raw_path[1:-1]
    decoded = bytearray()
    index = 0
    while index < len(body):
        character = body[index]
        if character != "\\":
            decoded.extend(character.encode("utf-8"))
            index += 1
            continue
        escape = body[index + 1 : index + 2]
        if escape in _PATH_ESCAPES:
            decoded.extend(_PATH_ESCAPES[escape].encode("utf-8"))
            index += 2
            continue
        octal = body[index + 1 : index + 4]
        if len(octal) == 3 and all(digit in "01234567" for digit in octal):
            decoded.append(int(octal, 8))
            index += 4
            continue
        raise PolicyError(f"unreadable escape sequence in quoted diff path {raw_path!r}")
    try:
        return decoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyError(f"quoted diff path {raw_path!r} does not decode as UTF-8: {exc}") from exc


def _post_change_path(raw_path):
    """Repository-relative path a ``+++`` header names, or None for a deletion."""
    # For an unquoted path containing spaces, git terminates the `---`/`+++`
    # header with a literal tab. It is a separator, not part of the filename;
    # retaining it makes `path.endswith(".py")` false and silently drops the
    # changed Python file from the gate.
    raw_path = raw_path.removesuffix("\t")
    if raw_path == "/dev/null":
        return None
    unquoted = _unquote_path(raw_path)
    return normalise_path(unquoted[2:] if unquoted.startswith("b/") else unquoted)


def _strip_side_prefix(raw_path):
    """Drop the ``a/``/``b/`` side marker so the two sides can be compared."""
    unquoted = _unquote_path(raw_path)
    return unquoted[2:] if unquoted.startswith(("a/", "b/")) else unquoted


def _split_git_header_paths(remainder):
    """Split the two path tokens in a ``diff --git`` header without losing C escapes."""
    tokens = []
    index = 0
    while index < len(remainder):
        while index < len(remainder) and remainder[index] == " ":
            index += 1
        if index >= len(remainder):
            break
        start = index
        if remainder[index] == '"':
            index += 1
            escaped = False
            while index < len(remainder):
                character = remainder[index]
                if character == '"' and not escaped:
                    index += 1
                    break
                if character == "\\" and not escaped:
                    escaped = True
                else:
                    escaped = False
                index += 1
            else:
                raise PolicyError(f"unterminated quoted path in diff header {remainder!r}")
        else:
            while index < len(remainder) and remainder[index] != " ":
                index += 1
        tokens.append(remainder[start:index])
    if len(tokens) != 2:
        raise PolicyError(f"diff header must name exactly two paths, got {remainder!r}")
    return tokens


def _section_path(remainder):
    """Best-effort post-change path from the ``diff --git`` line itself.

    Only a binary section needs it: there is no ``+++`` header to read the path
    from, and the gate still has to name the file it is failing on. The two
    sides are written unseparated (``a/x b/x``), so the split is recovered by
    finding the point where both halves name the same file -- which is every
    case but a rename, and a rename states its destination outright in
    ``rename to``.
    """
    if remainder.startswith('"'):
        _left, right = _split_git_header_paths(remainder)
        return _post_change_path(right)

    # Git does not C-quote a path merely because it contains spaces. Recover the
    # split by finding the point where the a/ and b/ sides name the same path.
    # Renames do not have such a split; their later `rename to`/`copy to`/`+++`
    # metadata supplies the authoritative destination instead.
    tokens = remainder.split(" ")
    for index in range(1, len(tokens)):
        left, right = " ".join(tokens[:index]), " ".join(tokens[index:])
        if _strip_side_prefix(left) == _strip_side_prefix(right):
            return _post_change_path(right)
    return None


class _DiffReader:
    """State machine over a git diff, anchored on its ``diff --git`` lines.

    ``diff --git`` is the only line in the format that file content cannot
    forge: with ``--unified=0`` every hunk body line carries a ``+``, ``-`` or
    ``\\`` prefix, so a source line can render as ``--- a/x`` or ``+++ b/x`` but
    never as a bare ``diff --git`` header. Matching ``---``/``+++`` positionally
    instead -- even as a pair -- lets a change to a file that *contains* a diff
    reattribute every later hunk to a path the gate does not check.
    """

    def __init__(self):
        self.changed = {}
        self.opaque = {}
        self.state = _PREAMBLE
        # ``path`` is authoritative, read from the ``+++`` header. ``section``
        # is the best-effort path for a section that has no ``+++`` at all,
        # which is the binary case and only ever used to name the failure.
        self.path = None
        self.section = None
        self.saw_file_header = False

    def read(self, line):
        if line.startswith(GIT_HEADER_PREFIX):
            self.state, self.path, self.saw_file_header = _HEADER, None, False
            self.section = _section_path(line[len(GIT_HEADER_PREFIX) :])
        elif self.state == _BINARY:
            # The base85 payload of a `GIT binary patch` carries no line
            # information; the section is already recorded as opaque.
            pass
        elif self.state == _HUNK:
            self._read_body(line)
        elif self.state == _HEADER:
            self._read_header(line)
        elif line.startswith(("--- ", "+++ ", "@@")):
            raise PolicyError(
                f"diff content {line!r} outside any `diff --git` section; the gate only reads "
                "diffs anchored by git's own file headers, because an unanchored header can be "
                "forged by file content"
            )

    def _read_body(self, line):
        if line.startswith("@@"):
            self._open_hunk(line)
        elif not line.startswith(_BODY_PREFIXES):
            raise PolicyError(f"unexpected line in a diff hunk body: {line!r}")

    def _read_header(self, line):
        if line.startswith("+++ "):
            self._name_file(line[4:])
        elif line.startswith("@@"):
            if not self.saw_file_header:
                raise PolicyError(f"hunk header {line!r} before any file header")
            self.state = _HUNK
            self._open_hunk(line)
        elif line.startswith(_BINARY_MARKERS):
            self._mark_opaque(line)
        elif line.startswith(("rename to ", "copy to ")):
            self.section = _post_change_path(line.split(" ", 2)[2])
        elif not line.startswith(_HEADER_METADATA_PREFIXES):
            raise PolicyError(
                f"unrecognised line {line!r} in the header of a `diff --git` section; the gate "
                "refuses a section it cannot read rather than skipping the changed lines it may "
                "describe"
            )

    def _name_file(self, raw_path):
        self.saw_file_header = True
        self.path = _post_change_path(raw_path)
        if self.path is not None:
            self.section = self.path
            self.changed.setdefault(self.path, set())

    def _open_hunk(self, line):
        # A hunk under ``+++ /dev/null`` describes a deletion: it introduces no
        # post-change line, so there is nothing to attribute to anyone.
        if self.path is not None:
            self.changed[self.path].update(_hunk_line_numbers(line))

    def _mark_opaque(self, line):
        if self.section is None:
            raise PolicyError(f"binary diff section {line!r} names no post-change file")
        self.opaque[self.section] = OPAQUE_REASON
        self.state = _BINARY

    def result(self):
        return DiffChanges(
            changed={path: lines for path, lines in self.changed.items() if lines},
            opaque=dict(self.opaque),
        )


def parse_diff(diff_text):
    """Read a git diff into the changed lines of each file it touches.

    Returns a ``DiffChanges``: ``changed`` maps a path to the post-change line
    numbers it added or modified, and ``opaque`` maps a path whose content
    changed but whose lines cannot be attributed to the reason why.

    Anything the reader cannot understand is a ``PolicyError`` rather than a
    skipped line. Deleted files, pure deletions, pure renames and mode-only
    changes carry no new lines and are the only changes that legitimately
    contribute nothing. A section git describes without line detail at all -- a
    binary file, or a text file marked ``-diff`` in ``.gitattributes`` -- is
    recorded in ``opaque`` instead, so the caller fails on it rather than losing
    it.
    """
    reader = _DiffReader()
    for line in diff_text.splitlines():
        reader.read(line)
    return reader.result()


def _require_file_fields(path, entry):
    required = ("executed_lines", "missing_lines", "excluded_lines", "missing_branches")
    missing = [field for field in required if field not in entry]
    if missing:
        raise PolicyError(
            f"coverage entry {path!r} is missing {', '.join(missing)}; the differential gate needs "
            "per-line detail from a branch-measured JSON report"
        )


def evaluate_opaque_file(repo_path, reason):
    """Classify a changed file whose lines the diff never described.

    The same documented buckets apply -- an exempt or non-production path is no
    more interesting when it is binary -- but anything else is unmeasured, which
    is the fail-closed answer. Dropping it instead would let a ``.py`` file
    marked ``-diff`` in ``.gitattributes`` carry untested code past the gate
    without appearing anywhere in its output.
    """
    exempt = exemption_reason(repo_path)
    if exempt is not None:
        return FileVerdict(repo_path, "exempt", exempt)
    coverage_path = to_coverage_path(repo_path)
    if coverage_path is not None and is_omitted(coverage_path):
        return FileVerdict(repo_path, "non-production", "excluded by the declared measurement policy")
    return FileVerdict(repo_path, "unmeasured", reason)


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


def is_python(path):
    return path.endswith(".py")


def collect_verdicts(args):
    """Validate the inputs and classify every changed Python file."""
    verify_measurement_policy(load_measurement_config(args.pyproject))
    report = load_coverage_report(args.coverage_json)
    diff = parse_diff(read_diff(args))
    python_changes = {path: lines for path, lines in diff.changed.items() if is_python(path)}
    opaque_changes = {path: reason for path, reason in diff.opaque.items() if is_python(path)}
    verdicts = [evaluate_file(path, lines, report) for path, lines in sorted(python_changes.items())]
    verdicts.extend(evaluate_opaque_file(path, reason) for path, reason in sorted(opaque_changes.items()))
    return {**python_changes, **opaque_changes}, verdicts


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
