"""Command-line, baseline, and ratchet tests for the architecture gate.

The gate has three exit codes and they mean different things: 0 is a clean
graph, 1 is a policy regression a contributor fixes, and 2 is a result nobody
should trust -- a malformed baseline, a drifted fingerprint, an unclassifiable
module, a source file that will not parse. Most of what follows pins the
boundary between 1 and 2, because a gate that reports "untrustworthy" as
"violation" trains people to baseline their way out of a broken policy.
"""

import io
import json
import sys
import tempfile
import textwrap
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from scripts.architecture_policy import (
    AREA_LABELS,
    CANONICAL_PYTHON,
    SCHEMA_VERSION,
    compute_policy_fingerprint,
)
from scripts.check_architecture import (
    BASELINE_SECTIONS,
    DEBT_DISPOSITION,
    ISSUE_STATES_SCHEMA_VERSION,
    REPORT_ONLY_BANNER,
    PolicyError,
    load_baseline,
    main,
    refresh_issue_states,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# A small but complete tree: one module per layer the tests need, wired the way
# the real repository wires them.
CLEAN_TREE = {
    "itambox/core/models.py": "",
    "itambox/core/tasks/context.py": "from core.models import BaseModel\n",
    "itambox/assets/models/asset.py": "from core.models import BaseModel\n",
    "itambox/assets/services.py": "from assets.models.asset import Asset\n",
    "itambox/assets/forms/asset_form.py": "from assets.models.asset import Asset\n",
}


def write(root, relative, body=""):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return path


class GateTestCase(unittest.TestCase):
    """Runs ``main()`` against a throw-away tree and captures both streams."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.baseline = self.root / "scripts" / "architecture_baseline.json"
        self.baseline.parent.mkdir(parents=True, exist_ok=True)
        self.issue_states = self.root / "scripts" / "architecture_issue_states.json"
        # Every hand-built debt fixture below references issue 100; record it as
        # open so the liveness check starts from pass, exactly like the checked-in
        # repository snapshot does for the real baseline.
        self.write_issue_states({100: "open"})
        self.write_tree(CLEAN_TREE)

    def write_tree(self, files):
        for relative, body in files.items():
            write(self.root, relative, body)

    def run_gate(self, *extra):
        return self.run_main(
            "--cwd",
            str(self.root),
            "--baseline",
            str(self.baseline),
            "--issue-states",
            str(self.issue_states),
            *extra,
        )

    def run_main(self, *arguments):
        """``main()`` with the argument vector spelled out, both streams captured."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(arguments))
        return code, out.getvalue(), err.getvalue()

    def triage(self, notes="keystone edge: assets.depreciation -> assets.services"):
        """Fill in every scaffolded row by hand, the way the docs describe."""
        document = self.load()
        for section in BASELINE_SECTIONS:
            for row in document[section]:
                row["removal_issue"] = 100
                row["disposition"] = DEBT_DISPOSITION
                row["removal_direction"] = TRIAGED_DIRECTION
                if "accepted_reason" in row:
                    row["accepted_reason"] = "Pre-existing when the boundary gate was introduced."
                if "notes" in row:
                    row["notes"] = notes
        self.baseline.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
        return document

    def write_issue_states(self, states):
        """Write the reviewed issue-state snapshot a debt baseline depends on."""
        document = {
            "schema_version": ISSUE_STATES_SCHEMA_VERSION,
            "refreshed_at": "2026-01-01T00:00:00+00:00",
            "issues": {str(number): state for number, state in sorted(states.items())},
        }
        self.issue_states.parent.mkdir(parents=True, exist_ok=True)
        self.issue_states.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")

    def empty_baseline(self):
        self.write_baseline({section: [] for section in BASELINE_SECTIONS})

    def scaffold_sections(self, extra_stale=False):
        """A reviewed, triaged layer-exception row plus an optional stale sibling."""
        rows = [
            {
                "id": "R-S1|core.tasks.context|assets.models.asset|module-top",
                "rule": "R-S1",
                "source": "core.tasks.context",
                "source_layer": "platform-service",
                "target": "assets.models.asset",
                "target_layer": "domain-model",
                "kind": "module-top",
                "count": 1,
                "owner": "area:operations",
                "removal_issue": 100,
                "disposition": DEBT_DISPOSITION,
                "removal_direction": TRIAGED_DIRECTION,
                "accepted_reason": "Pre-existing when the boundary gate was introduced.",
            }
        ]
        if extra_stale:
            rows.append(
                {
                    "id": "R-S3|core.tasks.context|assets.services|function-body",
                    "rule": "R-S3",
                    "source": "core.tasks.context",
                    "source_layer": "platform-service",
                    "target": "assets.services",
                    "target_layer": "domain-service",
                    "kind": "function-body",
                    "count": 1,
                    "owner": "area:operations",
                    "removal_issue": 100,
                    "disposition": DEBT_DISPOSITION,
                    "removal_direction": TRIAGED_DIRECTION,
                    "accepted_reason": "Pre-existing when the boundary gate was introduced.",
                }
            )
            rows[-1]["rule"] = "R-S3"
            rows[-1]["id"] = "R-S3|core.tasks.context|assets.services|function-body"
            rows.sort(key=lambda row: row["id"])
        sections = {section: [] for section in BASELINE_SECTIONS}
        sections["layer_exceptions"] = rows
        return sections

    def write_baseline(self, sections, **header):
        document = {
            "schema_version": SCHEMA_VERSION,
            "canonical_python": f"{CANONICAL_PYTHON[0]}.{CANONICAL_PYTHON[1]}",
            "policy_sha256": compute_policy_fingerprint(("itambox",)),
        }
        document.update(sections)
        document.update(header)
        self.baseline.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")

    def load(self):
        return json.loads(self.baseline.read_text(encoding="utf-8"))


class CleanRunTests(GateTestCase):
    def test_a_clean_tree_against_an_empty_baseline_exits_zero(self):
        self.empty_baseline()

        code, out, err = self.run_gate()

        self.assertEqual(code, 0, err)
        self.assertIn("architecture:", out)
        self.assertIn("module-top", out)

    def test_a_missing_baseline_without_write_is_untrustworthy(self):
        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("architecture gate failed", err)

    def test_json_output_is_sorted_and_free_of_backslash_paths(self):
        self.empty_baseline()

        code, out, _err = self.run_gate("--format", "json")

        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertNotIn("\\", out)
        self.assertEqual(sorted(payload), list(payload))
        self.assertIn("census", payload)
        self.assertIn("dynamic_imports", payload)
        self.assertIn("typing_only", payload)

    def test_json_mode_emits_only_json_on_stdout_when_the_tree_is_failing(self):
        """A caller piping stdout into a parser must never get prose appended."""
        self.empty_baseline()
        self.write_tree({"itambox/core/tasks/context.py": "from assets.models.asset import Asset\n"})

        code, out, err = self.run_gate("--format", "json")

        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertTrue(payload["layer_exceptions"])
        self.assertNotIn("architecture policy:", out)
        self.assertIn("new forbidden cross-layer import", err)

    def test_report_only_json_without_a_baseline_is_still_one_json_document(self):
        """The inventory mode that has no baseline is where prose used to leak."""
        self.write_tree({"itambox/core/tasks/context.py": "from assets.models.asset import Asset\n"})

        code, out, err = self.run_gate("--report-only", "--format", "json")

        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["layer_exceptions"])
        self.assertEqual(err.count(REPORT_ONLY_BANNER), 2)
        self.assertNotIn(REPORT_ONLY_BANNER, out)
        self.assertNotIn("architecture policy:", out)
        self.assertIn("new forbidden cross-layer import", err)

    def test_json_mode_stays_parseable_when_the_baseline_is_unusable(self):
        self.write_tree({"itambox/core/tasks/context.py": "from assets.models.asset import Asset\n"})
        self.write_baseline({section: [] for section in BASELINE_SECTIONS}, policy_sha256="0" * 64)

        code, out, err = self.run_gate("--format", "json")

        self.assertEqual(code, 2)
        json.loads(out)
        self.assertIn("policy_sha256", err)

    def test_two_runs_of_the_same_tree_are_byte_identical(self):
        self.empty_baseline()

        first = self.run_gate("--format", "json")[1]
        second = self.run_gate("--format", "json")[1]

        self.assertEqual(first, second)

    def test_explain_prints_a_path_in_each_graph(self):
        self.empty_baseline()

        code, out, _err = self.run_gate("--explain", "assets.services", "core.models")

        self.assertEqual(code, 0)
        self.assertIn("assets.services -> assets.models.asset -> core.models", out)

    def test_explain_in_json_mode_is_still_one_json_document(self):
        """``--format json`` promises stdout to a parser; no mode may opt out."""
        self.empty_baseline()

        code, out, _err = self.run_gate("--format", "json", "--explain", "assets.services", "core.models")

        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["explain"]["source"], "assets.services")
        self.assertEqual(payload["explain"]["target"], "core.models")
        self.assertEqual(
            payload["explain"]["paths"]["module-top"],
            ["assets.services", "assets.models.asset", "core.models"],
        )

    def test_explain_reports_an_absent_path_as_null_in_json(self):
        self.empty_baseline()

        code, out, _err = self.run_gate("--format", "json", "--explain", "core.models", "assets.services")

        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(out)["explain"]["paths"]["effective"])


