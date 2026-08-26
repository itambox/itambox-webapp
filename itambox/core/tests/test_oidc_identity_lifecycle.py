from unittest.mock import Mock, patch

from django.core.exceptions import SuspiciousOperation
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings

from core import identity_provisioning
from core.auth.oidc import (
    OIDCIdentityBindingRequiredError,
    OIDCIdentityProvisioningError,
    OIDCTokenValidationError,
    TenantOIDCBackend,
)
from core.managers import set_current_tenant
from core.models import ObjectChange
from extras.models import Event
from organization.models import AssetHolder, Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.services.identity_provisioning import organization_identity_provisioner
from users.models import OIDCIdentity, User

ISSUER = "https://idp.example/issuer"
OIDC_CONFIG = {
    "tenant-alpha": {
        "OIDC_OP_ISSUER": ISSUER,
        "OIDC_RP_CLIENT_ID": "client-alpha",
        "OIDC_RP_CLIENT_SECRET": "not-used-in-test",
        "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://idp.example/authorize",
        "OIDC_OP_TOKEN_ENDPOINT": "https://idp.example/token",
        "OIDC_OP_USER_ENDPOINT": "https://idp.example/userinfo",
    }
}


@override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=OIDC_CONFIG)
class TenantOIDCIdentityLifecycleTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        self.tenant = Tenant.objects.create(name="Alpha", slug="tenant-alpha")
        set_current_tenant(self.tenant)
        self.backend = TenantOIDCBackend()

    def tearDown(self):
        set_current_tenant(None)

    def resolve(self, payload, user_info=None):
        claims = user_info if user_info is not None else {"email": "new@example.test"}
        with (
            patch.object(self.backend, "get_userinfo", return_value=claims),
            patch.object(
                self.backend,
                "verify_claims",
                return_value=True,
            ),
        ):
            with identity_provisioning.override_identity_provisioner(organization_identity_provisioner):
                return self.backend.get_or_create_user("access-token", "id-token", payload)

    def test_existing_binding_is_authority_after_email_and_username_change(self):
        user = User.objects.create_user(username="old-display", email="old@example.test")
        identity = OIDCIdentity.objects.create(user=user, issuer=ISSUER, subject="stable-subject")

        resolved = self.resolve(
            {"iss": ISSUER, "sub": "stable-subject"},
            {
                "email": "changed@example.test",
                "sub": "stable-subject",
                "given_name": "Changed",
                "family_name": "Profile",
            },
        )

        self.assertEqual(resolved.pk, user.pk)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(OIDCIdentity.objects.count(), 1)
        identity.refresh_from_db()
        user.refresh_from_db()
        self.assertEqual(identity.subject, "stable-subject")
        self.assertEqual(user.email, "changed@example.test")
        self.assertEqual(user.username, "old-display")

    def test_new_identity_creates_one_user_and_one_binding(self):
        resolved = self.resolve(
            {"iss": ISSUER, "sub": "new-subject"},
            {"email": "new@example.test", "given_name": "New", "family_name": "User"},
        )

        self.assertIsNotNone(resolved)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(OIDCIdentity.objects.count(), 1)
        identity = OIDCIdentity.objects.get()
        self.assertEqual(identity.user_id, resolved.pk)
        self.assertEqual(identity.issuer, ISSUER)
        self.assertEqual(identity.subject, "new-subject")

    def test_same_identity_repeat_updates_profile_without_new_user(self):
        first = self.resolve(
            {"iss": ISSUER, "sub": "repeat-subject"},
            {"email": "first@example.test", "given_name": "First"},
        )
        second = self.resolve(
            {"iss": ISSUER, "sub": "repeat-subject"},
            {"email": "second@example.test", "given_name": "Second"},
        )

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(OIDCIdentity.objects.count(), 1)
        self.assertEqual(User.objects.get(pk=first.pk).email, "second@example.test")

    def test_different_exact_issuer_or_subject_remains_distinct(self):
        first = self.resolve({"iss": ISSUER, "sub": "case-sensitive"}, {"email": "one@example.test"})
        other_tenant = Tenant.objects.create(name="Other", slug="tenant-other")
        set_current_tenant(other_tenant)
        with override_settings(
            ITAMBOX_TENANT_OIDC_CONFIGS={
                **OIDC_CONFIG,
                "tenant-other": {"OIDC_OP_ISSUER": "https://other.example/issuer"},
            }
        ):
            second = self.resolve(
                {"iss": "https://other.example/issuer", "sub": "case-sensitive"},
                {"email": "two@example.test"},
            )

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(OIDCIdentity.objects.count(), 2)

    def test_one_legacy_candidate_fails_closed_without_mutation(self):
        legacy = User.objects.create_user(username="legacy", email="legacy@example.test")

        with self.assertRaises(OIDCIdentityBindingRequiredError):
            self.resolve(
                {"iss": ISSUER, "sub": "unbound-subject"},
                {"email": "legacy@example.test"},
            )

        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(OIDCIdentity.objects.count(), 0)
        self.assertEqual(legacy.email, "legacy@example.test")
        self.assertEqual(Membership.objects.count(), 0)
        self.assertEqual(AssetHolder.objects.count(), 0)

    def test_multiple_legacy_candidates_fail_closed_identically(self):
        User.objects.create_user(username="legacy-a", email="duplicate@example.test")
        User.objects.create_user(username="legacy-b", email="duplicate@example.test")

        with self.assertRaises(OIDCIdentityBindingRequiredError) as caught:
            self.resolve(
                {"iss": ISSUER, "sub": "ambiguous-subject"},
                {"email": "DUPLICATE@example.test"},
            )

        self.assertEqual(str(caught.exception), "OIDC identity requires an explicit operator binding.")
        self.assertEqual(OIDCIdentity.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)

    @override_settings(OIDC_CREATE_USER=False)
    def test_create_disabled_without_binding_returns_none_without_writes(self):
        result = self.resolve(
            {"iss": ISSUER, "sub": "create-disabled"},
            {"email": "disabled@example.test"},
        )

        self.assertIsNone(result)
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(OIDCIdentity.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)

    @override_settings(OIDC_CREATE_USER=False)
    def test_create_disabled_does_not_block_existing_binding(self):
        user = User.objects.create_user(username="existing", email="existing@example.test")
        OIDCIdentity.objects.create(user=user, issuer=ISSUER, subject="existing-subject")

        result = self.resolve(
            {"iss": ISSUER, "sub": "existing-subject"},
            {"email": "updated@example.test"},
        )

        self.assertEqual(result.pk, user.pk)
        self.assertEqual(User.objects.get(pk=user.pk).email, "updated@example.test")

    def test_userinfo_identity_mismatch_fails_before_any_write(self):
        with self.assertRaises(OIDCTokenValidationError):
            self.resolve(
                {"iss": ISSUER, "sub": "verified-subject"},
                {"email": "mismatch@example.test", "iss": ISSUER, "sub": "other-subject"},
            )

        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(OIDCIdentity.objects.count(), 0)
        self.assertEqual(Membership.objects.count(), 0)

    def test_failed_organization_phase_keeps_binding_and_retries_same_user(self):
        payload = {"iss": ISSUER, "sub": "retry-subject"}
        with (
            patch.object(
                identity_provisioning,
                "provision_external_identity",
                side_effect=RuntimeError("raw organization failure should not leak"),
            ),
            patch.object(self.backend, "get_userinfo", return_value={"email": "retry@example.test"}),
            patch.object(
                self.backend,
                "verify_claims",
                return_value=True,
            ),
        ):
            with self.assertRaises(OIDCIdentityProvisioningError) as caught:
                self.backend.get_or_create_user("access-token", "id-token", payload)

        self.assertNotIn("raw organization failure", str(caught.exception))
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(OIDCIdentity.objects.count(), 1)
        user_id = User.objects.get().pk
        user = self.resolve(payload, {"email": "retry-success@example.test"})
        self.assertEqual(user.pk, user_id)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(OIDCIdentity.objects.count(), 1)

    def test_invalid_subject_is_rejected_by_get_or_create_before_writes(self):
        with self.assertRaises(OIDCTokenValidationError):
            self.resolve({"iss": ISSUER, "sub": "über"}, {"email": "invalid@example.test"})

        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(OIDCIdentity.objects.count(), 0)

    def test_invalid_subject_is_rejected_before_upstream_token_storage(self):
        request = RequestFactory().get("/oidc/callback?code=code&state=state")
        backend = TenantOIDCBackend()
        backend.get_token = Mock(return_value={"access_token": "access", "id_token": "id"})
        backend.store_tokens = Mock()
        invalid_payload = {
            "aud": "client-alpha",
            "iss": ISSUER,
            "sub": "über",
        }

        with (
            patch(
                "mozilla_django_oidc.auth.OIDCAuthenticationBackend.verify_token",
                return_value=invalid_payload,
            ),
            self.assertRaises(OIDCTokenValidationError),
        ):
            backend.authenticate(request)

        backend.store_tokens.assert_not_called()
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(OIDCIdentity.objects.count(), 0)


