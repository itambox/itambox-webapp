"""Independent oracle for the T02 specification-contract fixture corpus.

This module validates a language-neutral JSON fixture corpus that encodes
expected outcomes sourced from the exact contract paragraphs of the
specification architecture (sections 3, 4, 8.2 and 16). The corpus covers
scalar presence and normalization, deterministic composition, historical
validity, and the five B/L/R reconciliation outcomes.

The oracle is independent of any implementation by construction: this module
imports only the Python standard library and never imports, instantiates or
executes application code. Expected outcomes are static JSON values; they are
never produced by calling the implementation under test.
"""

import json
import re
import unittest
from pathlib import Path

FIXTURE_ROOT = Path(__file__).resolve().parent / "specification_contract_fixtures"

# Domain issue codes from the adopted interface ledger (T01 contract, section 4.2).
DOMAIN_ISSUE_CODES = frozenset(
    {
        "INVALID_TYPE",
        "INVALID_DECIMAL",
        "INVALID_RANGE",
        "INVALID_DATE",
        "INVALID_CHOICE",
        "REQUIRED_FIELD",
        "UNKNOWN_FIELD_KEY",
        "READ_ONLY_FIELD",
        "CONFLICT_CLEAR_OVERLAP",
        "DUPLICATE_FIELD",
        "IMMUTABLE_DEFINITION",
        "OWNERSHIP_CONFLICT",
        "REFERENCE_CONFLICT",
        "DEPENDENCY_RETIREMENT",
        "UNSUPPORTED_STRUCTURE",
        "STALE_RESOURCE",
        "STALE_DEFINITION",
        "STALE_PLAN",
        "EXPORT_BLOCKED",
        "OBJECT_UNAVAILABLE",
        "MISSING_PRECONDITION",
    }
)

# Allowed three-way decisions from section 8.2.
DECISIONS = frozenset(
    {
        "unchanged",
        "upstream_safe",
        "local_override",
        "converged",
        "conflict_keep_local",
        "conflict_take_upstream",
        "conflict_abort",
    }
)

CASE_ID_PATTERN = re.compile(r"^T02-(SCALAR|COMP|HIST|REC)-\d{3}$")


def _load() -> dict:
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    documents = {}
    for name in manifest["corpus_files"]:
        documents[name] = json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))
    return manifest, documents


def _truth_table_decision(b, l, r):
    """Section 8.2 decision for a managed path given B, L and R."""
    if l == b and r == b:
        return "unchanged"
    if l == b and r != b:
        return "upstream_safe"
    if l != b and r == b:
        return "local_override"
    if l == r:
        return "converged"
    return "conflict"


def _coverage_ids(coverage):
    """Flatten manifest coverage values (plain id lists or nested dicts of id lists)."""
    for value in coverage.values():
        if isinstance(value, dict):
            for nested in value.values():
                yield from nested
        else:
            yield from value


