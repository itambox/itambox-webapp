import threading
from unittest.mock import Mock, patch

import pytest
from django.db import IntegrityError, close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase, override_settings

from core import identity_provisioning
from core.auth.oidc import (
    OIDCIdentityProvisioningError,
    TenantOIDCBackend,
    VerifiedOIDCIdentity,
    _acquire_oidc_identity_lock,
)
from core.managers import set_current_tenant
from organization.models import AssetHolder, Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.services.identity_provisioning import organization_identity_provisioner
from users.models import OIDCIdentity, User

ISSUER = "https://race.example/issuer"
OIDC_CONFIG = {
    "race-tenant": {
        "OIDC_OP_ISSUER": ISSUER,
        "OIDC_RP_CLIENT_ID": "race-client",
        "OIDC_RP_CLIENT_SECRET": "not-used-in-test",
        "OIDC_OP_AUTHORIZATION_ENDPOINT": "https://race.example/authorize",
        "OIDC_OP_TOKEN_ENDPOINT": "https://race.example/token",
        "OIDC_OP_USER_ENDPOINT": "https://race.example/userinfo",
        "OIDC_OP_JWKS_ENDPOINT": "https://race.example/jwks",
        "OIDC_GROUP_ROLE_MAPPING": {},
    }
}
LOCK_SQL = "SELECT pg_advisory_xact_lock(%s::integer, %s::integer)"


