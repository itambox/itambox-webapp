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

from scripts.check_architecture import linked_documents

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
E2E_WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "e2e.yml"
PRE_COMMIT_PATH = REPO_ROOT / ".pre-commit-config.yaml"
MAKEFILE_PATH = REPO_ROOT / "Makefile"

STEP_START = "      - "
FIELD_INDENT = "        "
BLOCK_INDENT = "          "

# The gates that run after the lanes, and the script each one invokes.
POST_SUITE_GATES = {
    "Check the lane matrix gate": "scripts/check_xdist_matrix.py",
    "Certify the run and publish durations": "scripts/check_test_report.py",
    "Check the global coverage ratchet": "scripts/check_coverage_baseline.py",
    "Check differential coverage for changed production code": "scripts/check_diff_coverage.py",
}

# Both lanes must have succeeded: the gates read files the lanes write, and
# the combined coverage report exists only when both lanes ran.
LANES_SUCCEEDED = "steps.parallel.conclusion == 'success' && steps.serial.conclusion == 'success'"


class AccessibilityE2EWorkflowPolicyTests(unittest.TestCase):
    def test_accessibility_browser_contracts_run_before_merge(self):
        workflow = E2E_WORKFLOW_PATH.read_text(encoding="utf-8")

        self.assertRegex(workflow, r"(?m)^  pull_request:\n    branches: \[main\]$")
        self.assertIn("run: npm test", workflow)


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


def path_filters(workflow_text):
    """The ``on.pull_request.paths`` entries, unquoted, in file order."""
    filters = []
    inside = False
    for line in workflow_text.splitlines():
        stripped = line.strip()
        if stripped == "paths:":
            inside = True
            continue
        if not inside:
            continue
        if stripped.startswith("- "):
            filters.append(stripped[2:].strip().strip('"'))
            continue
        # A comment inside the sequence is not the end of it. Treating it as one
        # would silently shorten the filter list and pass every assertion below.
        if stripped and not stripped.startswith("#"):
            break
    return filters


def _filter_matcher(pattern):
    """Compile one path filter the way GitHub matches it.

    ``**`` crosses directory separators and ``*`` does not, so a filter naming
    ``itambox/docs/development/*.md`` would not cover a future subdirectory
    while ``**/*.md`` does. Translating rather than reaching for ``fnmatch``
    keeps that distinction, which is the whole point of the assertion below.
    """
    compiled = []
    for token in re.split(r"(\*\*/|\*\*|\*|\?)", pattern):
        compiled.append(
            {"**/": r"(?:[^/]+/)*", "**": r".*", "*": r"[^/]*", "?": r"[^/]"}.get(token) or re.escape(token)
        )
    return re.compile("".join(compiled) + r"\Z")