class UntrustworthyInvocationTests(GateTestCase):
    """A run that measured nothing must never report a clean graph."""

    def test_a_target_directory_that_is_absent_is_exit_two(self):
        self.empty_baseline()

        code, _out, err = self.run_gate("itambox/nowhere")

        self.assertEqual(code, 2)
        self.assertIn("architecture gate failed", err)

    def test_a_wrong_cwd_does_not_rewrite_a_real_baseline(self):
        """Otherwise one mistyped path turns the whole baseline into an empty file."""
        self.empty_baseline()
        before = self.baseline.read_bytes()

        code, _out, err = self.run_main(
            "--cwd",
            str(self.root / "does-not-exist"),
            "--baseline",
            str(self.baseline),
            "--write-baseline",
        )

        self.assertEqual(code, 2)
        self.assertIn("architecture gate failed", err)
        self.assertEqual(self.baseline.read_bytes(), before)

    def test_a_baseline_path_that_cannot_be_written_is_exit_two(self):
        """A regular file standing in for a directory fails the same way on
        every platform, and it must arrive as a diagnostic, not a traceback."""
        blocker = self.root / "scripts" / "not-a-directory"
        blocker.write_text("regular file\n", encoding="utf-8")

        code, _out, err = self.run_main(
            "--cwd",
            str(self.root),
            "--baseline",
            str(blocker / "architecture_baseline.json"),
            "--write-baseline",
        )

        self.assertEqual(code, 2)
        self.assertIn("cannot write baseline", err)
        self.assertNotIn("Traceback", err)


class ForbiddenEdgeTests(GateTestCase):
    def seed_platform_service_violation(self):
        self.write_tree({"itambox/core/tasks/context.py": "from assets.models.asset import Asset\n"})

    def test_a_new_forbidden_edge_is_a_regression(self):
        self.empty_baseline()
        self.seed_platform_service_violation()

        code, out, _err = self.run_gate()

        self.assertEqual(code, 1)
        self.assertIn("R-S1", out)
        self.assertIn("core.tasks.context", out)
        self.assertIn("itambox/core/tasks/context.py:1", out)

    def test_write_baseline_refuses_to_absorb_a_newly_observed_identity(self):
        self.empty_baseline()
        before = self.baseline.read_bytes()
        self.seed_platform_service_violation()

        code, out, _err = self.run_gate("--write-baseline")

        self.assertEqual(code, 1)
        self.assertEqual(self.baseline.read_bytes(), before)
        self.assertIn("hand-review", out)

    def test_a_recorded_identity_passes_and_a_count_increase_does_not(self):
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections())

        self.assertEqual(self.run_gate()[0], 0)

        self.write_tree(
            {
                "itambox/core/tasks/context.py": (
                    "from assets.models.asset import Asset\n\n\ndef later():\n    from assets.models.asset import Other\n"
                )
            }
        )
        code, out, _err = self.run_gate()

        self.assertEqual(code, 1)
        self.assertIn("R-S1", out)

    def test_a_paid_down_identity_makes_the_baseline_stale(self):
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections())
        self.write_tree({"itambox/core/tasks/context.py": "from core.models import BaseModel\n"})

        code, out, _err = self.run_gate()

        self.assertEqual(code, 1)
        self.assertIn("stale", out)
        self.assertIn("--write-baseline", out)

    def test_write_baseline_drops_stale_rows_and_keeps_human_metadata(self):
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections(extra_stale=True))

        code, _out, _err = self.run_gate("--write-baseline")

        self.assertEqual(code, 0)
        rows = self.load()["layer_exceptions"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["removal_direction"], TRIAGED_DIRECTION)
        self.assertEqual(rows[0]["removal_issue"], 100)