class SpecificationContractFixtureCorpusTest(unittest.TestCase):
    """Conformance and coverage checks over the T02 fixture corpus."""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.manifest, cls.documents = _load()
        cls.cases = {}
        for name, doc in cls.documents.items():
            for case in doc["cases"]:
                cls.cases[case["id"]] = (name, case)

    def test_corpus_files_parse_and_declare_language_neutrality(self):
        self.assertEqual(self.manifest["schema_version"], 1)
        self.assertIs(self.manifest["language_neutral"], True)
        self.assertEqual(self.manifest["task"], "T02")
        for name, doc in self.documents.items():
            self.assertEqual(doc["schema_version"], 1, name)
            self.assertIs(doc["language_neutral"], True, name)
            self.assertGreaterEqual(len(doc["cases"]), 1, name)

    def test_case_ids_are_unique_and_well_formed(self):
        ids = list(self.cases)
        self.assertEqual(len(ids), len(set(ids)), "duplicate case ids in corpus")
        for case_id in ids:
            self.assertRegex(case_id, CASE_ID_PATTERN, case_id)

    def test_every_case_has_required_oracle_fields(self):
        for case_id, (name, case) in self.cases.items():
            for field in (
                "id",
                "invariant",
                "source_section",
                "initial_state",
                "operation",
                "expected_result",
                "expected_unchanged",
            ):
                self.assertIn(field, case, f"{case_id} in {name} lacks {field}")
            self.assertIsInstance(case["invariant"], str, case_id)
            self.assertIsInstance(case["source_section"], str, case_id)
            self.assertIsInstance(case["initial_state"], dict, case_id)
            self.assertIsInstance(case["operation"], dict, case_id)
            self.assertIsInstance(case["expected_result"], dict, case_id)
            self.assertIsInstance(case["expected_unchanged"], dict, case_id)
        self.assertGreaterEqual(len(self.cases), 1)

    def test_rejected_cases_use_stable_issue_codes_and_paths(self):
        for case_id, (name, case) in self.cases.items():
            result = case["expected_result"]
            if result.get("outcome") != "rejected":
                continue
            error = result.get("error")
            self.assertIsInstance(error, str, case_id)
            self.assertIn(error, DOMAIN_ISSUE_CODES, f"{case_id} error {error!r}")
            if "path" in result:
                self.assertIsInstance(result["path"], list, case_id)
                for element in result["path"]:
                    self.assertIsInstance(element, str, case_id)

    def test_accepted_patch_cases_declare_stored_state(self):
        for case_id, (name, case) in self.cases.items():
            result = case["expected_result"]
            if case["operation"].get("kind") == "patch" and result.get("outcome") == "accepted":
                self.assertIn("stored", result, case_id)

    def test_expected_unchanged_matches_initial_state_byte_for_value(self):
        for case_id, (name, case) in self.cases.items():
            initial = case["initial_state"]
            unchanged = case["expected_unchanged"]
            for key, value in unchanged.items():
                if key in initial:
                    self.assertEqual(
                        initial[key],
                        value,
                        f"{case_id}: expected_unchanged[{key!r}] diverges from initial_state",
                    )

    def test_reconciliation_truth_table_matches_section_8_2(self):
        compared = []
        for case_id, (name, case) in self.cases.items():
            if case["operation"].get("kind") != "three_way_compare":
                continue
            state = case["initial_state"]
            for key in ("B", "L", "R"):
                self.assertIn(key, state, case_id)
                self.assertIsInstance(state[key], dict, case_id)
            result = case["expected_result"]
            if result.get("outcome") == "unmanaged":
                # Unmanaged paths (for example tenant_id) are never reconciled.
                self.assertNotIn("decision", result, case_id)
                self.assertIs(result.get("managed_path"), False, case_id)
                continue
            declared = result["decision"]
            self.assertIn(declared, DECISIONS, case_id)
            expected = _truth_table_decision(state["B"], state["L"], state["R"])
            if expected == "conflict":
                self.assertTrue(declared.startswith("conflict"), case_id)
            else:
                self.assertEqual(declared, expected, case_id)
            if declared == "upstream_safe" and "proposed" in result:
                self.assertIs(result["proposed"], True, case_id)
            if declared == "local_override" and "proposed" in result:
                self.assertIs(result["proposed"], False, case_id)
            compared.append(declared)
        for required in ("unchanged", "upstream_safe", "local_override", "converged"):
            self.assertIn(required, compared, f"missing B/L/R outcome {required}")
        self.assertTrue(
            any(label.startswith("conflict") for label in compared),
            "no explicit conflict outcome in corpus",
        )

    def test_atomic_list_paths_are_never_index_merged(self):
        for case_id, (name, case) in self.cases.items():
            operation = case["operation"]
            if operation.get("kind") != "three_way_compare":
                continue
            path = operation.get("path")
            if path in ("composition", "multi_select_value"):
                result = case["expected_result"]
                self.assertIs(result.get("atomic"), True, case_id)
                if "merged_arrays" in result:
                    self.assertIs(result["merged_arrays"], False, case_id)

    def test_keep_local_advances_baseline_to_r(self):
        cases = [
            case
            for _, case in self.cases.values()
            if case["initial_state"].get("chosen") == "keep_local"
        ]
        self.assertGreaterEqual(len(cases), 1, "no keep-local resolution case in corpus")
        for case in cases:
            self.assertEqual(
                case["expected_result"].get("new_baseline_is"),
                "R",
                f"{case['id']}: after keep local the new baseline must be R, not L",
            )

    def test_manifest_required_categories_are_covered(self):
        coverage = self.manifest["coverage"]
        for category in self.manifest["required_categories"]:
            value = coverage.get(category)
            self.assertIsNotNone(value, f"required category {category} missing")
            ids = list(_coverage_ids({category: value}))
            self.assertGreaterEqual(len(ids), 1, f"required category {category} empty")
            for case_id in ids:
                self.assertIn(case_id, self.cases, f"{category}: unknown case {case_id}")

    def test_manifest_covers_every_case_and_only_existing_cases(self):
        coverage = self.manifest["coverage"]
        referenced = set()
        for case_id in _coverage_ids(coverage):
            self.assertIn(case_id, self.cases, f"manifest references unknown case {case_id}")
            referenced.add(case_id)
        missing = set(self.cases) - referenced
        self.assertEqual(missing, set(), f"cases not covered by manifest: {sorted(missing)}")

    def test_manifest_corpus_files_all_exist(self):
        for name in self.manifest["corpus_files"]:
            self.assertIn(name, self.documents, name)
            self.assertTrue((FIXTURE_ROOT / name).is_file(), name)

    def test_corpus_is_implementation_independent(self):
        # The test environment must never have loaded application modules.
        loaded = {module for module in __import__("sys").modules}
        application_prefixes = ("assets", "itambox", "extras", "organization")
        self.assertFalse(
            any(
                module.split(".", 1)[0] in application_prefixes
                for module in loaded
            ),
            f"application modules were loaded: {sorted(loaded & {'assets', 'itambox', 'extras', 'organization'})}",
        )


if __name__ == "__main__":
    unittest.main()