def triggers_ci(workflow_text, repository_path):
    return any(_filter_matcher(pattern).match(repository_path) for pattern in path_filters(workflow_text))


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
                self.assertIn(LANES_SUCCEEDED, condition)

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
            "steps.parallel.conclusion != 'skipped' || steps.serial.conclusion != 'skipped'",
            condition,
            "lanes that never ran wrote no report; uploading is an error, not a missing artifact",
        )

    def test_the_manifest_step_precedes_both_lanes(self):
        """The completeness manifest must be recorded before the lanes run."""
        names = [step.get("name") for step in self.steps if step.get("name")]
        collect = names.index("Collect the serial node IDs for the completeness manifest")
        self.assertLess(collect, names.index("Run the parallel lane"))
        self.assertLess(collect, names.index("Run the serial-only lane"))

    def test_the_lanes_use_the_marker_split(self):
        """The xdist marker selection is enforced in the workflow, not just conftest."""
        parallel = step_named(self.steps, "Run the parallel lane").get("run", "")
        serial = step_named(self.steps, "Run the serial-only lane").get("run", "")
        self.assertIn("-n auto", parallel)
        self.assertIn("-m 'not serial_only'", parallel)
        self.assertIn("-m serial_only", serial)
        self.assertNotIn("-n auto", serial)

    def test_the_matrix_gate_reads_both_lane_reports(self):
        """Disjointness can only be checked against both lane reports."""
        gate = step_named(self.steps, "Check the lane matrix gate")
        run = gate.get("run", "")
        self.assertIn("--xdist artifacts/junit-parallel.xml", run)
        self.assertIn("--serial artifacts/junit-serial-only.xml", run)


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

    def test_exception_policy_changes_trigger_ci_and_run_the_gate(self):
        self.assertIn('- "scripts/exception_baseline.json"', self.workflow_text)
        self.assertTrue(triggers_ci(self.workflow_text, "itambox/docs/development/exception-policy.md"))
        gate = step_named(load_steps(), "Check the exception policy gate")
        self.assertIn("scripts/check_exception_policy.py", gate.get("run", ""))

    def test_pre_commit_runs_the_same_exception_gate(self):
        config = PRE_COMMIT_PATH.read_text(encoding="utf-8")
        self.assertIn("id: exception-policy", config)
        self.assertIn("entry: python scripts/check_exception_policy.py", config)
        self.assertIn("language_version: python3.12", config)

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

    def test_architecture_policy_changes_trigger_ci_and_run_the_gate(self):
        self.assertIn('- "scripts/architecture_baseline.json"', self.workflow_text)
        for document in (
            "itambox/docs/development/architecture-policy.md",
            "itambox/docs/development/adr-0001-architecture-boundaries-and-layering.md",
            "itambox/docs/plugins/api_reference.md",
        ):
            with self.subTest(document=document):
                self.assertTrue(triggers_ci(self.workflow_text, document))
        gate = step_named(load_steps(), "Check the architecture boundary gate")
        self.assertIn("scripts/check_architecture.py", gate.get("run", ""))

    def test_the_architecture_gate_runs_after_the_local_import_gate(self):
        """The two import-shaped gates stay adjacent and in dependency order."""
        names = [step.get("name") for step in load_steps() if step.get("name")]

        self.assertLess(
            names.index("Check the local-import policy gate"),
            names.index("Check the architecture boundary gate"),
        )

    def test_the_architecture_gate_is_not_wired_in_report_only_mode(self):
        """`--report-only` always exits 0; wiring it would be a silent pass."""
        gate = step_named(load_steps(), "Check the architecture boundary gate")

        self.assertNotIn("--report-only", gate.get("run", ""))
        self.assertNotIn("--report-only", PRE_COMMIT_PATH.read_text(encoding="utf-8"))

    def test_pre_commit_runs_the_same_architecture_gate(self):
        config = PRE_COMMIT_PATH.read_text(encoding="utf-8")

        self.assertIn("id: architecture-policy", config)
        self.assertIn("entry: python scripts/check_architecture.py", config)
        self.assertIn("pass_filenames: false", config)
        self.assertIn("always_run: true", config)

    def test_the_makefile_exposes_the_architecture_targets(self):
        makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
        phony = next(line for line in makefile.splitlines() if line.startswith(".PHONY:"))

        for target in ("architecture-check", "architecture-baseline"):
            with self.subTest(target=target):
                self.assertIn(target, phony)
                self.assertIn(f"\n{target}:\n", makefile)
                self.assertIn(f"make {target}", makefile)

    def test_every_document_the_link_rule_reads_also_triggers_ci(self):
        """`R-DOC1` is pointless on a document whose change runs no CI at all.

        Derived from the gate's own document set rather than from a list
        repeated here: enumerating the inputs in two places is how five of the
        development documents came to be scanned by a rule that never ran on them.
        """
        for document in linked_documents(REPO_ROOT):
            relative = document.relative_to(REPO_ROOT).as_posix()
            with self.subTest(document=relative):
                self.assertTrue(
                    triggers_ci(self.workflow_text, relative),
                    f"{relative} is read by R-DOC1 but matches no on.pull_request.paths filter",
                )

    def test_a_development_document_that_does_not_exist_yet_is_already_covered(self):
        """The filter has to cover the rule's glob, not today's file listing."""
        for future in (
            "itambox/docs/development/adr-9999-not-yet-written.md",
            "itambox/docs/development/nested/deeper-note.md",
        ):
            with self.subTest(document=future):
                self.assertTrue(triggers_ci(self.workflow_text, future))

    def test_the_path_filter_reader_distinguishes_star_from_double_star(self):
        """Guards the assertions above: a matcher that ignores `**` proves nothing."""
        self.assertTrue(triggers_ci('paths:\n  - "a/**/*.md"\n', "a/b/c.md"))
        self.assertFalse(triggers_ci('paths:\n  - "a/*.md"\n', "a/b/c.md"))
        self.assertTrue(triggers_ci('paths:\n  - "a/*.md"\n', "a/c.md"))
        self.assertEqual(path_filters('paths:\n  - "a/*.md"\n  - "b.py"\n'), ["a/*.md", "b.py"])
        self.assertEqual(path_filters('paths:\n  # note\n  - "a.md"\nother:\n  - "b"\n'), ["a.md"])

    def test_the_new_docs_are_reachable_in_the_mkdocs_nav(self):
        navigation = (REPO_ROOT / "itambox" / "mkdocs.yml").read_text(encoding="utf-8")

        self.assertIn("'development/architecture-policy.md'", navigation)
        self.assertIn("'development/adr-0001-architecture-boundaries-and-layering.md'", navigation)
        self.assertIn("omitted_files: warn", navigation)

    def test_no_policy_text_implies_plugin_support_for_internal_modules(self):
        """Layer membership describes ITAMbox's structure; it promises nothing."""
        policy_doc = REPO_ROOT / "itambox" / "docs" / "development" / "architecture-policy.md"
        text = policy_doc.read_text(encoding="utf-8")

        self.assertIn("scans first-party code under `itambox/`", text)
        for layer in ("framework", "kernel", "platform-service"):
            for promise in ("public API", "supported API", "stable API"):
                with self.subTest(layer=layer, promise=promise):
                    self.assertNotIn(f"{layer} {promise}", text)

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