TRIAGED_DIRECTION = "Move the tenant lookup behind a service boundary owned by the domain application."


class AbsoluteRuleTests(GateTestCase):
    """``R-M1`` has no baseline representation at any severity."""

    def seed_model_to_presentation(self):
        self.write_tree(
            {"itambox/assets/models/asset.py": "from assets.forms.asset_form import AssetForm\n"},
        )

    def test_a_model_importing_presentation_fails_with_no_escape(self):
        self.empty_baseline()
        self.seed_model_to_presentation()

        code, out, _err = self.run_gate()

        self.assertEqual(code, 1)
        self.assertIn("R-M1", out)
        self.assertIn("no exception is permitted", out)

    def test_write_baseline_leaves_the_file_untouched(self):
        self.empty_baseline()
        before = self.baseline.read_bytes()
        self.seed_model_to_presentation()

        code, _out, _err = self.run_gate("--write-baseline")

        self.assertEqual(code, 1)
        self.assertEqual(self.baseline.read_bytes(), before)

    def test_bootstrap_does_not_launder_it_either(self):
        self.seed_model_to_presentation()

        code, _out, _err = self.run_gate("--write-baseline")

        self.assertEqual(code, 1)
        recorded = self.load()["layer_exceptions"] if self.baseline.exists() else []
        self.assertEqual([row for row in recorded if row["rule"] == "R-M1"], [])

    def test_a_hand_written_row_is_rejected_before_any_scan_result_matters(self):
        sections = {section: [] for section in BASELINE_SECTIONS}
        sections["layer_exceptions"] = [
            {
                "id": "R-M1|assets.models.asset|assets.forms.asset_form|module-top",
                "rule": "R-M1",
                "source": "assets.models.asset",
                "source_layer": "domain-model",
                "target": "assets.forms.asset_form",
                "target_layer": "presentation",
                "kind": "module-top",
                "count": 1,
                "owner": "area:assets",
                "removal_issue": 100,
                "disposition": DEBT_DISPOSITION,
                "removal_direction": TRIAGED_DIRECTION,
                "accepted_reason": "recorded",
            }
        ]
        self.write_baseline(sections)

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("R-M1", err)

    def test_a_shim_typing_guard_cannot_hide_it(self):
        """``TYPE_CHECKING`` on something that is not ``typing`` is not the guard."""
        self.empty_baseline()
        self.write_tree(
            {
                "itambox/assets/models/asset.py": """
                import compat

                if compat.TYPE_CHECKING:
                    from assets.forms.asset_form import AssetForm
                """
            }
        )

        code, out, _err = self.run_gate()

        self.assertEqual(code, 1)
        self.assertIn("R-M1", out)

    def test_deferring_the_import_into_a_function_does_not_downgrade_it(self):
        self.empty_baseline()
        self.write_tree(
            {
                "itambox/assets/models/asset.py": (
                    "def render():\n    from assets.forms.asset_form import AssetForm\n\n    return AssetForm\n"
                )
            }
        )

        code, out, _err = self.run_gate()

        self.assertEqual(code, 1)
        self.assertIn("R-M1", out)
        self.assertIn("function-body", out)


class CycleTests(GateTestCase):
    """Two domain services, so a cycle finding is never masked by a matrix rule."""

    def setUp(self):
        super().setUp()
        self.write_tree({"itambox/assets/depreciation.py": "", "itambox/assets/scanning.py": ""})

    def seed_module_top_cycle(self):
        self.write_tree(
            {
                "itambox/assets/services.py": "from assets.depreciation import schedule\n",
                "itambox/assets/depreciation.py": "from assets.services import build\n",
            }
        )

    def test_a_new_module_top_cycle_fails(self):
        self.empty_baseline()
        self.seed_module_top_cycle()

        code, out, _err = self.run_gate()

        self.assertEqual(code, 1)
        self.assertIn("R-C1", out)
        self.assertIn("assets.depreciation -> assets.services", out)

    def test_deferring_one_leg_still_fails_as_an_effective_cycle(self):
        self.empty_baseline()
        self.write_tree(
            {
                "itambox/assets/services.py": "from assets.depreciation import schedule\n",
                "itambox/assets/depreciation.py": "def schedule():\n    from assets.services import build\n\n    return build\n",
            }
        )

        code, out, _err = self.run_gate()

        self.assertEqual(code, 1)
        self.assertIn("R-CE1", out)

    def test_a_type_checking_back_edge_is_not_a_cycle(self):
        self.empty_baseline()
        self.write_tree(
            {
                "itambox/assets/services.py": "from assets.depreciation import schedule\n",
                "itambox/assets/depreciation.py": (
                    "from typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    from assets.services import build\n"
                ),
            }
        )

        code, out, err = self.run_gate()

        self.assertEqual(code, 0, out + err)

    def test_a_module_top_component_may_not_be_recorded_as_effective(self):
        self.seed_module_top_cycle()
        sections = {section: [] for section in BASELINE_SECTIONS}
        sections["cycles"] = [self.cycle_row(graph="effective")]
        self.write_baseline(sections)

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("module-top", err)

    def test_growing_a_recorded_cycle_is_one_regression_and_one_stale_entry(self):
        self.seed_module_top_cycle()
        sections = {section: [] for section in BASELINE_SECTIONS}
        sections["cycles"] = [self.cycle_row()]
        self.write_baseline(sections)
        code, out, err = self.run_gate()
        self.assertEqual(code, 0, out + err)

        self.write_tree(
            {
                "itambox/assets/depreciation.py": "from assets.scanning import resolve\n",
                "itambox/assets/scanning.py": "from assets.services import build\n",
            }
        )
        code, out, _err = self.run_gate()

        self.assertEqual(code, 1)
        self.assertIn("stale", out)
        self.assertIn("R-C1", out)

    def cycle_row(self, graph="module-top"):
        modules = ["assets.depreciation", "assets.services"]
        return {
            "id": "|".join([graph, *modules]),
            "graph": graph,
            "modules": modules,
            "edges": [
                {"source": "assets.depreciation", "target": "assets.services", "kind": "module-top"},
                {"source": "assets.services", "target": "assets.depreciation", "kind": "module-top"},
            ],
            "owner": "area:assets",
            "removal_issue": 100,
            "disposition": DEBT_DISPOSITION,
            "removal_direction": TRIAGED_DIRECTION,
            "accepted_reason": "Pre-existing when the boundary gate was introduced.",
        }


