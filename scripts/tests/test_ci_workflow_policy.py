"""Policy checks over the CI workflow itself.

The quality gates are only as good as the workflow that runs them. Two failure
modes are invisible in a green tick and cannot be caught by testing the gate
scripts:

* **A skipped gate reads as a passed gate.** A step with no ``if:`` inherits
  ``success()``, so one failing step silently skips every step after it. The
  three post-suite gates answer independent questions -- did the suite run, did
  coverage slip, is the changed code tested -- and a run that regressed on two
  of them has to report both.
* **A hand-enumerated test list rots.** A new ``scripts/tests/test_*.py`` suite
  that nobody remembers to add to the workflow never runs, and a gate whose
  tests never run is not a gate.

The workflow is read as text rather than parsed as YAML on purpose: this suite
is stdlib-only, and CI runs it on the bare interpreter before any dependency is
installed, precisely so a broken gate is caught before the ~40 minute suite.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"

STEP_START = "      - "
FIELD_INDENT = "        "
BLOCK_INDENT = "          "

# The gates that run after the suite, and the script each one invokes.
POST_SUITE_GATES = {
    "Certify the run and publish durations": "scripts/check_test_report.py",
    "Check the global coverage ratchet": "scripts/check_coverage_baseline.py",
    "Check differential coverage for changed production code": "scripts/check_diff_coverage.py",
}

SUITE_SUCCEEDED = "steps.suite.conclusion == 'success'"


def _assign_field(step, body):
    """Record one ``key: value`` field; return the key when it opens a block scalar."""
    key, _, value = body.partition(":")
    key = key.strip()
    value = value.strip()
    if value in {"|", ">", "|-", ">-"}:
        step[key] = ""
        return key
    step[key] = value
    return None


def parse_steps(workflow_text):
    """Extract the steps of every job as ``{field: value}`` mappings.

    A deliberately small reader for the one shape this workflow uses: steps at
    six spaces, their fields at eight, block scalars at ten or more. Nested
    mappings (``with:``) are skipped -- no assertion here needs them.
    """
    steps = []
    current = None
    block_key = None
    for line in workflow_text.splitlines():
        if line.startswith(STEP_START) and not line.startswith(STEP_START + " "):
            current = {}
            steps.append(current)
            block_key = _assign_field(current, line[len(STEP_START) :])
            continue
        if current is None:
            continue
        if line.startswith(BLOCK_INDENT):
            if block_key is not None:
                current[block_key] = (current[block_key] + "\n" + line.strip()).strip()
            continue
        if line.startswith(FIELD_INDENT):
            block_key = _assign_field(current, line[len(FIELD_INDENT) :])
            continue
        if not line.strip():
            continue
        current = None
        block_key = None
    return steps


def load_steps():
    return parse_steps(WORKFLOW_PATH.read_text(encoding="utf-8"))


def step_named(steps, name):
    for step in steps:
        if step.get("name") == name:
            return step
    raise AssertionError(f"the CI workflow has no step named {name!r}")


class PostSuiteGateIndependenceTests(unittest.TestCase):
    """One failing gate must not skip the next and hide its finding."""

    def setUp(self):
        self.steps = load_steps()

    def test_every_post_suite_gate_runs_on_its_own_condition(self):
        for name, script in POST_SUITE_GATES.items():
            with self.subTest(gate=name):
                step = step_named(self.steps, name)
                self.assertIn(script, step.get("run", ""))
                condition = step.get("if", "")
                self.assertIn(
                    "always()",
                    condition,
                    "without always() this gate inherits success() and is skipped by any earlier failure",
                )
                self.assertIn(SUITE_SUCCEEDED, condition)

    def test_only_the_differential_gate_is_restricted_to_pull_requests(self):
        """On a push to a protected branch there is no "changed code" range."""
        for name in POST_SUITE_GATES:
            with self.subTest(gate=name):
                condition = step_named(self.steps, name).get("if", "")
                pull_request_only = "github.event_name == 'pull_request'" in condition
                self.assertEqual(pull_request_only, "check_diff_coverage.py" in step_named(self.steps, name)["run"])

    def test_artifacts_are_not_uploaded_when_the_suite_was_never_attempted(self):
        """`if-no-files-found: error` would turn a skipped suite into an upload failure."""
        step = step_named(self.steps, "Upload coverage and duration artifacts")
        condition = step.get("if", "")
        self.assertIn("always()", condition)
        self.assertIn(
            "steps.suite.conclusion != 'skipped'",
            condition,
            "a suite that never ran wrote no report; uploading is an error, not a missing artifact",
        )


class GateSuiteDiscoveryTests(unittest.TestCase):
    """A hand-written list of test modules is a list that goes stale silently."""

    def setUp(self):
        self.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")

    def test_no_ci_step_names_an_individual_gate_suite_module(self):
        named = sorted(set(re.findall(r"scripts\.tests\.\w+", self.workflow_text)))
        self.assertEqual(
            named,
            [],
            "the workflow enumerates individual suites; a new scripts/tests/test_*.py would "
            "then be silently excluded. Run them by discovery instead.",
        )

    def test_suite_baseline_changes_trigger_ci(self):
        self.assertIn('- "scripts/suite_baseline.json"', self.workflow_text)

    def test_openapi_artifacts_and_baseline_changes_trigger_ci(self):
        self.assertIn('- "itambox/schema.yaml"', self.workflow_text)
        self.assertIn('- "scripts/openapi_diagnostics_baseline.json"', self.workflow_text)

    def test_openapi_gate_uses_canonical_hash_seed_and_always_uploads_failure_artifacts(self):
        steps = load_steps()
        gate = step_named(steps, "Check deterministic OpenAPI schema and diagnostics baseline")
        self.assertEqual(gate.get("id"), "openapi")
        self.assertIn('PYTHONHASHSEED: "0"', self.workflow_text)
        self.assertIn('PYTHONPATH: ""', self.workflow_text)
        self.assertIn("scripts/check_openapi_schema.py", gate.get("run", ""))
        self.assertIn("artifacts/openapi/schema.generated.yaml", gate.get("run", ""))
        self.assertNotIn(".artifacts/openapi", self.workflow_text)

        upload = step_named(steps, "Upload OpenAPI generation artifacts")
        condition = upload.get("if", "")
        self.assertIn("always()", condition)
        self.assertIn("steps.openapi.conclusion != 'skipped'", condition)

    def test_the_discovery_invocation_reaches_every_suite_on_disk(self):
        """The arguments CI passes must actually load every suite that exists."""
        step = step_named(load_steps(), "Check the repository gate suites")
        command = " ".join(step.get("run", "").split())
        match = re.search(
            r"python -m unittest discover --start-directory (?P<start>\S+) "
            r"--top-level-directory (?P<top>\S+) --pattern '(?P<pattern>\S+)'",
            command,
        )
        self.assertIsNotNone(match, f"expected a unittest discovery invocation, got {command!r}")

        start_dir = REPO_ROOT / match.group("start")
        loader = unittest.TestLoader()
        discovered = {
            test.__class__.__module__
            for suite in loader.discover(
                start_dir=str(start_dir),
                top_level_dir=str(REPO_ROOT / match.group("top")),
                pattern=match.group("pattern"),
            )
            for test in _flatten(suite)
        }
        on_disk = {f"scripts.tests.{path.stem}" for path in sorted(start_dir.glob("test_*.py"))}

        self.assertEqual(loader.errors, [], "a suite failed to import under CI's discovery arguments")
        self.assertEqual(discovered, on_disk)


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


if __name__ == "__main__":
    unittest.main()