@override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=OIDC_CONFIG)
class TenantOIDCProviderCompatibilityTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        self.customer = Tenant.objects.create(name="Customer", slug="tenant-alpha")
        self.provider = Tenant.objects.create(name="Provider", slug="provider", is_provider=True)
        self.customer.managed_by = self.provider
        self.customer.save(update_fields=["managed_by"])
        set_current_tenant(self.customer)

    def tearDown(self):
        set_current_tenant(None)

    def test_missing_provider_role_is_terminal_with_zero_organization_writes(self):
        backend = TenantOIDCBackend()
        claims = {
            "email": "provider-staff@example.test",
            "groups": ["provider-staff"],
        }
        settings = {
            "tenant-alpha": {
                **OIDC_CONFIG["tenant-alpha"],
                "OIDC_GROUP_PROVIDER_ROLE_MAPPING": {"provider-staff": "Missing role"},
                "OIDC_GROUP_ROLE_MAPPING": {"provider-staff": "Admin"},
            },
            "provider": {
                "OIDC_OP_ISSUER": ISSUER,
                "OIDC_GROUP_PROVIDER_ROLE_MAPPING": {"provider-staff": "Missing role"},
            },
        }
        before = {
            "users": User.objects.count(),
            "bindings": OIDCIdentity.objects.count(),
            "holders": AssetHolder.objects.count(),
            "memberships": Membership.objects.count(),
            "roles": Role.objects.count(),
            "grants": RoleGrant.objects.count(),
            "scopes": RoleGrantScope.objects.count(),
            "changes": ObjectChange.objects.count(),
            "events": Event.objects.count(),
        }
        with override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=settings):
            with (
                patch.object(backend, "get_userinfo", return_value=claims),
                patch.object(
                    backend,
                    "verify_claims",
                    return_value=True,
                ),
            ):
                with identity_provisioning.override_identity_provisioner(organization_identity_provisioner):
                    user = backend.get_or_create_user(
                        "access-token",
                        "id-token",
                        {"iss": ISSUER, "sub": "provider-staff-subject"},
                    )

        self.assertIsNotNone(user)
        self.assertTrue(OIDCIdentity.objects.filter(user=user).exists())
        self.assertFalse(Membership.objects.filter(user=user).exists())
        self.assertFalse(AssetHolder.objects.filter(user=user).exists())
        self.assertFalse(RoleGrant.objects.filter(membership__user=user).exists())
        self.assertFalse(RoleGrantScope.objects.filter(role_grant__membership__user=user).exists())
        self.assertEqual(User.objects.count(), before["users"] + 1)
        self.assertEqual(OIDCIdentity.objects.count(), before["bindings"] + 1)
        self.assertEqual(AssetHolder.objects.count(), before["holders"])
        self.assertEqual(Membership.objects.count(), before["memberships"])
        self.assertEqual(Role.objects.count(), before["roles"])
        self.assertEqual(RoleGrant.objects.count(), before["grants"])
        self.assertEqual(RoleGrantScope.objects.count(), before["scopes"])
        self.assertEqual(ObjectChange.objects.count(), before["changes"])
        self.assertEqual(Event.objects.count(), before["events"])