class CycleClaimTests(GateTestCase):
    """``assets.depreciation`` and ``assets.scanning`` are both domain services,
    so these fixtures exercise ``R-C3`` without also tripping a matrix rule."""

    ANNOTATED = """
        def build():
            # inline import: cycle: assets.services <-> assets.depreciation at load time
            from assets.depreciation import schedule

            return schedule
        """

    # The second statement carries no comment of its own, so it inherits the
    # group's ``cycle`` category and the claim identity stays anchored on the
    # first statement. That is exactly why the target set has to ratchet.
    GROUP_OF_TWO = """
        def build():
            # inline import: cycle: assets.services <-> assets.depreciation at load time
            from assets.depreciation import schedule
            from assets.scanning import resolve

            return schedule, resolve
        """

    def setUp(self):
        super().setUp()
        self.write_tree({"itambox/assets/depreciation.py": "", "itambox/assets/scanning.py": ""})

    def test_a_claim_the_graph_does_not_support_fails(self):
        self.empty_baseline()
        self.write_tree({"itambox/assets/services.py": self.ANNOTATED})

        code, out, _err = self.run_gate()

        self.assertEqual(code, 1)
        self.assertIn("R-C3", out)
        self.assertIn("itambox/assets/services.py", out)
        self.assertIn("assets.depreciation", out)

    def test_a_claim_supported_by_the_effective_graph_passes(self):
        self.write_tree(
            {
                "itambox/assets/services.py": self.ANNOTATED,
                "itambox/assets/depreciation.py": "from assets.services import build\n",
            }
        )
        sections = {section: [] for section in BASELINE_SECTIONS}
        sections["cycles"] = [
            {
                "id": "effective|assets.depreciation|assets.services",
                "graph": "effective",
                "modules": ["assets.depreciation", "assets.services"],
                "edges": [
                    {"source": "assets.depreciation", "target": "assets.services", "kind": "module-top"},
                    {"source": "assets.services", "target": "assets.depreciation", "kind": "function-body"},
                ],
                "owner": "area:assets",
                "removal_issue": 100,
                "disposition": DEBT_DISPOSITION,
                "removal_direction": TRIAGED_DIRECTION,
                "accepted_reason": "Pre-existing when the boundary gate was introduced.",
            }
        ]
        self.write_baseline(sections)

        code, out, err = self.run_gate()

        self.assertEqual(code, 0, out + err)

    def test_a_supported_claim_whose_component_is_unrecorded_is_r_c2(self):
        self.empty_baseline()
        self.write_tree(
            {
                "itambox/assets/services.py": self.ANNOTATED,
                "itambox/assets/depreciation.py": "from assets.services import build\n",
            }
        )

        code, out, _err = self.run_gate()

        self.assertEqual(code, 1)
        self.assertIn("R-C2", out)

    def test_one_comment_covering_a_group_is_one_finding(self):
        self.empty_baseline()
        self.write_tree(
            {
                "itambox/assets/services.py": """
                def build():
                    # inline imports: cycle: assets.services <-> its sibling services at load time
                    from assets.depreciation import schedule
                    from assets.scanning import resolve

                    return schedule, resolve
                """
            }
        )

        code, out, _err = self.run_gate()

        self.assertEqual(code, 1)
        self.assertEqual(out.count("R-C3 "), 1)
        self.assertIn("assets.depreciation, assets.scanning", out)

    def test_other_annotation_categories_are_ignored(self):
        self.empty_baseline()
        self.write_tree(
            {
                "itambox/assets/services.py": """
                def build():
                    # inline import: app-registry: avoid AppRegistryNotReady at import time
                    from assets.depreciation import schedule

                    return schedule
                """
            }
        )

        code, out, err = self.run_gate()

        self.assertEqual(code, 0, out + err)

    def test_a_recorded_claim_is_owned_by_its_source_module(self):
        self.write_tree({"itambox/assets/services.py": self.ANNOTATED})

        self.run_gate("--write-baseline")

        row = self.load()["unsupported_cycle_claims"][0]
        self.assertEqual(row["source"], "assets.services")
        self.assertEqual(row["path"], "itambox/assets/services.py")
        self.assertEqual(row["owner"], "area:assets")

    def test_a_recorded_source_that_disagrees_with_its_path_is_untrustworthy(self):
        """Otherwise the owner is derived from provenance nobody checked."""
        self.write_tree({"itambox/assets/services.py": self.ANNOTATED})
        self.run_gate("--write-baseline")
        self.triage()
        document = self.load()
        row = document["unsupported_cycle_claims"][0]
        row["source"] = "core.auth.guards"
        row["owner"] = "area:auth-rbac"
        self.baseline.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("records source", err)
        self.assertIn("assets.services", err)

    def test_a_recorded_claim_lists_only_its_unsupported_targets(self):
        """The row has to record what the finding is, not what the group resolves to.

        ``assets.depreciation`` imports back, so that half of the claim is true;
        ``assets.scanning`` does not. Recording both would make the row disagree
        with the diagnostic and with ``--format json``, and would ratchet against
        a set the gate never reports.
        """
        self.write_tree(
            {
                "itambox/assets/services.py": self.GROUP_OF_TWO,
                "itambox/assets/depreciation.py": "from assets.services import build\n",
            }
        )

        self.run_gate("--write-baseline")

        self.assertEqual(self.load()["unsupported_cycle_claims"][0]["targets"], ["assets.scanning"])

    def test_a_malformed_annotation_is_left_to_the_local_import_gate(self):
        self.empty_baseline()
        self.write_tree(
            {
                "itambox/assets/services.py": """
                def build():
                    # inline import: cycle:
                    from assets.depreciation import schedule

                    return schedule
                """
            }
        )

        code, out, _err = self.run_gate()

        self.assertEqual(code, 0, out)
        self.assertNotIn("R-C3", out)