@pytest.mark.serial_only
@override_settings(ITAMBOX_TENANT_OIDC_CONFIGS=OIDC_CONFIG)
class OIDCIdentityConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        set_current_tenant(None)
        self.tenant = Tenant.objects.create(name="Race Tenant", slug="race-tenant")
        self.role = Role.objects.create(tenant=self.tenant, name="Member", permissions=[])

    def tearDown(self):
        set_current_tenant(None)
        connections.close_all()

    def test_advisory_lock_sql_is_parameterized_and_uses_digest_parts(self):
        class RecordingCursor:
            def __init__(self):
                self.calls = []

            def execute(self, sql, params):
                self.calls.append((sql, params))

        cursor = RecordingCursor()
        _acquire_oidc_identity_lock(
            cursor,
            VerifiedOIDCIdentity(issuer=ISSUER, subject="parameterized-subject"),
        )

        self.assertEqual(len(cursor.calls), 1)
        sql, params = cursor.calls[0]
        self.assertEqual(sql, LOCK_SQL)
        self.assertIsInstance(params, tuple)
        self.assertEqual(len(params), 2)
        self.assertTrue(all(isinstance(value, int) for value in params))
        rendered = repr(cursor.calls)
        self.assertNotIn(ISSUER, rendered)
        self.assertNotIn("parameterized-subject", rendered)

    def _worker(
        self,
        label,
        subject,
        start_barrier,
        results,
        thread_state,
        winner,
        winner_lock_held,
        release_winner,
        loser_lock_sql_started,
        finished,
    ):
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                results[label]["pid"] = cursor.fetchone()[0]
            tenant = Tenant._base_manager.get(pk=self.tenant.pk)
            set_current_tenant(tenant)
            backend = TenantOIDCBackend()
            claims = {
                "email": f"{subject}@example.test",
                "given_name": "Race",
                "family_name": "User",
            }
            backend.get_userinfo = Mock(return_value=claims)
            backend.verify_claims = Mock(return_value=True)
            backend.get_username = lambda user_claims, worker_label=label: f"race-{subject}-{worker_label}"
            original_select = backend._select_identity_binding

            def record_binding_read(identity, *, for_update=False):
                selected = original_select(identity, for_update=for_update)
                if for_update:
                    results[label]["binding_read_count"] = results[label].get("binding_read_count", 0) + 1
                    results[label]["binding_read_exact"] = identity.issuer == ISSUER and identity.subject == subject
                return selected

            backend._select_identity_binding = record_binding_read
            thread_state.label = label
            start_barrier.wait(timeout=5)

            def execute_wrapper(execute, sql, params, many, context):
                normalized = " ".join(sql.split())
                if normalized != LOCK_SQL:
                    return execute(sql, params, many, context)
                results[label]["lock_sql"] = normalized
                results[label]["lock_param_count"] = len(params or ())
                results[label]["lock_params_are_int"] = all(isinstance(value, int) for value in (params or ()))
                if label == winner:
                    result = execute(sql, params, many, context)
                    winner_lock_held.set()
                    if not release_winner.wait(timeout=10):
                        raise RuntimeError("winner release gate timed out")
                    return result
                results[label]["loser_lock_sql_started"] = True
                loser_lock_sql_started.set()
                result = execute(sql, params, many, context)
                results[label]["loser_lock_sql_completed"] = True
                return result

            with connection.execute_wrapper(execute_wrapper):
                with identity_provisioning.override_identity_provisioner(organization_identity_provisioner):
                    user = backend.get_or_create_user(
                        "access-token",
                        "id-token",
                        {"iss": ISSUER, "sub": subject},
                    )
            results[label]["user_id"] = user.pk if user is not None else None
        except Exception as exc:
            results[label]["error_type"] = type(exc).__name__
        finally:
            finished[label].set()
            set_current_tenant(None)
            connections.close_all()

    def _run_same_identity_race(self, subject, winner, *, existing_binding=False):
        if existing_binding:
            user = User.objects.create_user(username=f"existing-{subject}", email=f"old-{subject}@example.test")
            OIDCIdentity.objects.create(user=user, issuer=ISSUER, subject=subject)

        before = {
            "users": User.objects.count(),
            "bindings": OIDCIdentity.objects.count(),
            "holders": AssetHolder.objects.count(),
            "memberships": Membership.objects.count(),
            "roles": Role.objects.count(),
            "grants": RoleGrant.objects.count(),
            "scopes": RoleGrantScope.objects.count(),
        }
        results = {"A": {}, "B": {}}
        thread_state = threading.local()
        winner_start_barrier = threading.Barrier(2)
        loser_start_barrier = threading.Barrier(2)
        winner_lock_held = threading.Event()
        loser_lock_sql_started = threading.Event()
        release_winner = threading.Event()
        finished = {"A": threading.Event(), "B": threading.Event()}

        def start_worker(label, start_barrier):
            return threading.Thread(
                target=self._worker,
                args=(
                    label,
                    subject,
                    start_barrier,
                    results,
                    thread_state,
                    winner,
                    winner_lock_held,
                    release_winner,
                    loser_lock_sql_started,
                    finished,
                ),
            )

        winner_thread = start_worker(winner, winner_start_barrier)
        winner_thread.start()
        winner_start_barrier.wait(timeout=5)
        self.assertTrue(winner_lock_held.wait(timeout=10))

        loser = "B" if winner == "A" else "A"
        loser_thread = start_worker(loser, loser_start_barrier)
        loser_thread.start()
        loser_start_barrier.wait(timeout=5)
        self.assertTrue(loser_lock_sql_started.wait(timeout=10))
        self.assertFalse(finished[loser].wait(timeout=0.5))
        self.assertFalse(results[loser].get("loser_lock_sql_completed", False))
        release_winner.set()
        winner_thread.join(timeout=15)
        loser_thread.join(timeout=15)

        self.assertFalse(winner_thread.is_alive())
        self.assertFalse(loser_thread.is_alive())
        for label in ("A", "B"):
            with self.subTest(worker=label):
                self.assertIsNone(results[label].get("error_type"))
                self.assertEqual(results[label].get("lock_sql"), LOCK_SQL)
                self.assertEqual(results[label].get("lock_param_count"), 2)
                self.assertTrue(results[label].get("lock_params_are_int"))
                self.assertGreaterEqual(results[label].get("binding_read_count", 0), 1)
                self.assertTrue(results[label].get("binding_read_exact"))
        self.assertNotEqual(results["A"].get("pid"), results["B"].get("pid"))
        self.assertEqual(results["A"].get("user_id"), results["B"].get("user_id"))

        identity = OIDCIdentity.objects.get(issuer=ISSUER, subject=subject)
        membership = Membership.objects.get(user_id=identity.user_id, tenant=self.tenant)
        fingerprint = {
            "user_delta": User.objects.count() - before["users"],
            "binding_delta": OIDCIdentity.objects.count() - before["bindings"],
            "holder_delta": AssetHolder.objects.count() - before["holders"],
            "membership_delta": Membership.objects.count() - before["memberships"],
            "role_delta": Role.objects.count() - before["roles"],
            "grant_delta": RoleGrant.objects.count() - before["grants"],
            "scope_delta": RoleGrantScope.objects.count() - before["scopes"],
            "total_bindings": OIDCIdentity.objects.filter(issuer=ISSUER, subject=subject).count(),
            "tenant_holders": AssetHolder.objects.filter(tenant=self.tenant).count() - before["holders"],
            "tenant_memberships": Membership.objects.filter(tenant=self.tenant).count() - before["memberships"],
            "tenant_grants": RoleGrant.objects.filter(membership__tenant=self.tenant).count() - before["grants"],
            "tenant_scopes": RoleGrantScope.objects.filter(role_grant__membership__tenant=self.tenant).count()
            - before["scopes"],
            "direct_grant_count": RoleGrant.objects.filter(membership=membership, role=self.role).count(),
            "own_scope_count": RoleGrantScope.objects.filter(
                role_grant__membership=membership,
                role_grant__role=self.role,
                scope_type=RoleGrantScope.SCOPE_OWN,
            ).count(),
        }
        expected_delta = 0 if existing_binding else 1
        self.assertEqual(fingerprint["user_delta"], expected_delta)
        self.assertEqual(fingerprint["binding_delta"], 0 if existing_binding else 1)
        self.assertEqual(fingerprint["holder_delta"], 1 if not existing_binding else 1)
        self.assertEqual(fingerprint["membership_delta"], 1 if not existing_binding else 1)
        self.assertEqual(fingerprint["grant_delta"], 1 if not existing_binding else 1)
        self.assertEqual(fingerprint["scope_delta"], 1 if not existing_binding else 1)
        self.assertEqual(User.objects.count(), before["users"] + expected_delta)
        self.assertEqual(fingerprint["total_bindings"], 1)
        self.assertEqual(fingerprint["tenant_holders"], 1)
        self.assertEqual(fingerprint["tenant_memberships"], 1)
        self.assertEqual(fingerprint["tenant_grants"], 1)
        self.assertEqual(fingerprint["tenant_scopes"], 1)
        self.assertEqual(fingerprint["direct_grant_count"], 1)
        self.assertEqual(fingerprint["own_scope_count"], 1)
        return fingerprint

    def test_both_forced_winner_orders_use_real_lock_contention_and_same_fingerprint(self):
        order_a = self._run_same_identity_race("winner-order-a", "A")
        order_b = self._run_same_identity_race("winner-order-b", "B")
        self.assertEqual(order_a, order_b)

    def test_existing_binding_contention_resolves_one_user_and_one_aggregate(self):
        fingerprint = self._run_same_identity_race("existing-binding", "A", existing_binding=True)
        self.assertEqual(fingerprint["user_delta"], 0)
        self.assertEqual(User.objects.filter(username="existing-existing-binding").count(), 1)

    def test_unique_constraint_backstop_rereads_exact_binding_after_savepoint(self):
        subject = "unique-backstop"
        lookup_empty = threading.Event()
        release_lookup = threading.Event()
        result = {}
        backend = TenantOIDCBackend()
        claims = {"email": "worker@example.test", "given_name": "Worker"}
        backend.get_userinfo = Mock(return_value=claims)
        backend.verify_claims = Mock(return_value=True)
        original_select = backend._select_identity_binding
        select_calls = {"count": 0}

        def paused_select(identity, *, for_update=False):
            selected = original_select(identity, for_update=for_update)
            if select_calls["count"] == 0 and for_update and selected is None:
                select_calls["count"] += 1
                lookup_empty.set()
                if not release_lookup.wait(timeout=10):
                    raise RuntimeError("unique backstop gate timed out")
            return selected

        backend._select_identity_binding = paused_select

        def worker():
            close_old_connections()
            try:
                set_current_tenant(Tenant._base_manager.get(pk=self.tenant.pk))
                with identity_provisioning.override_identity_provisioner(organization_identity_provisioner):
                    user = backend.get_or_create_user(
                        "access-token",
                        "id-token",
                        {"iss": ISSUER, "sub": subject},
                    )
                result["user_id"] = user.pk
            except Exception as exc:
                result["error_type"] = type(exc).__name__
            finally:
                set_current_tenant(None)
                connections.close_all()

        before_users = User.objects.count()
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(lookup_empty.wait(timeout=10))
            canonical_user = User.objects.create_user(username="canonical-user", email="canonical@example.test")
            canonical_binding = OIDCIdentity.objects.create(
                user=canonical_user,
                issuer=ISSUER,
                subject=subject,
            )
        finally:
            release_lookup.set()
            thread.join(timeout=15)
        self.assertFalse(thread.is_alive())
        self.assertIsNone(result.get("error_type"))
        self.assertEqual(result.get("user_id"), canonical_user.pk)
        self.assertEqual(User.objects.count(), before_users + 1)
        self.assertEqual(OIDCIdentity.objects.filter(issuer=ISSUER, subject=subject).count(), 1)
        self.assertEqual(AssetHolder.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(Membership.objects.filter(tenant=self.tenant).count(), 1)
        membership = Membership.objects.get(user=canonical_user, tenant=self.tenant)
        self.assertEqual(RoleGrant.objects.filter(membership=membership, role=self.role).count(), 1)
        grant = RoleGrant.objects.get(membership=membership, role=self.role)
        self.assertEqual(RoleGrantScope.objects.filter(role_grant=grant).count(), 1)
        self.assertEqual(canonical_binding.user_id, canonical_user.pk)

    def test_absent_reread_after_integrity_error_is_safe_and_zero_write(self):
        backend = TenantOIDCBackend()
        identity = VerifiedOIDCIdentity(issuer=ISSUER, subject="absent-reread")
        before = {
            "users": User.objects.count(),
            "bindings": OIDCIdentity.objects.count(),
            "holders": AssetHolder.objects.count(),
            "memberships": Membership.objects.count(),
            "roles": Role.objects.count(),
            "grants": RoleGrant.objects.count(),
            "scopes": RoleGrantScope.objects.count(),
        }
        with transaction.atomic():
            with patch.object(OIDCIdentity.objects, "create", side_effect=IntegrityError()):
                with self.assertRaises(OIDCIdentityProvisioningError) as caught:
                    backend._create_user_and_binding(identity, {"email": "absent@example.test"})
        self.assertEqual(str(caught.exception), "OIDC identity provisioning could not be completed.")
        self.assertEqual(
            {
                "users": User.objects.count(),
                "bindings": OIDCIdentity.objects.count(),
                "holders": AssetHolder.objects.count(),
                "memberships": Membership.objects.count(),
                "roles": Role.objects.count(),
                "grants": RoleGrant.objects.count(),
                "scopes": RoleGrantScope.objects.count(),
            },
            before,
        )
