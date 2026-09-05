"""Database-free tests for the signed specification preview-token kernel."""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path

from django.core import signing

_MODULE_PATH = Path(__file__).resolve().parents[1] / "services" / "specifications" / "preview_tokens.py"
_MODULE_SPEC = importlib.util.spec_from_file_location("_issue479_preview_tokens_unit", _MODULE_PATH)
if _MODULE_SPEC is None or _MODULE_SPEC.loader is None:
    raise RuntimeError("could not load preview-token kernel")
_preview_tokens = importlib.util.module_from_spec(_MODULE_SPEC)
sys.modules[_MODULE_SPEC.name] = _preview_tokens
_MODULE_SPEC.loader.exec_module(_preview_tokens)

MAX_PREVIEW_TOKEN_LENGTH = _preview_tokens.MAX_PREVIEW_TOKEN_LENGTH
PREVIEW_TOKEN_SALT = _preview_tokens.PREVIEW_TOKEN_SALT
OwnerRef = _preview_tokens.OwnerRef
PreviewTokenError = _preview_tokens.PreviewTokenError
PreviewTokenExpectation = _preview_tokens.PreviewTokenExpectation
issue_preview_token = _preview_tokens.issue_preview_token
normalized_input_digest = _preview_tokens.normalized_input_digest
verify_preview_token = _preview_tokens.verify_preview_token

TEST_KEY = hashlib.sha256(b"T10 preview token unit test key").hexdigest()
TEST_NOW = 1_700_000_000