class CycleClaimTargetRatchetTests(GateTestCase):
    """A recorded claim freezes a *target set*, not merely its own existence.

    The claim identity is ``(path, scope, statement)`` anchored on the first
    statement of an annotation group, and ``check_local_imports`` lets the rest
    of a contiguous group inherit that one comment. So a second import written
    directly underneath an already-recorded claim changes neither gate's
    identity set. Without a set ratchet on the targets, that is a new deferred
    coupling with a false ``cycle`` justification that no gate reports.
    """

    ONE = CycleClaimTests.ANNOTATED
    TWO = CycleClaimTests.GROUP_OF_TWO

    def setUp(self):
        super().setUp()
        self.write_tree({"itambox/assets/depreciation.py": "", "itambox/assets/scanning.py": ""})
        self.write_tree({"itambox/assets/services.py": self.ONE})
        self.run_gate("--write-baseline")
        self.triage()
        self.recorded_id = self.load()["unsupported_cycle_claims"][0]["id"]

    def test_the_starting_point_is_clean(self):
        code, out, err = self.run_gate()

        self.assertEqual(code, 0, out + err)

    def test_an_extra_import_under_a_recorded_comment_is_a_regression(self):
        self.write_tree({"itambox/assets/services.py": self.TWO})

        code, out, _err = self.run_gate()

        self.assertEqual(code, 1)
        self.assertIn("R-C3", out)
        self.assertIn("assets.scanning", out)

    def test_the_identity_really_does_not_move(self):
        """Pins the premise: the ratchet is needed because the id is stable."""
        self.write_tree({"itambox/assets/services.py": self.TWO})

        _code, out, _err = self.run_gate("--format", "json")

        observed = json.loads(out)["unsupported_cycle_claims"]
        self.assertEqual([row["id"] for row in observed], [self.recorded_id])
        self.assertEqual(observed[0]["targets"], ["assets.depreciation", "assets.scanning"])

    def test_write_baseline_refuses_the_added_target(self):
        self.write_tree({"itambox/assets/services.py": self.TWO})
        before = self.baseline.read_bytes()

        code, out, _err = self.run_gate("--write-baseline")

        self.assertEqual(code, 1)
        self.assertIn("refuses newly observed debt", out)
        self.assertIn("assets.scanning", out)
        self.assertEqual(self.baseline.read_bytes(), before)

    def test_a_paid_down_target_makes_the_baseline_stale(self):
        self.write_tree({"itambox/assets/services.py": self.TWO})
        self.run_gate("--write-baseline")
        self.hand_record_both_targets()
        self.write_tree({"itambox/assets/services.py": self.ONE})

        code, out, _err = self.run_gate()

        self.assertEqual(code, 1)
        self.assertIn("stale", out)
        self.assertIn("assets.scanning", out)

    def test_write_baseline_normalises_a_paid_down_target_and_keeps_metadata(self):
        self.hand_record_both_targets()
        reason = self.load()["unsupported_cycle_claims"][0]["removal_direction"]

        code, _out, err = self.run_gate("--write-baseline")

        self.assertEqual(code, 0, err)
        row = self.load()["unsupported_cycle_claims"][0]
        self.assertEqual(row["targets"], ["assets.depreciation"])
        self.assertEqual(row["removal_direction"], reason)
        self.assertEqual(row["removal_issue"], 100)

    def hand_record_both_targets(self):
        """Record a superset of what the tree shows, as a reviewed row would."""
        document = self.load()
        document["unsupported_cycle_claims"][0]["targets"] = ["assets.depreciation", "assets.scanning"]
        self.baseline.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")


