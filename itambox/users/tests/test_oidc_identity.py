from io import StringIO
from unittest.mock import patch

from django.core import management
from django.core.exceptions import ValidationError
from django.core.management import CommandError
from django.db import IntegrityError
from django.test import TestCase, override_settings

from core.auth.oidc import TenantOIDCBackend
from core.management.commands.bind_oidc_identity import Command
from core.managers import set_current_tenant
from core.models import ObjectChange
from core.oidc_identity import (
    OIDC_ISSUER_MAX_LENGTH,
    OIDC_SUBJECT_MAX_LENGTH,
    oidc_advisory_lock_parts,
    oidc_identity_bytes,
)
from core.tasks.context import TaskContext
from organization.models import Tenant
from users.models import OIDCIdentity, User

OIDC_SETTINGS = {
    "tenant-alpha": {
        "OIDC_OP_ISSUER": "https://idp.example/issuer",
        "OIDC_RP_CLIENT_ID": "client-alpha",
        "OIDC_RP_CLIENT_SECRET": "secret-alpha",
        "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://idp.example/authorize",
        "OIDC_OP_TOKEN_ENDPOINT": "https://idp.example/token",
        "OIDC_OP_USER_ENDPOINT": "https://idp.example/userinfo",
        "OIDC_OP_JWKS_ENDPOINT": "https://idp.example/jwks",
    },
    "tenant-beta": {
        "oidc_op_issuer": "https://idp.example/issuer-beta",
        "OIDC_RP_CLIENT_ID": "client-beta",
        "OIDC_RP_CLIENT_SECRET": "secret-beta",
        "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://idp.example/authorize-beta",
        "OIDC_OP_TOKEN_ENDPOINT": "https://idp.example/token-beta",
        "OIDC_OP_USER_ENDPOINT": "https://idp.example/userinfo-beta",
        "OIDC_OP_JWKS_ENDPOINT": "https://idp.example/jwks-beta",
    },
}
GLOBAL_OIDC_SETTINGS = {
    "OIDC_OP_ISSUER": "https://global.example/issuer",
    "OIDC_RP_CLIENT_ID": "global-client",
    "OIDC_RP_CLIENT_SECRET": "global-secret",
    "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://global.example/authorize",
    "OIDC_OP_TOKEN_ENDPOINT": "https://global.example/token",
    "OIDC_OP_USER_ENDPOINT": "https://global.example/userinfo",
    "OIDC_OP_JWKS_ENDPOINT": "https://global.example/jwks",
}


class OIDCIdentityModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="oidc-model-user")

    def test_identity_model_is_users_owned_and_has_canonical_fields(self):
        field_names = {field.name for field in OIDCIdentity._meta.get_fields() if field.concrete}

        self.assertEqual(field_names, {"id", "user", "issuer", "subject"})
        self.assertEqual(OIDCIdentity._meta.get_field("user").remote_field.related_name, "oidc_identities")
        self.assertEqual(OIDCIdentity._meta.get_field("issuer").max_length, OIDC_ISSUER_MAX_LENGTH)
        self.assertEqual(OIDCIdentity._meta.get_field("subject").max_length, OIDC_SUBJECT_MAX_LENGTH)
        self.assertTrue(OIDCIdentity.changelog_global)
        self.assertEqual(OIDCIdentity._change_logging_excluded_fields, ["issuer", "subject"])

        constraints = [
            constraint
            for constraint in OIDCIdentity._meta.constraints
            if constraint.name == "users_oidcidentity_unique_issuer_subject"
        ]
        self.assertEqual(len(constraints), 1)
        self.assertEqual(constraints[0].fields, ("issuer", "subject"))
        self.assertFalse(
            any(index.fields == ["issuer", "subject"] for index in OIDCIdentity._meta.indexes),
            "the stable unique constraint already supplies the exact lookup index",
        )

    def test_exact_pair_is_case_sensitive_and_pairwise_across_issuers(self):
        rows = [
            ("https://idp.example/issuer", "Subject-A"),
            ("https://idp.example/issuer", "subject-a"),
            ("https://idp.example/issuer", "Subject-B"),
            ("https://other.example/issuer", "Subject-A"),
        ]

        for issuer, subject in rows:
            OIDCIdentity.objects.create(user=self.user, issuer=issuer, subject=subject)

        self.assertEqual(OIDCIdentity.objects.count(), len(rows))
        for issuer, subject in rows:
            self.assertEqual(
                OIDCIdentity.objects.get(issuer=issuer, subject=subject).user_id,
                self.user.pk,
            )

        with self.assertRaises(IntegrityError):
            OIDCIdentity.objects.create(
                user=self.user,
                issuer=rows[0][0],
                subject=rows[0][1],
            )

    def test_exact_values_are_stored_without_normalization(self):
        issuer = "HTTPS://IdP.example:443/issuer/"
        subject = "Case-Sensitive_Subject"
        identity = OIDCIdentity.objects.create(user=self.user, issuer=issuer, subject=subject)

        identity.refresh_from_db()
        self.assertEqual(identity.issuer, issuer)
        self.assertEqual(identity.subject, subject)
        self.assertEqual(str(identity), f"OIDC identity binding for User #{self.user.pk}")

    def test_invalid_values_fail_model_validation_before_writes(self):
        invalid_values = [
            ("https://idp.example/issuer", ""),
            ("https://idp.example/issuer", "über"),
            ("https://idp.example/issuer", "x" * 256),
            ("", "valid-subject"),
            ("i" * (OIDC_ISSUER_MAX_LENGTH + 1), "valid-subject"),
        ]
        for issuer, subject in invalid_values:
            with self.subTest(issuer_length=len(issuer), subject_length=len(subject)):
                identity = OIDCIdentity(user=self.user, issuer=issuer, subject=subject)
                with self.assertRaises(ValidationError):
                    identity.full_clean()
        self.assertEqual(OIDCIdentity.objects.count(), 0)

    def test_user_profile_and_username_changes_do_not_mutate_binding(self):
        identity = OIDCIdentity.objects.create(
            user=self.user,
            issuer="https://idp.example/issuer",
            subject="immutable-subject",
        )

        self.user.username = "renamed-user"
        self.user.email = "changed@example.test"
        self.user.save(update_fields=["username", "email"])

        identity.refresh_from_db()
        self.assertEqual(identity.user_id, self.user.pk)
        self.assertEqual(identity.issuer, "https://idp.example/issuer")
        self.assertEqual(identity.subject, "immutable-subject")

    def test_binding_creation_is_global_and_redacted_in_audit(self):
        issuer = "https://audit.example/issuer"
        subject = "audit-subject"

        with TaskContext(operation="oidc.identity.bind"):
            identity = OIDCIdentity.objects.create(
                user=self.user,
                issuer=issuer,
                subject=subject,
            )

        change = ObjectChange._base_manager.get(
            changed_object_id=identity.pk,
            changed_object_type__app_label="users",
            changed_object_type__model="oidcidentity",
        )
        self.assertEqual(change.action, "create")
        self.assertIsNone(change.user_id)
        self.assertIsNone(change.tenant_id)
        self.assertEqual(change.user_name, "System")
        self.assertEqual(change.object_repr, f"OIDC identity binding for User #{self.user.pk}")
        self.assertNotIn(issuer, repr(change.postchange_data))
        self.assertNotIn(subject, repr(change.postchange_data))
        self.assertNotIn(issuer, change.object_repr)
        self.assertNotIn(subject, change.object_repr)

    def test_user_delete_cascades_identity_rows(self):
        OIDCIdentity.objects.create(
            user=self.user,
            issuer="https://idp.example/issuer",
            subject="cascade-subject",
        )

        self.user.delete()

        self.assertFalse(OIDCIdentity.objects.exists())

    def test_lock_key_uses_length_framed_exact_identity_bytes(self):
        self.assertNotEqual(
            oidc_identity_bytes("ab", "c"),
            oidc_identity_bytes("a", "bc"),
        )
        self.assertEqual(
            len(oidc_advisory_lock_parts("https://idp.example/issuer", "subject")),
            2,
        )


