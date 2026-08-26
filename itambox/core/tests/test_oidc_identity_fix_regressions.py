from unittest.mock import Mock, patch

from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase, override_settings
from django.utils.module_loading import import_string

from core import identity_provisioning
from core.auth.oidc import (
    OIDCIdentityBindingRequiredError,
    OIDCIdentityProvisioningError,
    OIDCTokenValidationError,
    TenantOIDCBackend,
)
from core.management.commands.bind_oidc_identity import Command
from core.models import ObjectChange
from core.oidc_identity import oidc_sensitive_audit, oidc_sensitive_audit_enabled
from core.tasks.context import TaskContext
from extras.models import Event
from organization.models import AssetHolder, Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.services.identity_provisioning import organization_identity_provisioner
from users.models import OIDCIdentity, User

GLOBAL_OIDC_SETTINGS = {
    "OIDC_OP_ISSUER": "https://global.example/issuer",
    "OIDC_RP_CLIENT_ID": "global-client",
    "OIDC_RP_CLIENT_SECRET": "global-secret",
    "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://global.example/authorize",
    "OIDC_OP_TOKEN_ENDPOINT": "https://global.example/token",
    "OIDC_OP_USER_ENDPOINT": "https://global.example/userinfo",
    "OIDC_OP_JWKS_ENDPOINT": "https://global.example/jwks",
}


class OIDCIdentityAuditRegressionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Audit Tenant", slug="audit-tenant")
        self.actor = User.objects.create_superuser(username="audit-actor", email="audit-actor@example.test")
        self.target = User.objects.create_user(
            username="old-audit-user",
            email="old-audit@example.test",
            first_name="Old",
            last_name="Name",
        )

    def test_oidc_profile_only_update_emits_one_safe_truthful_audit_event(self):
        before = ObjectChange._base_manager.filter(
            changed_object_type__app_label="users",
            changed_object_type__model="user",
            changed_object_id=self.target.pk,
        ).count()

        with TaskContext(tenant_id=self.tenant.pk, user_id=self.actor.pk, operation="oidc.profile.sync"):
            with oidc_sensitive_audit():
                self.target.email = "new-audit@example.test"
                self.target.first_name = "New"
                self.target.last_name = "Profile"
                self.target.username = "new-audit-user"
                self.target.save(update_fields=["email", "first_name", "last_name", "username"])

        changes = list(
            ObjectChange._base_manager.filter(
                changed_object_type__app_label="users",
                changed_object_type__model="user",
                changed_object_id=self.target.pk,
            ).order_by("pk")
        )
        self.assertEqual(len(changes), before + 1)
        change = changes[-1]
        self.assertEqual(change.action, "update")
        self.assertEqual(change.user_id, self.actor.pk)
        self.assertEqual(change.tenant_id, self.tenant.pk)
        self.assertEqual(change.object_repr, f"users.user #{self.target.pk}")
        self.assertEqual(change.prechange_data, {"reason_code": "oidc_profile_sync", "changed_fields": []})
        self.assertEqual(
            change.postchange_data,
            {
                "reason_code": "oidc_profile_sync",
                "changed_fields": ["email", "first_name", "last_name", "username"],
            },
        )
        rendered = repr(change.prechange_data) + repr(change.postchange_data) + change.object_repr
        for secret in ("new-audit@example.test", "new-audit-user", "New", "Profile"):
            self.assertNotIn(secret, rendered)

    def test_nested_oidc_organization_events_keep_actor_and_redact_profile_values(self):
        role = Role.objects.create(tenant=self.tenant, name="Nested Member", permissions=[])
        with TaskContext(tenant_id=self.tenant.pk, user_id=self.actor.pk, operation="oidc.organization.sync"):
            with oidc_sensitive_audit():
                holder = AssetHolder.objects.create(
                    user=self.target,
                    first_name="Nested",
                    last_name="Person",
                    upn="nested-upn@example.test",
                    email="nested@example.test",
                    tenant=self.tenant,
                )
                membership = Membership.objects.create(user=self.target, tenant=self.tenant)
                grant = RoleGrant.objects.create(membership=membership, role=role)
                scope = RoleGrantScope.objects.create(
                    role_grant=grant,
                    scope_type=RoleGrantScope.SCOPE_OWN,
                )

        expected = (
            ("assetholder", holder.pk, "organization.assetholder"),
            ("membership", membership.pk, "organization.membership"),
            ("rolegrant", grant.pk, "organization.rolegrant"),
            ("rolegrantscope", scope.pk, "organization.rolegrantscope"),
        )
        for model, pk, label in expected:
            with self.subTest(model=model):
                change = ObjectChange._base_manager.get(
                    changed_object_type__app_label=label.split(".")[0],
                    changed_object_type__model=label.split(".")[1],
                    changed_object_id=pk,
                )
                self.assertEqual(change.user_id, self.actor.pk)
                self.assertEqual(change.object_repr, f"{label} #{pk}")
                rendered = repr(change.prechange_data) + repr(change.postchange_data) + change.object_repr
                for secret in ("nested-upn@example.test", "nested@example.test", "Nested", "Person"):
                    self.assertNotIn(secret, rendered)

    def test_oidc_user_delete_discards_stale_unredacted_snapshot(self):
        self.target._prechange_snapshot = {"email": "stale-delete@example.test", "username": "stale-delete"}
        with TaskContext(tenant_id=self.tenant.pk, user_id=self.actor.pk, operation="oidc.profile.delete"):
            with oidc_sensitive_audit():
                target_pk = self.target.pk
                self.target.delete()

        change = ObjectChange._base_manager.get(
            changed_object_type__app_label="users",
            changed_object_type__model="user",
            changed_object_id=target_pk,
        )
        self.assertEqual(change.action, "delete")
        self.assertEqual(change.user_id, self.actor.pk)
        rendered = repr(change.prechange_data) + repr(change.postchange_data) + change.object_repr
        self.assertNotIn("stale-delete@example.test", rendered)
        self.assertNotIn("stale-delete", rendered)

    def test_oidc_audit_context_is_reset_after_exception(self):
        self.assertFalse(oidc_sensitive_audit_enabled())
        with self.assertRaisesRegex(RuntimeError, "expected"):
            with oidc_sensitive_audit():
                self.assertTrue(oidc_sensitive_audit_enabled())
                raise RuntimeError("expected")
        self.assertFalse(oidc_sensitive_audit_enabled())