class PreviewTokenKernelTests(unittest.TestCase):
    def _expectation(self) -> PreviewTokenExpectation:
        return PreviewTokenExpectation(
            actor_id=41,
            authentication_revision="authentication-revision-1",
            access_scope_fingerprint="authorized-scope-fingerprint-1",
            command_kind="update_asset_specifications",
            target=OwnerRef(owner_kind="asset", owner_id=9001),
            normalized_input_digest=normalized_input_digest(
                {
                    "fieldsets": {"presence": "explicit", "identities": ["itambox/compute"]},
                    "patch": {"clear": [], "set": {"memory_capacity": "32.000"}},
                }
            ),
            expected_resource_revision="resource-revision-1",
            expected_definition_revision="definition-revision-1",
            expected_category_default_snapshot_revision="category-default-revision-1",
            historical_state_digest="historical-state-digest-1",
        )

    def _issue(self, expectation: PreviewTokenExpectation | None = None, *, now: int = TEST_NOW) -> str:
        return issue_preview_token(expectation or self._expectation(), key=TEST_KEY, now=now)

    def _assert_stale(self, token: str, expected: PreviewTokenExpectation | None = None, *, now: int = TEST_NOW):
        with self.assertRaises(PreviewTokenError) as raised:
            verify_preview_token(
                token,
                expected=expected or self._expectation(),
                key=TEST_KEY,
                now=now,
            )
        self.assertEqual(raised.exception.code, "STALE_PLAN")

    def test_unchanged_token_roundtrip_is_stateless_and_has_fixed_lifetime(self):
        expected = self._expectation()
        token = self._issue(expected)

        actual = verify_preview_token(token, expected=expected, key=TEST_KEY, now=TEST_NOW)

        self.assertEqual(actual.binding(), expected)
        self.assertEqual(actual.issued_at_epoch_seconds, TEST_NOW)
        self.assertEqual(actual.expires_at_epoch_seconds, TEST_NOW + 1800)
        self.assertLess(len(token), MAX_PREVIEW_TOKEN_LENGTH)

    def test_tampered_signature_and_payload_are_rejected(self):
        token = self._issue()
        payload, separator, signature = token.rpartition(":")
        self.assertEqual(separator, ":")
        tampered_signature = f"{payload}:{('A' if signature[0] != 'A' else 'B')}{signature[1:]}"
        self._assert_stale(tampered_signature)

        tampered_payload = f"{('A' if payload[0] != 'A' else 'B')}{payload[1:]}:{signature}"
        self._assert_stale(tampered_payload)

    def test_wrong_key_salt_and_purpose_are_rejected(self):
        token = self._issue()
        with self.assertRaises(PreviewTokenError) as raised:
            verify_preview_token(token, expected=self._expectation(), key="different-key", now=TEST_NOW)
        self.assertEqual(raised.exception.code, "STALE_PLAN")

        payload = signing.Signer(key=TEST_KEY, salt=PREVIEW_TOKEN_SALT, fallback_keys=()).unsign_object(token)
        wrong_salt_token = signing.Signer(
            key=TEST_KEY,
            salt="wrong.preview-token-purpose",
            fallback_keys=(),
        ).sign_object(payload)
        self._assert_stale(wrong_salt_token)

        payload["purpose"] = "wrong.preview-token-purpose"
        wrong_purpose_token = signing.Signer(
            key=TEST_KEY,
            salt=PREVIEW_TOKEN_SALT,
            fallback_keys=(),
        ).sign_object(payload)
        self._assert_stale(wrong_purpose_token)

    def test_malformed_and_oversized_tokens_are_rejected(self):
        for token in ("", "not-a-signed-token", "x" * (MAX_PREVIEW_TOKEN_LENGTH + 1)):
            with self.subTest(token_length=len(token)):
                self._assert_stale(token)

    def test_compressed_tokens_are_outside_the_emitted_format(self):
        signer = signing.Signer(key=TEST_KEY, salt=PREVIEW_TOKEN_SALT, fallback_keys=())
        payload = signer.unsign_object(self._issue())
        compressed = signer.sign_object(payload, compress=True)
        self.assertTrue(compressed.startswith("."))
        self._assert_stale(compressed)

    def test_expired_and_not_yet_valid_tokens_are_rejected_without_sleeping(self):
        token = self._issue(now=1000)

        self._assert_stale(token, now=999)
        self._assert_stale(token, now=2800)
        self.assertEqual(
            verify_preview_token(token, expected=self._expectation(), key=TEST_KEY, now=1000).issued_at_epoch_seconds,
            1000,
        )
        self.assertEqual(
            verify_preview_token(token, expected=self._expectation(), key=TEST_KEY, now=2799).expires_at_epoch_seconds,
            2800,
        )

    def test_each_binding_claim_mismatch_is_rejected(self):
        expected = self._expectation()
        token = self._issue(expected)
        mismatches = {
            "actor": replace(expected, actor_id=42),
            "authentication": replace(expected, authentication_revision="authentication-revision-2"),
            "scope": replace(expected, access_scope_fingerprint="authorized-scope-fingerprint-2"),
            "command": replace(expected, command_kind="cleanup_asset_specification_history"),
            "target": replace(expected, target=OwnerRef(owner_kind="asset_type", owner_id=9001)),
            "normalized_input": replace(
                expected,
                normalized_input_digest=normalized_input_digest({"different": "normalized input"}),
            ),
            "resource_revision": replace(expected, expected_resource_revision="resource-revision-2"),
            "definition_revision": replace(expected, expected_definition_revision="definition-revision-2"),
            "category_default_snapshot": replace(
                expected,
                expected_category_default_snapshot_revision="category-default-revision-2",
            ),
            "historical_state": replace(expected, historical_state_digest="historical-state-digest-2"),
        }

        for name, mismatch in mismatches.items():
            with self.subTest(claim=name):
                self._assert_stale(token, mismatch)

    def test_normalized_input_is_order_independent_but_omitted_and_explicit_empty_differ(self):
        reordered = normalized_input_digest(
            {
                "patch": {"set": {"memory_capacity": "32.000"}, "clear": []},
                "fieldsets": {"identities": ["itambox/compute"], "presence": "explicit"},
            }
        )
        original = self._expectation().normalized_input_digest
        self.assertEqual(reordered, original)

        omitted = {"fieldsets": {"presence": "omitted", "identities": []}}
        explicit_empty = {"fieldsets": {"presence": "explicit", "identities": []}}
        omitted_digest = normalized_input_digest(omitted)
        explicit_empty_digest = normalized_input_digest(explicit_empty)
        self.assertNotEqual(omitted_digest, explicit_empty_digest)

        omitted_expected = replace(self._expectation(), normalized_input_digest=omitted_digest)
        explicit_empty_expected = replace(self._expectation(), normalized_input_digest=explicit_empty_digest)
        self._assert_stale(self._issue(omitted_expected), explicit_empty_expected)

    def test_requested_and_authorized_scope_binding_is_not_interchangeable(self):
        tenant_scope = replace(self._expectation(), access_scope_fingerprint="selector-tenant-7-authorized-tenant-7")
        group_scope_same_tenants = replace(
            self._expectation(), access_scope_fingerprint="selector-group-3-authorized-tenant-7"
        )

        self._assert_stale(self._issue(tenant_scope), group_scope_same_tenants)

    def test_category_default_snapshot_change_is_bound_even_when_definition_is_unchanged(self):
        expected = self._expectation()
        changed_snapshot = replace(
            expected,
            expected_category_default_snapshot_revision="category-default-revision-2",
        )
        self.assertEqual(expected.expected_definition_revision, "definition-revision-1")
        self.assertEqual(changed_snapshot.expected_definition_revision, expected.expected_definition_revision)
        self._assert_stale(self._issue(expected), changed_snapshot)

    def test_signed_payload_schema_rejects_missing_claims(self):
        token = self._issue()
        payload = signing.Signer(key=TEST_KEY, salt=PREVIEW_TOKEN_SALT, fallback_keys=()).unsign_object(token)
        del payload["claims"]["historical_state_digest"]
        malformed_schema_token = signing.Signer(
            key=TEST_KEY,
            salt=PREVIEW_TOKEN_SALT,
            fallback_keys=(),
        ).sign_object(payload)

        self._assert_stale(malformed_schema_token)

    def test_normalized_input_and_tokens_are_bounded(self):
        with self.assertRaises(ValueError):
            normalized_input_digest({"too_long": "x" * 5000})
        self._assert_stale("x" * (MAX_PREVIEW_TOKEN_LENGTH + 1))

    def test_digest_accepts_full_effective_field_text_envelope(self):
        normalized = {
            "patch": {
                "set": [{"field_key": f"field_{index}", "value": "🧪" * 4096} for index in range(512)],
                "clear": [],
            }
        }
        self.assertEqual(len(normalized_input_digest(normalized)), 64)

    def test_digest_accepts_full_effective_field_multiselect_envelope(self):
        normalized = {
            "patch": {
                "set": [
                    {"field_key": f"field_{index}", "value": [f"choice_{choice:02}" for choice in range(64)]}
                    for index in range(512)
                ],
                "clear": [],
            }
        }
        self.assertEqual(len(normalized_input_digest(normalized)), 64)

    def test_key_is_required_and_never_uses_a_test_or_settings_fallback(self):
        with self.assertRaises(TypeError):
            issue_preview_token(self._expectation(), now=TEST_NOW)  # type: ignore[call-arg]
        with self.assertRaises(ValueError):
            issue_preview_token(self._expectation(), key="", now=TEST_NOW)

    def test_invalid_token_failure_does_not_log_or_echo_credentials(self):
        token = self._issue()
        with self.assertNoLogs("assets.services.specifications.preview_tokens", level="DEBUG"):
            with self.assertRaises(PreviewTokenError) as raised:
                verify_preview_token("not-a-token", expected=self._expectation(), key=TEST_KEY, now=TEST_NOW)

        self.assertNotIn(TEST_KEY, str(raised.exception))
        self.assertNotIn(token, str(raised.exception))

    def test_collection_does_not_replace_the_canonical_module(self):
        probe = (
            "import runpy, sys, types; "
            "name = 'assets.services.specifications.preview_tokens'; "
            "sentinel = types.ModuleType(name); sys.modules[name] = sentinel; "
            "runpy.run_path(sys.argv[1], run_name='preview_token_collection_probe'); "
            "assert sys.modules[name] is sentinel, 'collection replaced the canonical module'"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", probe, str(Path(__file__).resolve())],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_import_does_not_load_orm_or_bootstrap_a_database(self):
        repository_root = Path(__file__).resolve().parents[2]
        probe = (
            "import importlib.util, sys; "
            "sys.path.insert(0, sys.argv[1]); "
            "path = sys.argv[2]; "
            "spec = importlib.util.spec_from_file_location('assets.services.specifications.preview_tokens', path); "
            "module = importlib.util.module_from_spec(spec); "
            "sys.modules[spec.name] = module; "
            "spec.loader.exec_module(module); "
            "blocked = {'django.db', 'django.db.models', 'assets.models', 'organization.models', 'core.models'}; "
            "loaded = {name for name in sys.modules if name in blocked or name.startswith('django.db.')}; "
            "assert not loaded, sorted(loaded); "
            "print('orm-free')"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", probe, str(repository_root), str(_MODULE_PATH)],
            check=True,
            capture_output=True,
            text=True,
            cwd=repository_root,
        )
        self.assertEqual(result.stdout.strip(), "orm-free")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