class BaselineSchemaTests(GateTestCase):
    """Every field rule is exit 2: a malformed baseline is not a violation."""

    def valid_sections(self):
        return {section: [] for section in BASELINE_SECTIONS}

    def assert_untrustworthy(self, sections, needle, **header):
        self.write_baseline(sections, **header)
        code, _out, err = self.run_gate()
        self.assertEqual(code, 2, err)
        self.assertIn(needle, err)

    def test_header_fields_are_validated(self):
        self.assert_untrustworthy(self.valid_sections(), "schema", schema_version=99)
        self.assert_untrustworthy(self.valid_sections(), "canonical_python", canonical_python="3.11")
        self.assert_untrustworthy(self.valid_sections(), "policy_sha256", policy_sha256="0" * 64)

    def test_unknown_and_missing_top_level_keys_are_rejected(self):
        sections = self.valid_sections()
        sections["surprise"] = []
        self.assert_untrustworthy(sections, "top-level")

        sections = self.valid_sections()
        del sections["cycles"]
        self.assert_untrustworthy(sections, "top-level")

    def test_row_level_field_rules(self):
        cases = {
            "owner": {"owner": "area:nonexistent"},
            "owner ": {"owner": "renerettig"},
            "removal_issue": {"removal_issue": 0},
            "removal_issue ": {"removal_issue": "100"},
            "removal_direction": {"removal_direction": "TBD"},
            "removal_direction ": {"removal_direction": "TODO"},
            "removal_direction  ": {"removal_direction": "too short"},
            "accepted_reason": {"accepted_reason": ""},
            "disposition": {"disposition": "maybe"},
            "disposition ": {"disposition": ""},
            "count": {"count": 0},
            "count ": {"count": True},
            "rule": {"rule": "R-C4"},
            "kind": {"kind": "static"},
        }
        for label, mutation in cases.items():
            with self.subTest(field=label.strip()):
                sections = self.valid_sections()
                sections["layer_exceptions"] = [dict(self.exception_row(), **mutation)]
                self.write_baseline(sections)
                code, _out, err = self.run_gate()
                self.assertEqual(code, 2, err)

    def test_a_hand_edited_identity_is_rejected(self):
        sections = self.valid_sections()
        sections["layer_exceptions"] = [dict(self.exception_row(), id="R-S1|nope|nope|module-top")]

        self.assert_untrustworthy(sections, "identity")

    def test_rows_must_be_sorted_and_unique(self):
        first = self.exception_row()
        second = dict(
            first,
            id="R-S3|core.tasks.context|assets.services|module-top",
            rule="R-S3",
            target="assets.services",
            target_layer="domain-service",
        )
        sections = self.valid_sections()
        sections["layer_exceptions"] = [second, first]
        self.assert_untrustworthy(sections, "sorted")

        sections = self.valid_sections()
        sections["layer_exceptions"] = [first, dict(first)]
        self.assert_untrustworthy(sections, "duplicate")

    def test_a_recorded_owner_must_equal_the_derived_owner(self):
        sections = self.valid_sections()
        sections["layer_exceptions"] = [dict(self.exception_row(), owner="area:assets")]

        self.assert_untrustworthy(sections, "owner")

    def test_a_recorded_layer_must_equal_the_classified_layer(self):
        sections = self.valid_sections()
        sections["layer_exceptions"] = [dict(self.exception_row(), source_layer="kernel")]

        self.assert_untrustworthy(sections, "layer")

    def test_a_cycle_of_three_or_more_modules_needs_keystone_notes(self):
        sections = self.valid_sections()
        modules = ["assets.forms.asset_form", "assets.models.asset", "assets.services"]
        sections["cycles"] = [
            {
                "id": "|".join(["module-top", *modules]),
                "graph": "module-top",
                "modules": modules,
                "edges": [
                    {"source": "assets.forms.asset_form", "target": "assets.models.asset", "kind": "module-top"},
                    {"source": "assets.models.asset", "target": "assets.services", "kind": "module-top"},
                    {"source": "assets.services", "target": "assets.forms.asset_form", "kind": "module-top"},
                ],
                "owner": "area:assets",
                "removal_issue": 100,
                "disposition": DEBT_DISPOSITION,
                "removal_direction": TRIAGED_DIRECTION,
                "accepted_reason": "recorded",
            }
        ]

        self.assert_untrustworthy(sections, "notes")

    def test_a_cycle_edge_must_stay_inside_its_own_component(self):
        sections = self.valid_sections()
        modules = ["assets.forms.asset_form", "assets.services"]
        sections["cycles"] = [
            {
                "id": "|".join(["module-top", *modules]),
                "graph": "module-top",
                "modules": modules,
                "edges": [{"source": "assets.services", "target": "core.models", "kind": "module-top"}],
                "owner": "area:assets",
                "removal_issue": 100,
                "disposition": DEBT_DISPOSITION,
                "removal_direction": TRIAGED_DIRECTION,
                "accepted_reason": "recorded",
            }
        ]

        self.assert_untrustworthy(sections, "edges")

    def test_a_reviewed_policy_edit_can_be_re_stamped_but_not_laundered(self):
        """A drifted fingerprint blocks a check, and only re-stamps on a write.

        Without this, editing the policy would strand the baseline forever: the
        load that has to precede the write is the load the stale fingerprint
        rejects. It must not become an amnesty either -- debt added in the same
        commit is still refused.
        """
        self.seed = {"itambox/core/tasks/context.py": "from assets.models.asset import Asset\n"}
        self.write_tree(self.seed)
        sections = self.valid_sections()
        sections["layer_exceptions"] = [self.exception_row()]
        self.write_baseline(sections, policy_sha256="0" * 64)

        code, _out, err = self.run_gate()
        self.assertEqual(code, 2, err)
        self.assertIn("policy_sha256", err)

        code, out, err = self.run_gate("--write-baseline")
        self.assertEqual(code, 0, out + err)
        self.assertIn("fingerprint changed", out)
        self.assertEqual(self.load()["policy_sha256"], compute_policy_fingerprint(("itambox",)))
        self.assertEqual(self.run_gate()[0], 0)

    def test_a_drifted_fingerprint_still_refuses_debt_added_in_the_same_commit(self):
        self.write_tree(
            {
                "itambox/core/tasks/context.py": "from assets.models.asset import Asset\n",
                "itambox/core/events.py": "from assets.models.asset import Asset\n",
            }
        )
        sections = self.valid_sections()
        sections["layer_exceptions"] = [self.exception_row()]
        self.write_baseline(sections, policy_sha256="0" * 64)

        code, out, _err = self.run_gate("--write-baseline")

        self.assertEqual(code, 1)
        self.assertIn("refuses newly observed debt", out)
        self.assertIn("core.events", out)

    def test_the_round_trip_is_stable(self):
        self.write_tree({"itambox/core/tasks/context.py": "from assets.models.asset import Asset\n"})
        self.run_gate("--write-baseline")
        text = self.baseline.read_text(encoding="utf-8")

        self.assertTrue(text.endswith("\n"))
        self.assertNotIn("\\", text)
        with self.assertRaises(PolicyError):
            load_baseline(self.baseline, compute_policy_fingerprint(("itambox",)))

    def exception_row(self):
        return {
            "id": "R-S1|core.tasks.context|assets.models.asset|module-top",
            "rule": "R-S1",
            "source": "core.tasks.context",
            "source_layer": "platform-service",
            "target": "assets.models.asset",
            "target_layer": "domain-model",
            "kind": "module-top",
            "count": 1,
            "owner": "area:operations",
            "removal_issue": 100,
            "disposition": DEBT_DISPOSITION,
            "removal_direction": TRIAGED_DIRECTION,
            "accepted_reason": "Pre-existing when the boundary gate was introduced.",
        }


class IssueStateSnapshotTests(GateTestCase):
    """A debt row may only reference an open issue the reviewed snapshot records."""

    def seed_platform_service_violation(self):
        self.write_tree({"itambox/core/tasks/context.py": "from assets.models.asset import Asset\n"})

    def test_an_open_tracked_issue_passes(self):
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections())

        code, out, err = self.run_gate()

        self.assertEqual(code, 0, out + err)

    def test_a_closed_tracked_issue_is_untrustworthy(self):
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections())
        self.write_issue_states({100: "closed"})

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("#100", err)
        self.assertIn("closed", err)

    def test_a_tracked_issue_missing_from_the_snapshot_is_untrustworthy(self):
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections())
        self.write_issue_states({})

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("#100", err)

    def test_a_snapshot_with_issues_nobody_references_is_untrustworthy(self):
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections())
        self.write_issue_states({100: "open", 999: "open"})

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("#999", err)

    def test_a_missing_snapshot_is_untrustworthy_when_debt_exists(self):
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections())
        self.issue_states.unlink()

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("issue-state snapshot", err)

    def test_an_unparseable_snapshot_is_untrustworthy(self):
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections())
        self.issue_states.write_text("{not json", encoding="utf-8")

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("issue-state snapshot", err)

    def test_a_snapshot_with_a_non_numeric_issue_key_is_untrustworthy(self):
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections())
        self.issue_states.write_text(
            json.dumps(
                {"schema_version": ISSUE_STATES_SCHEMA_VERSION, "refreshed_at": "now", "issues": {"ten": "open"}}
            ),
            encoding="utf-8",
        )

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("non-numeric", err)


