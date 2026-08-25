from __future__ import annotations

import ast
import inspect
import textwrap
import threading
import time
from datetime import timedelta
from threading import Event
from unittest.mock import Mock, patch

import pytest
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

import core.auth.oidc as oidc_module
import organization.services.tenant_onboarding as tenant_onboarding
from core import identity_provisioning
from core.auth.oidc import TenantOIDCBackend
from core.identity_provisioning import ExternalIdentityProvisioningResult
from core.managers import set_current_tenant
from core.oidc_identity import oidc_sensitive_audit
from organization.models import AssetHolder, Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.services.identity_provisioning import organization_identity_provisioner
from users.models import OIDCIdentity, User

ISSUER = "https://phase-b.example/issuer"
OIDC_CONFIG = {
    "phase-b-customer": {
        "OIDC_OP_ISSUER": ISSUER,
        "OIDC_RP_CLIENT_ID": "phase-b-client",
        "OIDC_RP_CLIENT_SECRET": "phase-b-secret",
        "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://phase-b.example/authorize",
        "OIDC_OP_TOKEN_ENDPOINT": "https://phase-b.example/token",
        "OIDC_OP_USER_ENDPOINT": "https://phase-b.example/userinfo",
        "OIDC_OP_JWKS_ENDPOINT": "https://phase-b.example/jwks",
        "OIDC_GROUP_ROLE_MAPPING": {
            "customer-member": "Member",
            "customer-admin": "Admin",
        },
    },
    "phase-b-provider": {
        "OIDC_OP_ISSUER": ISSUER,
        "OIDC_GROUP_PROVIDER_ROLE_MAPPING": {
            "provider-first": "Provider Staff",
            "provider-second": "Provider Staff Second",
        },
    },
}