AUDIT_PHASE_B_CONFIG = {
    "audit-tenant": {
        "OIDC_OP_ISSUER": "https://audit.example/issuer",
        "OIDC_RP_CLIENT_ID": "audit-client",
        "OIDC_RP_CLIENT_SECRET": "audit-secret",
        "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://audit.example/authorize",
        "OIDC_OP_TOKEN_ENDPOINT": "https://audit.example/token",
        "OIDC_OP_USER_ENDPOINT": "https://audit.example/userinfo",
        "OIDC_OP_JWKS_ENDPOINT": "https://audit.example/jwks",
        "OIDC_GROUP_ROLE_MAPPING": {},
    }
}


class OIDCPhaseBAuditRegressionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Audit Phase B", slug="audit-tenant")
        self.actor = User.objects.create_superuser(username="phase-b-actor", email="phase-b-actor@example.test")

    @override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=AUDIT_PHASE_B_CONFIG)
    def test_phase_b_audits_user_and_nested_access_rows_with_truthful_actor(self):
        backend = TenantOIDCBackend()
        claims = {
            "iss": "https://audit.example/issuer",
            "sub": "phase-b-audit-subject",
            "email": "phase-b-audit@example.test",
            "given_name": "Phase",
            "family_name": "Audit",
            "groups": [],
        }
        with TaskContext(tenant_id=self.tenant.pk, user_id=self.actor.pk, operation="oidc.phase_b.audit"):
            with (
                patch.object(backend, "get_userinfo", return_value=claims),
                patch.object(backend, "verify_claims", return_value=True),
            ):
                with identity_provisioning.override_identity_provisioner(organization_identity_provisioner):
                    user = backend.get_or_create_user("access-token", "id-token", claims)

        identity = OIDCIdentity.objects.get(user=user)
        holder = AssetHolder.objects.get(user=user, tenant=self.tenant)
        membership = Membership.objects.get(user=user, tenant=self.tenant)
        grant = RoleGrant.objects.get(membership=membership)
        scope = RoleGrantScope.objects.get(role_grant=grant)
        objects = (
            ("users", "user", user.pk, self.tenant.pk),
            ("users", "oidcidentity", identity.pk, None),
            ("organization", "assetholder", holder.pk, self.tenant.pk),
            ("organization", "membership", membership.pk, self.tenant.pk),
            ("organization", "rolegrant", grant.pk, self.tenant.pk),
            ("organization", "rolegrantscope", scope.pk, self.tenant.pk),
        )
        for app_label, model, pk, tenant_id in objects:
            with self.subTest(model=model):
                changes = ObjectChange._base_manager.filter(
                    changed_object_type__app_label=app_label,
                    changed_object_type__model=model,
                    changed_object_id=pk,
                )
                self.assertEqual(changes.count(), 1)
                change = changes.get()
                self.assertEqual(change.user_id, self.actor.pk)
                self.assertEqual(change.tenant_id, tenant_id)
                rendered = repr(change.prechange_data) + repr(change.postchange_data) + change.object_repr
                for secret in (claims["email"], claims["given_name"], claims["family_name"], claims["sub"]):
                    self.assertNotIn(secret, rendered)