class AcceptedDispositionTests(GateTestCase):
    """``accepted`` rows are intentional architecture, not hidden debt."""

    def seed_platform_service_violation(self):
        self.write_tree({"itambox/core/tasks/context.py": "from assets.models.asset import Asset\n"})

    def accepted_row(self, **mutation):
        row = {
            "id": "R-S1|core.tasks.context|assets.models.asset|module-top",
            "rule": "R-S1",
            "source": "core.tasks.context",
            "source_layer": "platform-service",
            "target": "assets.models.asset",
            "target_layer": "domain-model",
            "kind": "module-top",
            "count": 1,
            "owner": "area:operations",
            "accepted_reason": "The platform owns the tenant lookup contract; reviewed acceptance, not removal debt.",
            "disposition": "accepted",
        }
        row.update(mutation)
        return row

    def test_a_policy_permitted_accepted_row_passes_without_a_snapshot(self):
        self.seed_platform_service_violation()
        sections = {section: [] for section in BASELINE_SECTIONS}
        sections["layer_exceptions"] = [self.accepted_row()]
        self.write_baseline(sections)
        self.issue_states.unlink()

        code, out, err = self.run_gate()

        self.assertEqual(code, 0, out + err)

    def test_an_accepted_row_cannot_record_a_removal_issue(self):
        self.seed_platform_service_violation()
        sections = {section: [] for section in BASELINE_SECTIONS}
        sections["layer_exceptions"] = [
            dict(self.accepted_row(), removal_issue=100, removal_direction=TRIAGED_DIRECTION)
        ]
        self.write_baseline(sections)

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("accepted", err)

    def test_an_accepted_row_requires_a_stable_rationale(self):
        self.seed_platform_service_violation()
        sections = {section: [] for section in BASELINE_SECTIONS}
        sections["layer_exceptions"] = [dict(self.accepted_row(), accepted_reason="")]
        self.write_baseline(sections)

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("accepted_reason", err)

    def test_an_absolutely_forbidden_rule_cannot_be_accepted(self):
        sections = {section: [] for section in BASELINE_SECTIONS}
        sections["layer_exceptions"] = [
            {
                "id": "R-M1|assets.models.asset|assets.forms.asset_form|module-top",
                "rule": "R-M1",
                "source": "assets.models.asset",
                "source_layer": "domain-model",
                "target": "assets.forms.asset_form",
                "target_layer": "presentation",
                "kind": "module-top",
                "count": 1,
                "owner": "area:assets",
                "accepted_reason": "Deliberate coupling, permanently allowed.",
                "disposition": "accepted",
            }
        ]
        self.write_baseline(sections)

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("R-M1", err)

    def test_a_cycle_row_cannot_be_accepted(self):
        sections = {section: [] for section in BASELINE_SECTIONS}
        sections["cycles"] = [
            {
                "id": "module-top|assets.depreciation|assets.services",
                "graph": "module-top",
                "modules": ["assets.depreciation", "assets.services"],
                "edges": [
                    {"source": "assets.depreciation", "target": "assets.services", "kind": "module-top"},
                    {"source": "assets.services", "target": "assets.depreciation", "kind": "module-top"},
                ],
                "owner": "area:assets",
                "removal_issue": 100,
                "removal_direction": TRIAGED_DIRECTION,
                "accepted_reason": "recorded",
                "disposition": "accepted",
            }
        ]
        self.write_baseline(sections)

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("always debt", err)


class IssueStateRefreshTests(GateTestCase):
    """``refresh_issue_states`` freezes reviewed states through an injected runner."""

    def seed_platform_service_violation(self):
        self.write_tree({"itambox/core/tasks/context.py": "from assets.models.asset import Asset\n"})

    def fake_runner(self, states, pull_request=False):
        def runner(command, **kwargs):
            number = int(command[2].rsplit("/", 1)[1])
            if number in states:
                payload = json.dumps(
                    {
                        "state": states[number],
                        "pull_request": {"number": number} if pull_request else None,
                    }
                )
                return types.SimpleNamespace(returncode=0, stdout=payload, stderr="")
            return types.SimpleNamespace(returncode=1, stdout="", stderr="Not Found")

        return runner

    def test_refresh_records_the_fetched_states(self):
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections())
        self.write_issue_states({100: "closed"})
        fingerprint = compute_policy_fingerprint(("itambox",))

        changed = refresh_issue_states(
            self.baseline, self.issue_states, fingerprint, runner=self.fake_runner({100: "open"})
        )

        self.assertTrue(changed)
        states = json.loads(self.issue_states.read_text(encoding="utf-8"))
        self.assertEqual(states["issues"], {"100": "open"})

    def test_refresh_is_deterministic_when_nothing_changed(self):
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections())
        fingerprint = compute_policy_fingerprint(("itambox",))
        runner = self.fake_runner({100: "open"})

        refresh_issue_states(self.baseline, self.issue_states, fingerprint, runner=runner)
        before = self.issue_states.read_bytes()

        changed = refresh_issue_states(self.baseline, self.issue_states, fingerprint, runner=runner)

        self.assertFalse(changed)
        self.assertEqual(self.issue_states.read_bytes(), before)

    def test_refresh_fails_on_a_missing_issue(self):
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections())
        fingerprint = compute_policy_fingerprint(("itambox",))

        with self.assertRaises(PolicyError) as caught:
            refresh_issue_states(self.baseline, self.issue_states, fingerprint, runner=self.fake_runner({}))

        self.assertIn("#100", str(caught.exception))
        self.assertIn("Not Found", str(caught.exception))

    def test_refresh_fails_when_the_gh_cli_is_unavailable(self):
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections())
        fingerprint = compute_policy_fingerprint(("itambox",))

        def missing(command, **kwargs):
            raise FileNotFoundError("gh")

        with self.assertRaises(PolicyError) as caught:
            refresh_issue_states(self.baseline, self.issue_states, fingerprint, runner=missing)

        self.assertIn("gh CLI", str(caught.exception))

    def test_refresh_rejects_a_pull_request_reference(self):
        # The GitHub issues endpoint also serves pull requests; a removal
        # tracker must be a real issue, so a PR payload is never accepted.
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections())
        fingerprint = compute_policy_fingerprint(("itambox",))

        with self.assertRaises(PolicyError) as caught:
            refresh_issue_states(
                self.baseline,
                self.issue_states,
                fingerprint,
                runner=self.fake_runner({100: "open"}, pull_request=True),
            )

        self.assertIn("#100", str(caught.exception))
        self.assertIn("pull request", str(caught.exception))

    def test_refresh_rejects_an_unparseable_response(self):
        self.seed_platform_service_violation()
        self.write_baseline(self.scaffold_sections())
        fingerprint = compute_policy_fingerprint(("itambox",))

        def broken(command, **kwargs):
            return types.SimpleNamespace(returncode=0, stdout="{not json", stderr="")

        with self.assertRaises(PolicyError) as caught:
            refresh_issue_states(self.baseline, self.issue_states, fingerprint, runner=broken)

        self.assertIn("#100", str(caught.exception))
        self.assertIn("cannot parse", str(caught.exception))