@override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=OIDC_CONFIG)
class OIDCPhaseBPortContractTests(TestCase):
    def setUp(self):
        self.provider = Tenant.objects.create(
            name="Phase B Provider",
            slug="phase-b-provider",
            is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name="Phase B Customer",
            slug="phase-b-customer",
            managed_by=self.provider,
        )
        self.customer_role = Role.objects.create(
            tenant=self.customer,
            name="Admin",
            permissions=[],
        )
        self.provider_role = Role.objects.create(
            tenant=self.provider,
            name="Provider Staff",
            permissions=[],
        )
        self.user = User.objects.create_user(
            username="phase-b-user",
            email="old@example.test",
            first_name="Old",
            last_name="Profile",
        )
        self.binding = OIDCIdentity.objects.create(
            user=self.user,
            issuer=ISSUER,
            subject="phase-b-subject",
        )
        set_current_tenant(self.customer)
        self.backend = TenantOIDCBackend()

    def tearDown(self):
        set_current_tenant(None)

    @staticmethod
    def claims():
        return {
            "iss": ISSUER,
            "sub": "phase-b-subject",
            "email": "new@example.test",
            "upn": "new-upn@example.test",
            "given_name": "New",
            "family_name": "Name",
            "groups": ["provider-first", "provider-second", "customer-admin"],
        }

    def test_adapter_owns_normalization_and_calls_sdk_free_port_once(self):
        result = ExternalIdentityProvisioningResult(mode="customer")
        with patch.object(
            identity_provisioning,
            "provision_external_identity",
            return_value=result,
        ) as provision:
            resolved = self.backend._finish_identity_phase_b(
                self.binding.pk,
                self.user.pk,
                self.claims(),
            )

        self.assertEqual(resolved.pk, self.user.pk)
        provision.assert_called_once()
        command = provision.call_args.args[0]
        self.assertEqual(command.user.pk, self.user.pk)
        self.assertEqual(command.customer_tenant.pk, self.customer.pk)
        self.assertEqual(command.profile.source, "OIDC")
        self.assertEqual(command.profile.email, "new@example.test")
        self.assertEqual(command.profile.upn, "new-upn@example.test")
        self.assertEqual(command.profile.first_name, "New")
        self.assertEqual(command.profile.last_name, "Name")
        self.assertEqual(command.customer_role_name, "Admin")
        self.assertIsNotNone(command.provider_staff)
        self.assertEqual(command.provider_staff.provider_tenant.pk, self.provider.pk)
        self.assertEqual(command.provider_staff.role_name, "Provider Staff")
        self.assertNotIn("provider-first", repr(command))
        self.assertNotIn("provider-second", repr(command))

    def test_rejected_provider_intent_is_terminal_without_profile_or_organization_write(self):
        before_profile = (self.user.email, self.user.first_name, self.user.last_name)
        before_organization = (
            AssetHolder.objects.count(),
            Membership.objects.count(),
            Role.objects.count(),
            RoleGrant.objects.count(),
            RoleGrantScope.objects.count(),
        )
        with patch.object(
            identity_provisioning,
            "provision_external_identity",
            return_value=ExternalIdentityProvisioningResult(mode="provider_mapping_rejected"),
        ) as provision:
            resolved = self.backend._finish_identity_phase_b(
                self.binding.pk,
                self.user.pk,
                self.claims(),
            )

        self.assertEqual(resolved.pk, self.user.pk)
        provision.assert_called_once()
        self.user.refresh_from_db()
        self.assertEqual(
            (self.user.email, self.user.first_name, self.user.last_name),
            before_profile,
        )
        self.assertEqual(
            (
                AssetHolder.objects.count(),
                Membership.objects.count(),
                Role.objects.count(),
                RoleGrant.objects.count(),
                RoleGrantScope.objects.count(),
            ),
            before_organization,
        )

    def test_can_login_false_does_not_call_identity_port_or_mutate_phase_b(self):
        self.user.can_login = False
        self.user.save(update_fields=["can_login"])
        before_profile = (self.user.email, self.user.first_name, self.user.last_name)
        before_organization = (
            AssetHolder.objects.count(),
            Membership.objects.count(),
            RoleGrant.objects.count(),
            RoleGrantScope.objects.count(),
        )
        with patch.object(identity_provisioning, "provision_external_identity") as provision:
            with (
                patch.object(self.backend, "get_userinfo", return_value={"email": "disabled-new@example.test"}),
                patch.object(self.backend, "verify_claims", return_value=True),
            ):
                result = self.backend.get_or_create_user(
                    "access-token",
                    "id-token",
                    {"iss": ISSUER, "sub": self.binding.subject},
                )

        self.assertEqual(result.pk, self.user.pk)
        provision.assert_not_called()
        self.user.refresh_from_db()
        self.assertEqual(
            (self.user.email, self.user.first_name, self.user.last_name),
            before_profile,
        )
        self.assertEqual(
            (
                AssetHolder.objects.count(),
                Membership.objects.count(),
                RoleGrant.objects.count(),
                RoleGrantScope.objects.count(),
            ),
            before_organization,
        )

    def test_phase_b_source_has_one_binding_lock_and_no_user_lock(self):
        source = inspect.getsource(TenantOIDCBackend._finish_identity_phase_b)
        tree = ast.parse(textwrap.dedent(source))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "provision_external_identity"
        ]
        self.assertEqual(len(calls), 1)
        self.assertIn("tenant_scope.tenant_model", source)
        self.assertEqual(source.count("select_for_update"), 1)
        self.assertNotIn("UserModel._base_manager.select_for_update", source)

    def test_adapter_has_no_direct_organization_or_legacy_provisioning_ownership(self):
        source = inspect.getsource(oidc_module)
        self.assertNotIn("from organization.models", source)
        self.assertNotIn("core.auth.provisioning", source)
        self.assertNotIn("MATRIX_MODELS", source)
        self.assertNotIn("Permission", source)
        self.assertNotIn("sync_user_profile_and_memberships", source)
        self.assertNotIn("get_permissions_for_role", source)


class OIDCPhaseBAuditFailureTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Audit Failure", slug="phase-b-customer")
        self.user = User.objects.create_user(
            username="audit-failure-user",
            email="old-audit@example.test",
            first_name="Old",
            last_name="Audit",
        )
        self.binding = OIDCIdentity.objects.create(
            user=self.user,
            issuer=ISSUER,
            subject="audit-failure-subject",
        )
        set_current_tenant(self.tenant)
        self.backend = TenantOIDCBackend()

    def tearDown(self):
        set_current_tenant(None)

    @override_settings(ITAMBOX_TENANT_OIDC_CONFIGS={"phase-b-customer": OIDC_CONFIG["phase-b-customer"]})
    def test_profile_failure_rolls_back_organization_but_keeps_phase_a_binding(self):
        before = {
            "holders": AssetHolder.objects.count(),
            "memberships": Membership.objects.count(),
            "roles": Role.objects.count(),
            "grants": RoleGrant.objects.count(),
            "scopes": RoleGrantScope.objects.count(),
            "profile": (self.user.email, self.user.first_name, self.user.last_name),
        }
        original_update = self.backend._update_user_profile

        def fail_after_profile_update(user, claims):
            original_update(user, claims)
            raise RuntimeError("profile-stage-canary")

        with (
            patch.object(
                identity_provisioning,
                "provision_external_identity",
                wraps=organization_identity_provisioner.provision,
            ) as provision,
            patch.object(
                self.backend,
                "_update_user_profile",
                side_effect=fail_after_profile_update,
            ),
            self.assertRaises(Exception),
        ):
            with oidc_sensitive_audit():
                self.backend._finish_identity_phase_b(
                    self.binding.pk,
                    self.user.pk,
                    {
                        "iss": ISSUER,
                        "sub": self.binding.subject,
                        "email": "new-audit@example.test",
                        "given_name": "New",
                        "family_name": "Audit",
                    },
                )

        provision.assert_called_once()
        self.user.refresh_from_db()
        self.assertEqual(OIDCIdentity.objects.filter(pk=self.binding.pk, user=self.user).count(), 1)
        self.assertEqual(
            {
                "holders": AssetHolder.objects.count(),
                "memberships": Membership.objects.count(),
                "roles": Role.objects.count(),
                "grants": RoleGrant.objects.count(),
                "scopes": RoleGrantScope.objects.count(),
                "profile": (self.user.email, self.user.first_name, self.user.last_name),
            },
            before,
        )


B2_OIDC_CONFIG = {
    "b2-customer": {
        "OIDC_OP_ISSUER": "https://b2.example/issuer",
        "OIDC_RP_CLIENT_ID": "b2-client",
        "OIDC_RP_CLIENT_SECRET": "b2-secret",
        "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://b2.example/authorize",
        "OIDC_OP_TOKEN_ENDPOINT": "https://b2.example/token",
        "OIDC_OP_USER_ENDPOINT": "https://b2.example/userinfo",
        "OIDC_OP_JWKS_ENDPOINT": "https://b2.example/jwks",
        "OIDC_GROUP_ROLE_MAPPING": {},
    }
}


