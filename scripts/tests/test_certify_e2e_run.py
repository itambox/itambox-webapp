"""Certification tests for selected/full Playwright evidence."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from scripts.certify_e2e_run import CertificationError, _parser, certify_run
from scripts.select_e2e_scopes import build_selection, validate_scope_map
from scripts.tests.test_select_e2e_scopes import BASE, HEAD, MERGE, _map_document

SYNTHETIC = "d" * 40


class CertificationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.scope_map = _map_document(self.root)
        validate_scope_map(self.scope_map, self.root, require_catalog_scopes=False)
        self.selection = build_selection(
            self.scope_map,
            self.root,
            event_name="pull_request",
            base_sha=BASE,
            head_sha=HEAD,
            merge_base_sha=MERGE,
            changes=[{"status": "M", "new_path": "src/assets/services.py"}],
        )
        self.identity = {
            "event_name": "pull_request",
            "base_sha": BASE,
            "head_sha": HEAD,
            "merge_base_sha": MERGE,
            "changed_path_digest": self.selection["changed_path_digest"],
        }
        self.current_identity = {
            **self.identity,
            "provenance_schema": 2,
            "tested_checkout_sha": HEAD,
            "tested_checkout_kind": "head",
            "synthetic_merge_sha": SYNTHETIC,
        }
        self.discovery = {
            "schema": 1,
            "selection_identity": copy.deepcopy(self.identity),
            "tested_checkout_sha": HEAD,
            "selected_spec_paths": [
                "spec/apps/assets",
                "spec/contracts/asset-custody",
                "spec/legacy-smoke",
                "spec/smoke",
            ],
            "discovered_specs": [
                "spec/apps/assets/contract.spec.ts",
                "spec/contracts/asset-custody/contract.spec.ts",
                "spec/legacy-smoke/contract.spec.ts",
                "spec/smoke/contract.spec.ts",
            ],
            "discovered_tests": [
                {"id": "assets::contract", "spec": "spec/apps/assets/contract.spec.ts", "project": "admin"},
                {
                    "id": "custody::contract",
                    "spec": "spec/contracts/asset-custody/contract.spec.ts",
                    "project": "operator",
                },
                {"id": "legacy::contract", "spec": "spec/legacy-smoke/contract.spec.ts", "project": "operator"},
                {"id": "smoke::contract", "spec": "spec/smoke/contract.spec.ts", "project": "operator"},
            ],
            "setup_projects": ["setup-admin", "setup-aggregate", "setup-operator"],
            "focused": False,
        }
        self.execution = {
            "schema": 1,
            "selection": copy.deepcopy(self.selection),
            "selection_identity": copy.deepcopy(self.identity),
            "tested_checkout_sha": HEAD,
            "selected_spec_paths": copy.deepcopy(self.discovery["selected_spec_paths"]),
            "executed_specs": copy.deepcopy(self.discovery["discovered_specs"]),
            "executed_tests": [
                {
                    "id": row["id"],
                    "spec": row["spec"],
                    "project": row["project"],
                    "status": "passed",
                    "attempts": [{"retry": 0, "status": "passed", "identity": f"e2e-operator-0-r0-{row['id']}"}],
                }
                for row in self.discovery["discovered_tests"]
            ],
            "cleanup": {"success": True, "failures": []},
            "focused": False,
            "report": {"file": "results.json", "malformed": False, "error": None},
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def run_certification(self, *, selection=None, discovery=Ellipsis, execution=None, current=None):
        if discovery is Ellipsis:
            discovery = self.discovery
        return certify_run(
            selection if selection is not None else self.selection,
            discovery,
            execution if execution is not None else self.execution,
            current if current is not None else self.current_identity,
            self.root,
            self.scope_map,
        )

    def test_valid_selected_run_certifies_with_counts_and_identity(self):
        result = self.run_certification()
        self.assertTrue(result["success"])
        self.assertEqual(result["verdict"], "passed")
        self.assertEqual(result["tested_checkout_sha"], HEAD)
        self.assertEqual(result["provenance_schema"], 2)
        self.assertEqual(result["synthetic_merge_sha"], SYNTHETIC)
        self.assertEqual(result["discovered_test_count"], 4)
        self.assertEqual(result["executed_test_count"], 4)
        self.assertEqual(result["retry_count"], 0)

    def test_selected_run_requires_only_the_setup_projects_used_by_selected_projects(self):
        discovery = copy.deepcopy(self.discovery)
        discovery["setup_projects"] = ["setup-admin", "setup-aggregate", "setup-operator"]
        result = self.run_certification(discovery=discovery)
        self.assertTrue(result["success"])

    def test_synthetic_merge_checkout_is_rejected_by_the_raw_head_contract(self):
        runtime = "e" * 40
        current = copy.deepcopy(self.identity)
        current.update(
            tested_checkout_sha=runtime,
            tested_checkout_kind="merge_candidate",
            synthetic_merge_sha=SYNTHETIC,
            provenance_schema=2,
        )
        discovery = copy.deepcopy(self.discovery)
        discovery["tested_checkout_sha"] = runtime
        execution = copy.deepcopy(self.execution)
        execution["tested_checkout_sha"] = runtime
        with self.assertRaises(CertificationError):
            self.run_certification(discovery=discovery, execution=execution, current=current)

    def test_execution_selection_mismatch_is_rejected(self):
        execution = copy.deepcopy(self.execution)
        execution["selection"] = copy.deepcopy(self.selection)
        execution["selection"]["mode"] = "full"
        execution["selection"]["scopes"] = ["all"]
        execution["selection"]["spec_paths"] = ["spec"]
        execution["selection"]["reasons"] = []
        with self.assertRaises(CertificationError):
            self.run_certification(execution=execution)

    def test_selected_path_with_no_discovered_tests_fails(self):
        discovery = copy.deepcopy(self.discovery)
        discovery["discovered_specs"] = [
            path for path in discovery["discovered_specs"] if not path.startswith("spec/apps/assets/")
        ]
        discovery["discovered_tests"] = [
            test for test in discovery["discovered_tests"] if not test["spec"].startswith("spec/apps/assets/")
        ]
        with self.assertRaises(CertificationError):
            self.run_certification(discovery=discovery)

    def test_selected_spec_omitted_or_outside_selection_fails(self):
        execution = copy.deepcopy(self.execution)
        execution["executed_specs"] = execution["executed_specs"][1:]
        execution["executed_tests"] = execution["executed_tests"][1:]
        with self.assertRaises(CertificationError):
            self.run_certification(execution=execution)

        execution = copy.deepcopy(self.execution)
        execution["executed_specs"].append("spec/not-selected.spec.ts")
        execution["executed_tests"].append(
            {
                "id": "outside",
                "spec": "spec/not-selected.spec.ts",
                "project": "operator",
                "status": "passed",
                "attempts": [{"retry": 0, "status": "passed", "identity": "e2e-operator-0-r0-outside"}],
            }
        )
        with self.assertRaises(CertificationError):
            self.run_certification(execution=execution)

    def test_skip_fixme_interrupted_unknown_and_focus_fail(self):
        for status in ("skipped", "fixme", "interrupted", "unknown"):
            execution = copy.deepcopy(self.execution)
            execution["executed_tests"][0]["status"] = status
            with self.subTest(status=status), self.assertRaises(CertificationError):
                self.run_certification(execution=execution)

        discovery = copy.deepcopy(self.discovery)
        discovery["focused"] = True
        with self.assertRaises(CertificationError):
            self.run_certification(discovery=discovery)

    def test_retry_pass_requires_distinct_retry_safe_identities_and_cleanup(self):
        execution = copy.deepcopy(self.execution)
        execution["executed_tests"][0]["attempts"] = [
            {"retry": 0, "status": "failed", "identity": "e2e-operator-0-r0-assets"},
            {"retry": 1, "status": "passed", "identity": "e2e-operator-0-r1-assets"},
        ]
        execution["executed_tests"][0]["status"] = "passed"
        result = self.run_certification(execution=execution)
        self.assertTrue(result["success"])
        self.assertEqual(result["retry_count"], 1)

        reused = copy.deepcopy(execution)
        reused["executed_tests"][0]["attempts"][1]["identity"] = reused["executed_tests"][0]["attempts"][0]["identity"]
        with self.assertRaises(CertificationError):
            self.run_certification(execution=reused)

        cleanup_failed = copy.deepcopy(self.execution)
        cleanup_failed["cleanup"] = {"success": False, "failures": ["asset: delete failed"]}
        with self.assertRaises(CertificationError):
            self.run_certification(execution=cleanup_failed)

    def test_identity_mismatch_missing_setup_or_missing_report_fails(self):
        for key, value in (
            ("head_sha", "d" * 40),
            ("merge_base_sha", "e" * 40),
            ("changed_path_digest", "sha256:" + "0" * 64),
        ):
            current = copy.deepcopy(self.current_identity)
            current[key] = value
            with self.subTest(key=key), self.assertRaises(CertificationError):
                self.run_certification(current=current)

        missing_setup = copy.deepcopy(self.discovery)
        missing_setup["setup_projects"] = ["setup-admin", "setup-operator"]
        with self.assertRaises(CertificationError):
            self.run_certification(discovery=missing_setup)

        with self.assertRaises(CertificationError):
            self.run_certification(discovery=None)

    def test_current_merge_candidate_checkout_is_rejected(self):
        runtime = "f" * 40
        selection = copy.deepcopy(self.selection)
        discovery = copy.deepcopy(self.discovery)
        execution = copy.deepcopy(self.execution)
        current = copy.deepcopy(self.identity)
        current.update(
            tested_checkout_sha=runtime,
            tested_checkout_kind="merge_candidate",
            synthetic_merge_sha=SYNTHETIC,
            provenance_schema=2,
        )
        discovery["tested_checkout_sha"] = runtime
        execution["tested_checkout_sha"] = runtime
        with self.assertRaises(CertificationError):
            self.run_certification(
                selection=selection,
                discovery=discovery,
                execution=execution,
                current=current,
            )

    def test_pr_runtime_identity_requires_synthetic_merge_provenance(self):
        current = copy.deepcopy(self.identity)
        current.update(provenance_schema=2, tested_checkout_sha=HEAD, tested_checkout_kind="head")
        with self.assertRaises(CertificationError):
            self.run_certification(current=current)

    def test_provenance_schema_rejects_non_integer_values(self):
        current = copy.deepcopy(self.current_identity)
        current["provenance_schema"] = 2.0
        with self.assertRaises(CertificationError):
            self.run_certification(current=current)

    def test_cli_identity_flags_include_the_complete_runtime_contract(self):
        args = _parser().parse_args(
            [
                "--selection",
                "selection.json",
                "--discovery",
                "discovery.json",
                "--execution",
                "execution.json",
                "--output",
                "certification.json",
                "--event-name",
                "pull_request",
                "--base-sha",
                BASE,
                "--head-sha",
                HEAD,
                "--merge-base-sha",
                MERGE,
                "--changed-path-digest",
                "sha256:" + "0" * 64,
                "--tested-checkout-sha",
                HEAD,
                "--tested-checkout-kind",
                "head",
                "--synthetic-merge-sha",
                SYNTHETIC,
            ]
        )
        self.assertEqual(args.tested_checkout_sha, HEAD)
        self.assertEqual(args.tested_checkout_kind, "head")
        self.assertEqual(args.synthetic_merge_sha, SYNTHETIC)

        selection = build_selection(
            self.scope_map,
            self.root,
            event_name="push",
            base_sha=HEAD,
            head_sha=HEAD,
            merge_base_sha=HEAD,
            changes=[],
        )
        all_specs = sorted(
            str(path.relative_to(self.root / "itambox/tests/e2e")).replace("\\", "/")
            for path in (self.root / "itambox/tests/e2e/spec").rglob("*.spec.ts")
        )
        discovery = copy.deepcopy(self.discovery)
        full_identity = {
            "event_name": "push",
            "base_sha": HEAD,
            "head_sha": HEAD,
            "merge_base_sha": HEAD,
            "changed_path_digest": selection["changed_path_digest"],
        }
        discovery["selection_identity"] = copy.deepcopy(full_identity)
        discovery["tested_checkout_sha"] = HEAD
        discovery["selected_spec_paths"] = ["spec"]
        discovery["setup_projects"] = ["setup-operator"]
        discovery["discovered_specs"] = all_specs
        discovery["discovered_tests"] = [{"id": spec, "spec": spec, "project": "operator"} for spec in all_specs]
        execution = copy.deepcopy(self.execution)
        execution["selection"] = copy.deepcopy(selection)
        execution["selection_identity"] = copy.deepcopy(full_identity)
        execution["tested_checkout_sha"] = HEAD
        execution["selected_spec_paths"] = ["spec"]
        execution["executed_specs"] = all_specs
        execution["executed_tests"] = [
            {
                "id": spec,
                "spec": spec,
                "project": "operator",
                "status": "passed",
                "attempts": [{"retry": 0, "status": "passed", "identity": f"e2e-operator-0-r0-{index}"}],
            }
            for index, spec in enumerate(all_specs)
        ]
        result = self.run_certification(
            selection=selection,
            discovery=discovery,
            execution=execution,
            current={
                "event_name": "push",
                "base_sha": HEAD,
                "head_sha": HEAD,
                "merge_base_sha": HEAD,
                "changed_path_digest": selection["changed_path_digest"],
                "provenance_schema": 2,
                "tested_checkout_sha": HEAD,
                "tested_checkout_kind": "head",
                "synthetic_merge_sha": None,
            },
        )
        self.assertTrue(result["success"])

        discovery["discovered_specs"] = all_specs[:-1]
        with self.assertRaises(CertificationError):
            self.run_certification(
                selection=selection,
                discovery=discovery,
                execution=execution,
                current={
                    "event_name": "push",
                    "base_sha": HEAD,
                    "head_sha": HEAD,
                    "merge_base_sha": HEAD,
                    "changed_path_digest": selection["changed_path_digest"],
                    "provenance_schema": 2,
                    "tested_checkout_sha": HEAD,
                    "tested_checkout_kind": "head",
                    "synthetic_merge_sha": None,
                },
            )


if __name__ == "__main__":
    unittest.main()