@override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=OIDC_SETTINGS, **GLOBAL_OIDC_SETTINGS)
class OIDCIdentityCommandTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="command-target",
            email="command-target@example.test",
        )
        self.other_user = User.objects.create_user(username="command-other")
        self.operator = User.objects.create_user(username="command-operator")
        self.tenant_alpha = Tenant.objects.create(name="Alpha", slug="tenant-alpha")
        self.tenant_beta = Tenant.objects.create(name="Beta", slug="tenant-beta")

    def call_bind(self, *args):
        output = StringIO()
        management.call_command("bind_oidc_identity", *args, stdout=output, stderr=output)
        return output.getvalue()

    def test_dry_run_is_zero_write_and_does_not_echo_identity(self):
        issuer = "https://idp.example/issuer"
        subject = "dry-run-subject"
        output = self.call_bind(
            "--user",
            str(self.user.pk),
            "--issuer",
            issuer,
            "--subject",
            subject,
            "--dry-run",
        )

        self.assertEqual(OIDCIdentity.objects.count(), 0)
        self.assertIn(f"User #{self.user.pk}", output)
        for secret in (issuer, subject, self.user.email, self.user.username):
            self.assertNotIn(secret, output)

    def test_effective_configured_issuer_set_accepts_live_tenant_and_global_values(self):
        for issuer, subject in (
            ("https://idp.example/issuer", "tenant-subject"),
            ("https://idp.example/issuer-beta", "tenant-subject-beta"),
            ("https://global.example/issuer", "global-subject"),
        ):
            with self.subTest(issuer=issuer):
                self.call_bind(
                    "--user",
                    str(self.user.pk),
                    "--issuer",
                    issuer,
                    "--subject",
                    subject,
                    "--confirm",
                    "--operator",
                    str(self.operator.pk),
                )
        self.assertEqual(OIDCIdentity.objects.count(), 3)

    def test_usable_global_is_accepted_when_only_non_string_tenant_keys_exist(self):
        with self.settings(
            ITAMBOX_TENANT_OIDC_CONFIGS={42: OIDC_SETTINGS["tenant-alpha"]},
            **GLOBAL_OIDC_SETTINGS,
        ):
            output = self.call_bind(
                "--user",
                str(self.user.pk),
                "--issuer",
                "https://global.example/issuer",
                "--subject",
                "global-malformed-map-subject",
                "--confirm",
            )

        self.assertIn("Created", output)
        self.assertEqual(OIDCIdentity.objects.count(), 1)

    def test_both_issuer_spellings_use_only_runtime_effective_first_value(self):
        configs = {
            "tenant-alpha": {
                **OIDC_SETTINGS["tenant-alpha"],
                "OIDC_OP_ISSUER": "https://upper.example/issuer",
                "oidc_op_issuer": "https://lower.example/issuer",
            }
        }
        with self.settings(ITAMBOX_TENANT_OIDC_CONFIGS=configs, **GLOBAL_OIDC_SETTINGS):
            issuers = Command._configured_issuers()

        self.assertEqual(issuers, {"https://upper.example/issuer", "https://global.example/issuer"})

    def test_disabled_ghost_and_incomplete_tenant_configs_are_not_issuer_sources(self):
        Tenant.objects.create(name="Disabled", slug="tenant-disabled")
        Tenant.objects.create(name="Incomplete", slug="tenant-incomplete")
        configs = {
            "tenant-alpha": OIDC_SETTINGS["tenant-alpha"],
            "tenant-disabled": {
                **OIDC_SETTINGS["tenant-alpha"],
                "OIDC_OP_ISSUER": "https://disabled.example/issuer",
                "enabled": False,
            },
            "tenant-incomplete": {
                **OIDC_SETTINGS["tenant-alpha"],
                "OIDC_OP_ISSUER": "https://incomplete.example/issuer",
                "OIDC_OP_TOKEN_ENDPOINT": "",
            },
            "tenant-ghost": {
                **OIDC_SETTINGS["tenant-alpha"],
                "OIDC_OP_ISSUER": "https://ghost.example/issuer",
            },
        }
        with self.settings(ITAMBOX_TENANT_OIDC_CONFIGS=configs, **GLOBAL_OIDC_SETTINGS):
            issuers = Command._configured_issuers()

        self.assertEqual(issuers, {"https://idp.example/issuer", "https://global.example/issuer"})

    def test_unusable_global_is_not_an_issuer_source(self):
        unusable_global = {**GLOBAL_OIDC_SETTINGS, "OIDC_OP_ISSUER": ""}
        with self.settings(ITAMBOX_TENANT_OIDC_CONFIGS=OIDC_SETTINGS, **unusable_global):
            issuers = Command._configured_issuers()
            with self.assertRaises(CommandError):
                self.call_bind(
                    "--user",
                    str(self.user.pk),
                    "--issuer",
                    "https://global.example/issuer",
                    "--subject",
                    "unusable-global-subject",
                    "--dry-run",
                )

        self.assertEqual(issuers, {"https://idp.example/issuer", "https://idp.example/issuer-beta"})
        self.assertEqual(OIDCIdentity.objects.count(), 0)

    def test_unknown_or_normalized_issuer_is_rejected_without_write(self):
        for issuer in (
            "https://unknown.example/issuer",
            "https://idp.example/issuer/",
            "HTTPS://idp.example/issuer",
        ):
            with self.subTest(issuer=issuer), self.assertRaises(CommandError) as caught:
                self.call_bind(
                    "--user",
                    str(self.user.pk),
                    "--issuer",
                    issuer,
                    "--subject",
                    "rejected-subject",
                    "--confirm",
                )
            self.assertNotIn(issuer, str(caught.exception))
        self.assertEqual(OIDCIdentity.objects.count(), 0)

    def test_live_binding_is_idempotent_and_audited_as_system_not_target(self):
        args = (
            "--user",
            str(self.user.pk),
            "--issuer",
            "https://idp.example/issuer",
            "--subject",
            "idempotent-subject",
            "--confirm",
            "--operator",
            str(self.operator.pk),
        )
        first = self.call_bind(*args)
        second = self.call_bind(*args)

        self.assertIn("Created", first)
        self.assertIn("already exists", second)
        self.assertEqual(OIDCIdentity.objects.filter(user=self.user).count(), 1)
        change = ObjectChange._base_manager.filter(
            changed_object_type__app_label="users",
            changed_object_type__model="oidcidentity",
            changed_object_id=OIDCIdentity.objects.get(user=self.user).pk,
        ).get()
        self.assertEqual(change.user_id, self.operator.pk)
        self.assertNotEqual(change.user_id, self.user.pk)

    def test_conflicting_user_never_reassigns_binding(self):
        identity = OIDCIdentity.objects.create(
            user=self.other_user,
            issuer="https://idp.example/issuer",
            subject="conflict-subject",
        )

        with self.assertRaises(CommandError):
            self.call_bind(
                "--user",
                str(self.user.pk),
                "--issuer",
                "https://idp.example/issuer",
                "--subject",
                "conflict-subject",
                "--confirm",
            )

        identity.refresh_from_db()
        self.assertEqual(identity.user_id, self.other_user.pk)
        self.assertEqual(OIDCIdentity.objects.count(), 1)

    def test_one_user_may_have_multiple_exact_identities(self):
        for subject in ("subject-a", "subject-b"):
            self.call_bind(
                "--user",
                str(self.user.pk),
                "--issuer",
                "https://idp.example/issuer",
                "--subject",
                subject,
                "--confirm",
            )

        self.assertEqual(OIDCIdentity.objects.filter(user=self.user).count(), 2)

    def test_nonexistent_target_operator_and_malformed_input_fail_before_writes(self):
        valid = (
            "--issuer",
            "https://idp.example/issuer",
            "--subject",
            "command-negative-subject",
            "--confirm",
        )
        with self.assertRaises(CommandError):
            self.call_bind("--user", "999999", *valid)
        with self.assertRaises(CommandError):
            self.call_bind("--user", str(self.user.pk), *valid, "--operator", "999999")
        for subject in ("", "über", "x" * 256):
            with self.subTest(subject_type=type(subject).__name__, subject_length=len(subject)):
                with self.assertRaises(CommandError):
                    self.call_bind(
                        "--user",
                        str(self.user.pk),
                        "--issuer",
                        "https://idp.example/issuer",
                        "--subject",
                        subject,
                        "--confirm",
                    )
        self.assertEqual(OIDCIdentity.objects.count(), 0)

    def test_dry_run_existing_and_conflicting_binding_are_read_only(self):
        existing = OIDCIdentity.objects.create(
            user=self.user,
            issuer="https://idp.example/issuer",
            subject="existing-dry-run-subject",
        )
        output = self.call_bind(
            "--user",
            str(self.user.pk),
            "--issuer",
            existing.issuer,
            "--subject",
            existing.subject,
            "--dry-run",
        )
        self.assertIn("already exists", output)
        conflicting = OIDCIdentity.objects.create(
            user=self.other_user,
            issuer="https://idp.example/issuer",
            subject="conflicting-dry-run-subject",
        )
        with self.assertRaises(CommandError):
            self.call_bind(
                "--user",
                str(self.user.pk),
                "--issuer",
                conflicting.issuer,
                "--subject",
                conflicting.subject,
                "--dry-run",
            )
        self.assertEqual(OIDCIdentity.objects.count(), 2)

    def test_command_created_binding_authorizes_next_canonical_login(self):
        subject = "command-to-login-subject"
        self.call_bind(
            "--user",
            str(self.user.pk),
            "--issuer",
            "https://idp.example/issuer",
            "--subject",
            subject,
            "--confirm",
        )
        set_current_tenant(self.tenant_alpha)
        backend = TenantOIDCBackend()
        with (
            patch.object(
                backend,
                "get_userinfo",
                return_value={
                    "iss": "https://idp.example/issuer",
                    "sub": subject,
                    "email": "changed-after-command@example.test",
                },
            ),
            patch.object(backend, "verify_claims", return_value=True),
        ):
            resolved = backend.get_or_create_user(
                "access-token",
                "id-token",
                {"iss": "https://idp.example/issuer", "sub": subject},
            )
        set_current_tenant(None)
        self.assertEqual(resolved.pk, self.user.pk)
        self.assertEqual(OIDCIdentity.objects.filter(issuer="https://idp.example/issuer", subject=subject).count(), 1)