class TypingPolicyWiringTests(unittest.TestCase):
    """The typing gate must run the same way from CI, the Makefile, and pre-commit."""

    GATE = "scripts/check_typing_policy.py"
    STEP = "Check the static typing policy gate"

    def setUp(self):
        self.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.steps = load_steps()

    def test_ci_runs_the_gate_after_installing_dependencies_and_before_the_suite(self):
        """The plugin needs the dev environment; a failure must precede the slow work."""
        step = step_named(self.steps, self.STEP)
        self.assertIn(self.GATE, step.get("run", ""))

        names = [step.get("name") for step in self.steps if step.get("name")]
        self.assertLess(names.index("Install dependencies"), names.index(self.STEP))
        self.assertLess(
            names.index(self.STEP), names.index("Collect the serial node IDs for the completeness manifest")
        )
        self.assertLess(names.index(self.STEP), names.index("Apply migrations to a fresh database"))

    def test_ci_does_not_wire_the_gate_in_its_read_only_mode(self):
        """`--list` checks nothing and always exits 0; wiring it is a silent pass."""
        self.assertNotIn("--list", step_named(self.steps, self.STEP).get("run", ""))
        pre_commit = PRE_COMMIT_PATH.read_text(encoding="utf-8")
        pre_commit_lines = pre_commit.splitlines()
        hook_start = next(index for index, line in enumerate(pre_commit_lines) if line.strip() == "- id: typing-policy")
        hook_end = next(
            (
                index
                for index in range(hook_start + 1, len(pre_commit_lines))
                if re.match(r"^\s*-\s+id:", pre_commit_lines[index])
            ),
        )
        typing_hook = "\n".join(pre_commit_lines[hook_start:hook_end])
        self.assertNotIn("--list", typing_hook)
        self.assertNotIn(f"{self.GATE} --list", MAKEFILE_PATH.read_text(encoding="utf-8"))

    def test_the_record_and_the_policy_document_trigger_ci(self):
        self.assertIn('- "scripts/typing_checked_modules.json"', self.workflow_text)
        for path in (
            "scripts/typing_checked_modules.json",
            "scripts/check_typing_policy.py",
            "itambox/docs/development/typing-policy.md",
            "pyproject.toml",
            "uv.lock",
        ):
            with self.subTest(path=path):
                self.assertTrue(triggers_ci(self.workflow_text, path))

    def test_pre_commit_runs_the_same_gate_in_the_full_dev_environment(self):
        config = PRE_COMMIT_PATH.read_text(encoding="utf-8")

        self.assertIn("id: typing-policy", config)
        self.assertIn(f"entry: uv run --locked --group dev python {self.GATE}", config)
        self.assertNotIn(f"--only-group dev python {self.GATE}", config)

    def test_the_makefile_exposes_the_typecheck_target(self):
        makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
        phony = next(line for line in makefile.splitlines() if line.startswith(".PHONY:"))

        self.assertIn("typecheck", phony)
        self.assertIn("\ntypecheck:\n", makefile)
        self.assertIn("make typecheck", makefile)

    def test_the_typing_policy_document_is_reachable_in_the_mkdocs_nav(self):
        navigation = (REPO_ROOT / "itambox" / "mkdocs.yml").read_text(encoding="utf-8")

        self.assertIn("'development/typing-policy.md'", navigation)


def _flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


if __name__ == "__main__":
    unittest.main()