class OIDCIdentityUnsafeSeamRegressionTests(TestCase):
    def test_direct_filter_users_by_claims_fails_closed_without_writes(self):
        backend = TenantOIDCBackend()
        with self.assertRaises(OIDCIdentityBindingRequiredError):
            backend.filter_users_by_claims({"email": "legacy@example.test", "sub": "legacy-subject"})
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(OIDCIdentity.objects.count(), 0)

    def test_direct_create_user_fails_closed_without_writes(self):
        backend = TenantOIDCBackend()
        with self.assertRaises(OIDCIdentityBindingRequiredError):
            backend.create_user({"email": "legacy@example.test", "sub": "legacy-subject"})
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(OIDCIdentity.objects.count(), 0)

    def test_direct_update_user_fails_closed_without_profile_or_org_writes(self):
        user = User.objects.create_user(username="legacy-user", email="old@example.test")
        backend = TenantOIDCBackend()
        with self.assertRaises(OIDCIdentityBindingRequiredError):
            backend.update_user(user, {"email": "new@example.test", "sub": "legacy-subject"})
        user.refresh_from_db()
        self.assertEqual(user.email, "old@example.test")
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(OIDCIdentity.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)
        self.assertEqual(AssetHolder.objects.count(), 0)


class OIDCIdentityIssuerPrecedenceRegressionTests(TestCase):
    def setUp(self):
        Tenant.objects.create(name="Alpha", slug="tenant-alpha")

    @override_settings(
        ITAMBOX_TENANT_OIDC_CONFIGS={
            "tenant-alpha": {
                "OIDC_OP_ISSUER": "https://upper.example/issuer",
                "oidc_op_issuer": "https://lower.example/issuer",
                "OIDC_RP_CLIENT_ID": "client",
                "OIDC_RP_CLIENT_SECRET": "secret",
                "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://idp.example/authorize",
                "OIDC_OP_TOKEN_ENDPOINT": "https://idp.example/token",
                "OIDC_OP_USER_ENDPOINT": "https://idp.example/userinfo",
                "OIDC_OP_JWKS_ENDPOINT": "https://idp.example/jwks",
            }
        },
        **GLOBAL_OIDC_SETTINGS,
    )
    def test_command_accepts_effective_tenant_and_global_but_not_lower_precedence_issuer(self):
        self.assertEqual(
            Command._configured_issuers(),
            {"https://upper.example/issuer", "https://global.example/issuer"},
        )


COMPAT_CONFIG = {
    "compat-tenant": {
        "OIDC_OP_ISSUER": "https://compat.example/issuer",
        "OIDC_RP_CLIENT_ID": "compat-client",
        "OIDC_RP_CLIENT_SECRET": "compat-secret",
        "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://compat.example/authorize",
        "OIDC_OP_TOKEN_ENDPOINT": "https://compat.example/token",
        "OIDC_OP_USER_ENDPOINT": "https://compat.example/userinfo",
        "OIDC_OP_JWKS_ENDPOINT": "https://compat.example/jwks",
        "OIDC_RP_SIGN_ALGO": "RS256",
        "OIDC_GROUP_ROLE_MAPPING": {},
    }
}


class OIDCCompatibilityRegressionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Compatibility", slug="compat-tenant")
        from core.managers import set_current_tenant

        set_current_tenant(self.tenant)

    def tearDown(self):
        from core.managers import set_current_tenant

        set_current_tenant(None)

    @staticmethod
    def request():
        request = RequestFactory().get("/oidc/callback/?code=authorization-code&state=state")
        request.session = SessionStore()
        return request

    @staticmethod
    def full_fingerprint():
        return {
            "users": User.objects.count(),
            "bindings": OIDCIdentity.objects.count(),
            "holders": AssetHolder.objects.count(),
            "memberships": Membership.objects.count(),
            "roles": Role.objects.count(),
            "grants": RoleGrant.objects.count(),
            "scopes": RoleGrantScope.objects.count(),
            "changes": ObjectChange.objects.count(),
            "events": Event.objects.count(),
            "profiles": tuple(
                User._base_manager.order_by("pk").values_list("pk", "username", "email", "first_name", "last_name")
            ),
        }

    @override_settings(
        ITAMBOX_TENANT_OIDC_CONFIGS=COMPAT_CONFIG,
        OIDC_STORE_ACCESS_TOKEN=True,
        OIDC_STORE_ID_TOKEN=True,
    )
    def test_valid_authenticate_smoke_preserves_upstream_boundaries_and_profile(self):
        backend = import_string("core.auth.oidc.TenantOIDCBackend")()
        payload = {
            "aud": ["compat-client", "another-audience"],
            "azp": "compat-client",
            "iss": "https://compat.example/issuer",
            "sub": "compat-subject",
            "nonce": "expected-nonce",
        }
        backend.get_token = Mock(return_value={"access_token": "access-token", "id_token": "id-token"})
        backend.retrieve_matching_jwk = Mock(return_value="verified-jwk")
        backend.get_payload_data = Mock(return_value=payload)
        backend.get_userinfo = Mock(
            return_value={
                "email": "compat@example.test",
                "given_name": "Compat",
                "family_name": "User",
                "iss": payload["iss"],
                "sub": payload["sub"],
                "groups": [],
            }
        )
        backend.verify_claims = Mock(return_value=True)
        request = self.request()

        with identity_provisioning.override_identity_provisioner(organization_identity_provisioner):
            result = backend.authenticate(request, nonce="expected-nonce", code_verifier="pkce-verifier")

        self.assertIsNotNone(result)
        self.assertTrue(result.can_login)
        self.assertEqual(result.email, "compat@example.test")
        self.assertEqual(result.oidc_identities.get().subject, "compat-subject")
        self.assertIs(import_string("core.auth.oidc.TenantOIDCBackend"), TenantOIDCBackend)
        backend.get_token.assert_called_once()
        token_request = backend.get_token.call_args.args[0]
        self.assertEqual(token_request["code_verifier"], "pkce-verifier")
        self.assertEqual(token_request["code"], "authorization-code")
        backend.retrieve_matching_jwk.assert_called_once_with("id-token")
        backend.get_payload_data.assert_called_once_with("id-token", "verified-jwk")
        backend.get_userinfo.assert_called_once()
        self.assertEqual(request.session["oidc_access_token"], "access-token")
        self.assertEqual(request.session["oidc_id_token"], "id-token")

    @override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=COMPAT_CONFIG)
    def test_invalid_subject_shapes_fail_at_verify_and_get_or_create_boundaries(self):
        before = self.full_fingerprint()
        for subject in ("", 123, "über", "x" * 256):
            with self.subTest(
                subject_type=type(subject).__name__, subject_length=len(subject) if isinstance(subject, str) else None
            ):
                backend = TenantOIDCBackend()
                backend.get_token = Mock(return_value={"access_token": "access", "id_token": "id"})
                backend.store_tokens = Mock()
                with patch(
                    "mozilla_django_oidc.auth.OIDCAuthenticationBackend.verify_token",
                    return_value={
                        "aud": "compat-client",
                        "iss": "https://compat.example/issuer",
                        "sub": subject,
                    },
                ):
                    with self.assertRaises(OIDCTokenValidationError):
                        backend.authenticate(self.request())
                backend.store_tokens.assert_not_called()
                with self.assertRaises(OIDCTokenValidationError):
                    backend.get_or_create_user(
                        "access-token",
                        "id-token",
                        {"iss": "https://compat.example/issuer", "sub": subject},
                    )
        self.assertEqual(self.full_fingerprint(), before)


class OIDCLegacyCandidateRegressionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Legacy", slug="compat-tenant")
        from core.managers import set_current_tenant

        set_current_tenant(self.tenant)
        self.backend = TenantOIDCBackend()

    def tearDown(self):
        from core.managers import set_current_tenant

        set_current_tenant(None)

    def fingerprint(self):
        return {
            "users": User.objects.count(),
            "bindings": OIDCIdentity.objects.count(),
            "holders": AssetHolder.objects.count(),
            "memberships": Membership.objects.count(),
            "roles": Role.objects.count(),
            "grants": RoleGrant.objects.count(),
            "scopes": RoleGrantScope.objects.count(),
            "changes": ObjectChange.objects.count(),
            "events": Event.objects.count(),
            "profiles": tuple(
                User._base_manager.order_by("pk").values_list("pk", "username", "email", "first_name", "last_name")
            ),
        }

    def assert_legacy_candidate_fails_without_mutation(self, user_info, subject):
        before = self.fingerprint()
        with (
            patch.object(self.backend, "get_userinfo", return_value=user_info),
            patch.object(self.backend, "verify_claims", return_value=True),
        ):
            with self.assertRaises(OIDCIdentityBindingRequiredError):
                self.backend.get_or_create_user(
                    "access-token",
                    "id-token",
                    {"iss": "https://compat.example/issuer", "sub": subject},
                )
        self.assertEqual(self.fingerprint(), before)

    @override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=COMPAT_CONFIG)
    def test_every_former_candidate_shape_fails_closed_with_full_zero_write_fingerprint(self):
        User.objects.create_user(username="email-match", email="legacy@example.test")
        self.assert_legacy_candidate_fails_without_mutation(
            {"email": "LEGACY@example.test"},
            "email-match-subject",
        )

        User.objects.create_user(username="email-fallback@example.test", email="different@example.test")
        self.assert_legacy_candidate_fails_without_mutation(
            {"email": "email-fallback@example.test"},
            "email-fallback-subject",
        )

        User.objects.create_user(username="subject-fallback", email="other@example.test")
        self.assert_legacy_candidate_fails_without_mutation(
            {"sub": "subject-fallback"},
            "subject-fallback",
        )

        User.objects.create_user(username="oidc_user", email="another@example.test")
        self.assert_legacy_candidate_fails_without_mutation({}, "literal-fallback-id")

    @override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=COMPAT_CONFIG)
    def test_multiple_email_candidates_are_full_zero_write_and_identical(self):
        User.objects.create_user(username="ambiguous-a", email="ambiguous@example.test")
        User.objects.create_user(username="ambiguous-b", email="ambiguous@example.test")
        self.assert_legacy_candidate_fails_without_mutation(
            {"email": "AMBIGUOUS@example.test"},
            "ambiguous-subject",
        )

    @override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=COMPAT_CONFIG, OIDC_CREATE_USER=False)
    def test_create_disabled_is_full_zero_write_without_binding_or_profile_state(self):
        before = self.fingerprint()
        with (
            patch.object(self.backend, "get_userinfo", return_value={"email": "disabled@example.test"}),
            patch.object(self.backend, "verify_claims", return_value=True),
        ):
            result = self.backend.get_or_create_user(
                "access-token",
                "id-token",
                {"iss": "https://compat.example/issuer", "sub": "disabled-subject"},
            )
        self.assertIsNone(result)
        self.assertEqual(self.fingerprint(), before)

    @override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=COMPAT_CONFIG)
    def test_userinfo_identity_mismatch_is_full_zero_write(self):
        before = self.fingerprint()
        with (
            patch.object(
                self.backend,
                "get_userinfo",
                return_value={"iss": "https://compat.example/issuer", "sub": "different-subject"},
            ),
            patch.object(self.backend, "verify_claims", return_value=True),
        ):
            with self.assertRaises(OIDCTokenValidationError):
                self.backend.get_or_create_user(
                    "access-token",
                    "id-token",
                    {"iss": "https://compat.example/issuer", "sub": "verified-subject"},
                )
        self.assertEqual(self.fingerprint(), before)


class OIDCPhaseBRollbackRegressionTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Rollback", slug="compat-tenant")
        from core.managers import set_current_tenant

        set_current_tenant(self.tenant)
        self.user = User.objects.create_user(
            username="rollback-user",
            email="old-rollback@example.test",
            first_name="Old",
            last_name="Profile",
        )
        OIDCIdentity.objects.create(
            user=self.user,
            issuer="https://compat.example/issuer",
            subject="rollback-subject",
        )
        self.backend = TenantOIDCBackend()

    def tearDown(self):
        from core.managers import set_current_tenant

        set_current_tenant(None)

    def fingerprint(self):
        self.user.refresh_from_db()
        return {
            "users": User.objects.count(),
            "bindings": OIDCIdentity.objects.count(),
            "holders": AssetHolder.objects.count(),
            "memberships": Membership.objects.count(),
            "roles": Role.objects.count(),
            "grants": RoleGrant.objects.count(),
            "scopes": RoleGrantScope.objects.count(),
            "changes": ObjectChange.objects.count(),
            "events": Event.objects.count(),
            "profile": (self.user.email, self.user.first_name, self.user.last_name),
        }

    def resolve(self):
        with (
            patch.object(
                self.backend,
                "get_userinfo",
                return_value={
                    "iss": "https://compat.example/issuer",
                    "sub": "rollback-subject",
                    "email": "new-rollback@example.test",
                    "given_name": "New",
                    "family_name": "Profile",
                    "groups": [],
                },
            ),
            patch.object(self.backend, "verify_claims", return_value=True),
        ):
            with identity_provisioning.override_identity_provisioner(organization_identity_provisioner):
                return self.backend.get_or_create_user(
                    "access-token",
                    "id-token",
                    {"iss": "https://compat.example/issuer", "sub": "rollback-subject"},
                )

    def assert_stage_rolls_back_and_retry_converges(self, stage):
        before = self.fingerprint()
        checkpoint = {
            "holder": "customer.holder_created",
            "membership": "customer.membership_created",
            "grant": "customer.grant_reconciled",
            "scope": "customer.scope_reconciled",
        }[stage]

        def fail_at_checkpoint(current):
            if current == checkpoint:
                raise RuntimeError(f"injected {stage} stage failure")

        with patch(
            "organization.services.identity_provisioning._stage_checkpoint",
            side_effect=fail_at_checkpoint,
        ):
            with self.assertRaises(OIDCIdentityProvisioningError):
                self.resolve()

        self.assertEqual(self.fingerprint(), before)
        resolved = self.resolve()
        self.assertEqual(resolved.pk, self.user.pk)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(OIDCIdentity.objects.count(), 1)
        self.assertEqual(AssetHolder.objects.filter(user=self.user, tenant=self.tenant).count(), 1)
        self.assertEqual(Membership.objects.filter(user=self.user, tenant=self.tenant).count(), 1)
        membership = Membership.objects.get(user=self.user, tenant=self.tenant)
        self.assertEqual(RoleGrant.objects.filter(membership=membership).count(), 1)
        grant = RoleGrant.objects.get(membership=membership)
        self.assertEqual(RoleGrantScope.objects.filter(role_grant=grant).count(), 1)

    @override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=COMPAT_CONFIG)
    def test_failure_after_real_holder_creation_rolls_back_phase_b(self):
        self.assert_stage_rolls_back_and_retry_converges("holder")

    @override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=COMPAT_CONFIG)
    def test_failure_after_real_membership_creation_rolls_back_phase_b(self):
        self.assert_stage_rolls_back_and_retry_converges("membership")

    @override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=COMPAT_CONFIG)
    def test_failure_after_real_role_grant_creation_rolls_back_phase_b(self):
        self.assert_stage_rolls_back_and_retry_converges("grant")

    @override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=COMPAT_CONFIG)
    def test_failure_after_real_scope_creation_rolls_back_phase_b(self):
        self.assert_stage_rolls_back_and_retry_converges("scope")
