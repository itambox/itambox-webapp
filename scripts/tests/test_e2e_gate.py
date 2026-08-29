"""Fail-closed aggregate E2E gate policy tests."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_e2e_gate import evaluate_gate, GateInputError

BASE = "a" * 40
HEAD = "b" * 40
MERGE = "c" * 40
DIGEST = "sha256:" + "d" * 64


class GatePolicyTests(unittest.TestCase):
    def setUp(self):
        self.identity = {
            "event_name": "pull_request",
            "base_sha": BASE,
            "head_sha": HEAD,
            "merge_base_sha": MERGE,
            "changed_path_digest": DIGEST,
        }
        self.selection = {
            "schema": 1,
            "mode": "selected",
            **self.identity,
            "scopes": ["app:assets", "legacy-smoke", "smoke"],
            "spec_paths": ["spec/apps/assets", "spec/legacy-smoke", "spec/smoke"],
            "reasons": [],
        }
        self.certification = {"schema": 1, "success": True, "verdict": "passed", **self.identity}
        self.base = {
            "schema": 1,
            "detector": {
                "result": "success",
                "selection": copy.deepcopy(self.selection),
                "artifact_exists": True,
            },
            "execution": {
                "result": "success",
                "selection": copy.deepcopy(self.selection),
                "tested_checkout_sha": HEAD,
                "selection_artifact_exists": True,
                "discovery_artifact_exists": True,
                "report_artifact_exists": True,
                "certification_artifact_exists": True,
            },
            "certification": copy.deepcopy(self.certification),
            "current": copy.deepcopy(self.identity),
        }

    def test_selected_success_requires_all_evidence_and_passes(self):
        result = evaluate_gate(self.base)
        self.assertTrue(result["success"])
        self.assertEqual(result["verdict"], "passed")

    def test_validated_none_succeeds_without_playwright_artifacts(self):
        value = copy.deepcopy(self.base)
        value["detector"]["selection"]["mode"] = "none"
        value["detector"]["selection"]["scopes"] = []
        value["detector"]["selection"]["spec_paths"] = []
        value["execution"] = {"result": "skipped"}
        value["certification"] = None
        result = evaluate_gate(value)
        self.assertTrue(result["success"])

    def test_every_detector_non_success_fails(self):
        for result_value in ("failure", "cancelled", "skipped", "timed_out"):
            value = copy.deepcopy(self.base)
            value["detector"]["result"] = result_value
            with self.subTest(result=result_value):
                result = evaluate_gate(value)
                self.assertFalse(result["success"])

    def test_every_required_execution_non_success_fails(self):
        for result_value in ("failure", "cancelled", "skipped", "timed_out"):
            value = copy.deepcopy(self.base)
            value["execution"]["result"] = result_value
            with self.subTest(result=result_value):
                result = evaluate_gate(value)
                self.assertFalse(result["success"])

    def test_none_execution_must_not_run_and_selected_identity_must_match(self):
        value = copy.deepcopy(self.base)
        value["detector"]["selection"]["mode"] = "none"
        value["detector"]["selection"]["scopes"] = []
        value["detector"]["selection"]["spec_paths"] = []
        value["execution"]["result"] = "success"
        self.assertFalse(evaluate_gate(value)["success"])

        value = copy.deepcopy(self.base)
        value["execution"]["tested_checkout_sha"] = "e" * 40
        self.assertFalse(evaluate_gate(value)["success"])

        value = copy.deepcopy(self.base)
        value["current"]["head_sha"] = "f" * 40
        self.assertFalse(evaluate_gate(value)["success"])

    def test_missing_artifacts_or_certification_failure_is_not_green(self):
        for field in (
            "selection_artifact_exists",
            "discovery_artifact_exists",
            "report_artifact_exists",
            "certification_artifact_exists",
        ):
            value = copy.deepcopy(self.base)
            value["execution"][field] = False
            with self.subTest(field=field):
                self.assertFalse(evaluate_gate(value)["success"])

        value = copy.deepcopy(self.base)
        value["certification"]["success"] = False
        self.assertFalse(evaluate_gate(value)["success"])

        value = copy.deepcopy(self.base)
        value["certification"] = {"not": "a certification"}
        self.assertFalse(evaluate_gate(value)["success"])

    def test_invalid_gate_input_raises_instead_of_defaulting_green(self):
        for value in ({}, {"schema": 99}, {"schema": 1, "detector": None}):
            with self.subTest(value=value), self.assertRaises(GateInputError):
                evaluate_gate(value)

    def test_authoritative_full_selection_is_required_even_for_empty_change_set(self):
        value = copy.deepcopy(self.base)
        value["detector"]["selection"] = {
            "schema": 1,
            "mode": "full",
            "event_name": "schedule",
            "base_sha": HEAD,
            "head_sha": HEAD,
            "merge_base_sha": HEAD,
            "changed_path_digest": DIGEST,
            "scopes": ["all"],
            "spec_paths": ["spec"],
            "reasons": [],
        }
        value["current"] = {
            "event_name": "schedule",
            "base_sha": HEAD,
            "head_sha": HEAD,
            "merge_base_sha": HEAD,
            "changed_path_digest": DIGEST,
        }
        value["execution"]["selection"] = copy.deepcopy(value["detector"]["selection"])
        value["certification"] = {"schema": 1, "success": True, "verdict": "passed", **value["current"]}
        self.assertTrue(evaluate_gate(value)["success"])


if __name__ == "__main__":
    unittest.main()