class BootstrapTests(GateTestCase):
    """The scaffold is structurally incapable of passing."""

    def test_bootstrapping_writes_sentinels_and_fails(self):
        self.write_tree({"itambox/core/tasks/context.py": "from assets.models.asset import Asset\n"})

        code, out, _err = self.run_gate("--write-baseline")

        self.assertEqual(code, 1)
        self.assertIn("removal_issue", out)
        self.assertIn("removal_direction", out)
        rows = self.load()["layer_exceptions"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["removal_issue"], 0)
        self.assertEqual(rows[0]["disposition"], "TODO")
        self.assertEqual(rows[0]["removal_direction"], "TODO")
        self.assertEqual(rows[0]["owner"], "area:operations")

    def test_the_scaffold_then_fails_validation(self):
        self.write_tree({"itambox/core/tasks/context.py": "from assets.models.asset import Asset\n"})
        self.run_gate("--write-baseline")

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("disposition", err)


class ReportOnlyTests(GateTestCase):
    def test_report_only_cannot_be_mistaken_for_a_pass(self):
        self.empty_baseline()
        self.write_tree({"itambox/core/tasks/context.py": "from assets.models.asset import Asset\n"})

        code, out, err = self.run_gate("--report-only")

        self.assertEqual(code, 0)
        self.assertIn("R-S1", out)
        self.assertEqual(err.count(REPORT_ONLY_BANNER), 2)

    def test_report_only_works_without_a_baseline_at_all(self):
        code, _out, err = self.run_gate("--report-only")

        self.assertEqual(code, 0)
        self.assertIn("REPORT ONLY", err)


class DocumentationLinkTests(GateTestCase):
    def test_a_dead_relative_markdown_link_fails(self):
        self.empty_baseline()
        write(self.root, "CONTRIBUTING.md", "See the [ADR](itambox/docs/development/adr-0001-missing.md).\n")

        code, out, _err = self.run_gate()

        self.assertEqual(code, 1)
        self.assertIn("R-DOC1", out)
        self.assertIn("adr-0001-missing.md", out)

    def test_absolute_and_anchor_links_are_not_fetched(self):
        self.empty_baseline()
        write(
            self.root, "CONTRIBUTING.md", "[a](https://example.invalid/x) [b](#section) [c](mailto:x@example.invalid)\n"
        )

        self.assertEqual(self.run_gate()[0], 0)

    def test_an_anchor_on_an_existing_file_resolves(self):
        self.empty_baseline()
        write(self.root, "CONTRIBUTING.md", "[a](AGENTS.md#lint-architecture-boundaries)\n")
        write(self.root, "AGENTS.md", "# Agents\n")

        self.assertEqual(self.run_gate()[0], 0)


class InterpreterTests(GateTestCase):
    def test_a_non_canonical_interpreter_is_refused_before_any_file_is_read(self):
        original = sys.version_info
        try:
            sys.version_info = (3, 11, 0, "final", 0)
            code, _out, err = self.run_gate()
        finally:
            sys.version_info = original

        self.assertEqual(code, 2)
        self.assertIn("3.12", err)


class UntrustworthyResultTests(GateTestCase):
    def test_an_unparseable_source_file_is_exit_two(self):
        self.empty_baseline()
        write(self.root, "itambox/assets/broken.py", "def broken(:\n")

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("cannot parse", err)

    def test_an_unclassifiable_module_is_exit_two_and_names_the_module(self):
        self.empty_baseline()
        write(self.root, "itambox/assets/newthing.py", "")

        code, _out, err = self.run_gate()

        self.assertEqual(code, 2)
        self.assertIn("assets.newthing", err)

    def test_narrowing_the_targets_invalidates_the_baseline(self):
        self.empty_baseline()

        code, _out, err = self.run_gate("itambox/assets")

        self.assertEqual(code, 2)
        self.assertIn("policy_sha256", err)


@unittest.skipUnless(
    sys.version_info[:2] == CANONICAL_PYTHON,
    "the architecture gate refuses non-canonical interpreters",
)
class RepositoryBaselineTests(unittest.TestCase):
    """The checked-in baseline reproduces this tree. No literal counts."""

    def run_repository_gate(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(extra))
        return code, out.getvalue(), err.getvalue()

    def test_the_checked_in_baseline_matches_the_repository(self):
        code, out, err = self.run_repository_gate()

        self.assertEqual(code, 0, out + err)

    def test_every_recorded_owner_is_a_repository_area_label(self):
        document = json.loads((REPOSITORY_ROOT / "scripts" / "architecture_baseline.json").read_text(encoding="utf-8"))
        owners = {row["owner"] for section in BASELINE_SECTIONS for row in document[section]}

        self.assertLessEqual(owners, AREA_LABELS)

    def test_the_baseline_records_no_absolutely_forbidden_rule(self):
        document = json.loads((REPOSITORY_ROOT / "scripts" / "architecture_baseline.json").read_text(encoding="utf-8"))

        self.assertEqual([row for row in document["layer_exceptions"] if row["rule"] == "R-M1"], [])


if __name__ == "__main__":
    unittest.main()