@pytest.mark.serial_only
@override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=B2_OIDC_CONFIG)
class OIDCB2LockCompositionTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        set_current_tenant(None)
        self.provider = Tenant.objects.create(
            name="B2 Provider",
            slug="b2-provider",
            is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name="B2 Customer",
            slug="b2-customer",
            managed_by=self.provider,
        )
        Role.objects.create(
            tenant=self.customer,
            name="Member",
            permissions=[],
        )
        administrator = Role.objects.create(
            tenant=self.provider,
            name="B2 Administrator",
            permissions=["organization.add_tenant"],
        )
        self.user = User.objects.create_user(
            username="b2-lock-user",
            email="b2-lock@example.test",
            is_staff=True,
        )
        self.provider_membership = Membership.objects.create(
            user=self.user,
            tenant=self.provider,
            is_active=True,
        )
        self.authorizer = RoleGrant.objects.create(
            membership=self.provider_membership,
            role=administrator,
            granted_by=self.user,
            reason="B2 test authorizer",
            valid_until=timezone.now() + timedelta(days=1),
        )
        RoleGrantScope.objects.create(
            role_grant=self.authorizer,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        self.binding = OIDCIdentity.objects.create(
            user=self.user,
            issuer=B2_OIDC_CONFIG["b2-customer"]["OIDC_OP_ISSUER"],
            subject="b2-lock-subject",
        )
        self.events = {
            "onboarding_tenant_locked": Event(),
            "service_tenant_attempted": Event(),
            "service_wait_observed": Event(),
            "binding_locked": Event(),
            "onboarding_user_locked": Event(),
        }
        self.outputs = {"onboarding": {}, "oidc": {}}
        self.lock_orders = {"onboarding": [], "oidc": []}

    def tearDown(self):
        set_current_tenant(None)
        connections.close_all()

    def _prepare_worker_connection(self, label):
        connections.close_all()
        connection.ensure_connection()
        backend = connection.connection
        self.assertIsNotNone(backend)
        pid = backend.get_backend_pid()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('application_name', %s, false)",
                [f"itambox443r-oidc-b2-{label}"],
            )
            cursor.execute("SET SESSION lock_timeout = '10s'")
            cursor.execute("SET SESSION statement_timeout = '30s'")
        return pid

    def _wait_for_real_tenant_wait(self, waiting_pid, blocking_pid):
        deadline = time.monotonic() + 8
        observations = []
        while time.monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT waiting.pid,
                           waiting.wait_event_type,
                           waiting.wait_event,
                           wait_lock.locktype,
                           wait_lock.relation::regclass::text,
                           wait_lock.page,
                           wait_lock.tuple,
                           blocking.pid,
                           waiting.query,
                           blocking.query,
                           waiting.application_name
                    FROM pg_stat_activity AS waiting
                    JOIN pg_locks AS wait_lock
                      ON wait_lock.pid = waiting.pid
                     AND NOT wait_lock.granted
                    LEFT JOIN LATERAL unnest(pg_blocking_pids(waiting.pid)) AS blocker(pid) ON TRUE
                    LEFT JOIN pg_stat_activity AS blocking ON blocking.pid = blocker.pid
                    WHERE waiting.pid = %s
                      AND waiting.datname = current_database()
                    """,
                    [waiting_pid],
                )
                rows = cursor.fetchall()
            observations.extend(rows)
            for row in rows:
                waiting_query = (row[8] or "").lower()
                if (
                    row[0] == waiting_pid
                    and row[1] == "Lock"
                    and row[3] in {"transactionid", "tuple"}
                    and row[7] == blocking_pid
                    and "organization_tenant" in waiting_query
                    and "for share" in waiting_query
                ):
                    return
            Event().wait(0.02)
        self.fail(f"OIDC Tenant FOR SHARE wait was not observed: {observations[-3:]}")

    def _onboarding_worker(self):
        try:
            pid = self._prepare_worker_connection("onboarding")
            original_lock_tenants = tenant_onboarding._lock_tenants

            def pause_after_tenant_lock(*, provider_id, tenant_id):
                result = original_lock_tenants(provider_id=provider_id, tenant_id=tenant_id)
                self.events["onboarding_tenant_locked"].set()
                if not self.events["service_wait_observed"].wait(timeout=8):
                    raise AssertionError("OIDC service Tenant wait was not observed")
                return result

            def observe_onboarding_sql(execute, sql, params, many, context):
                normalized = " ".join(sql.split()).lower()
                if 'from "organization_tenant"' in normalized and "for update" in normalized:
                    self.lock_orders["onboarding"].append("tenant")
                if 'from "users_user"' in normalized and "for update" in normalized:
                    self.lock_orders["onboarding"].append("user")
                    self.events["onboarding_user_locked"].set()
                return execute(sql, params, many, context)

            with patch.object(tenant_onboarding, "_lock_tenants", side_effect=pause_after_tenant_lock):
                with connection.execute_wrapper(observe_onboarding_sql):
                    self.outputs["onboarding"]["result"] = tenant_onboarding.onboard_managed_tenant(
                        actor=User._base_manager.get(pk=self.user.pk),
                        provider_id=self.provider.pk,
                        tenant=Tenant._base_manager.get(pk=self.customer.pk),
                    )
            self.outputs["onboarding"]["pid"] = pid
        except Exception as exc:
            self.outputs["onboarding"]["error"] = exc
        finally:
            connections.close_all()

    def _oidc_worker(self):
        try:
            pid = self._prepare_worker_connection("oidc")
            tenant = Tenant._base_manager.get(pk=self.customer.pk)
            set_current_tenant(tenant)
            backend = TenantOIDCBackend()
            backend.get_userinfo = Mock(
                return_value={
                    "iss": B2_OIDC_CONFIG["b2-customer"]["OIDC_OP_ISSUER"],
                    "sub": self.binding.subject,
                    "email": "b2-lock-updated@example.test",
                    "groups": [],
                }
            )
            backend.verify_claims = Mock(return_value=True)
            binding_locks = 0

            def observe_oidc_sql(execute, sql, params, many, context):
                nonlocal binding_locks
                normalized = " ".join(sql.split()).lower()
                if 'from "users_oidcidentity"' in normalized and "for update" in normalized:
                    binding_locks += 1
                    if binding_locks >= 2:
                        self.events["binding_locked"].set()
                    self.lock_orders["oidc"].append("binding")
                if 'from "organization_tenant"' in normalized and "for share" in normalized:
                    self.events["service_tenant_attempted"].set()
                    self.lock_orders["oidc"].append("tenant")
                if 'from "users_user"' in normalized and "for update" in normalized:
                    self.lock_orders["oidc"].append("user")
                return execute(sql, params, many, context)

            with connection.execute_wrapper(observe_oidc_sql):
                with identity_provisioning.override_identity_provisioner(organization_identity_provisioner):
                    self.outputs["oidc"]["result"] = backend.get_or_create_user(
                        "access-token",
                        "id-token",
                        {
                            "iss": B2_OIDC_CONFIG["b2-customer"]["OIDC_OP_ISSUER"],
                            "sub": self.binding.subject,
                        },
                    )
            self.outputs["oidc"]["pid"] = pid
        except Exception as exc:
            self.outputs["oidc"]["error"] = exc
        finally:
            set_current_tenant(None)
            connections.close_all()

    def test_binding_to_service_order_composes_with_real_onboarding_without_deadlock(self):
        onboarding = threading.Thread(target=self._onboarding_worker, name="real-b2-onboarding")
        oidc = threading.Thread(target=self._oidc_worker, name="real-b2-oidc")
        oidc_started = False
        onboarding.start()
        try:
            self.assertTrue(self.events["onboarding_tenant_locked"].wait(timeout=8))
            oidc.start()
            oidc_started = True
            self.assertTrue(self.events["binding_locked"].wait(timeout=8))
            self.assertTrue(self.events["service_tenant_attempted"].wait(timeout=8))
            self._wait_for_real_tenant_wait(
                self._oidc_pid_from_activity(),
                self._onboarding_pid_from_activity(),
            )
        finally:
            self.events["service_wait_observed"].set()
            onboarding.join(timeout=15)
            if oidc_started:
                oidc.join(timeout=15)
        self.assertFalse(onboarding.is_alive())
        self.assertFalse(oidc.is_alive())
        self.assertIsNone(self.outputs["onboarding"].get("error"))
        self.assertIsNone(self.outputs["oidc"].get("error"))
        self.assertTrue(self.events["onboarding_user_locked"].is_set())
        self.assertLess(self.lock_orders["onboarding"].index("tenant"), self.lock_orders["onboarding"].index("user"))
        self.assertLess(self.lock_orders["oidc"].index("tenant"), self.lock_orders["oidc"].index("user"))
        self.assertEqual(self.outputs["oidc"]["result"].pk, self.user.pk)

    def _oidc_pid_from_activity(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pid FROM pg_stat_activity WHERE application_name = %s",
                ["itambox443r-oidc-b2-oidc"],
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row)
        return row[0]

    def _onboarding_pid_from_activity(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pid FROM pg_stat_activity WHERE application_name = %s",
                ["itambox443r-oidc-b2-onboarding"],
            )
            row = cursor.fetchone()
        self.assertIsNotNone(row)
        return row[0]