class OIDCIdentityLogContractTests(TransactionTestCase):
    reset_sequences = True

    @override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=OIDC_CONFIG)
    def test_safe_exception_and_log_path_contains_no_identity_material(self):
        set_current_tenant(None)
        tenant = Tenant.objects.create(name="Alpha", slug="tenant-alpha")
        set_current_tenant(tenant)
        backend = TenantOIDCBackend()
        canaries = (
            "canary-issuer",
            "canary-subject",
            "canary@example.test",
            "canary-username",
            "canary-token",
            "raw organization failure",
        )
        try:
            with (
                patch.object(backend, "get_userinfo", return_value={"email": "canary@example.test"}),
                patch.object(
                    backend,
                    "verify_claims",
                    return_value=True,
                ),
                patch.object(
                    identity_provisioning,
                    "provision_external_identity",
                    side_effect=RuntimeError("raw organization failure"),
                ),
            ):
                with self.assertLogs("core.auth.oidc", level="WARNING") as captured:
                    with self.assertRaises(SuspiciousOperation):
                        backend.get_or_create_user(
                            "canary-token",
                            "canary-id-token",
                            {"iss": ISSUER, "sub": "canary-subject"},
                        )
            rendered = "\n".join(captured.output)
            for canary in canaries:
                self.assertNotIn(canary, rendered)
        finally:
            set_current_tenant(None)
