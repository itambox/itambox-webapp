import threading
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings

from core.auth.oidc import (
    TenantOIDCBackend,
    VerifiedOIDCIdentity,
    _acquire_oidc_identity_lock,
)
from core.managers import set_current_tenant
from organization.models import AssetHolder, Membership, Role, RoleGrant, RoleGrantScope, Tenant
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
    }
}


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
        self.assertEqual(sql, "SELECT pg_advisory_xact_lock(%s::integer, %s::integer)")
        self.assertIsInstance(params, tuple)
        self.assertEqual(len(params), 2)
        self.assertTrue(all(isinstance(value, int) for value in params))
        rendered = repr(cursor.calls)
        self.assertNotIn(ISSUER, rendered)
        self.assertNotIn("parameterized-subject", rendered)

    def _worker(self, label, subject, start_barrier, results, thread_state):
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
                "family_name": label,
            }
            backend.get_userinfo = lambda *args, **kwargs: claims
            backend.verify_claims = lambda user_info: True
            thread_state.label = label
            start_barrier.wait(timeout=5)
            user = backend.get_or_create_user(
                "access-token",
                "id-token",
                {"iss": ISSUER, "sub": subject},
            )
            results[label]["user_id"] = user.pk if user is not None else None
        except Exception as exc:
            results[label]["error_type"] = type(exc).__name__
        finally:
            set_current_tenant(None)
            connections.close_all()

    def _run_same_identity_race(self, subject, winner, *, existing_binding=False):
        if existing_binding:
            user = User.objects.create_user(username=f"existing-{subject}", email=f"old-{subject}@example.test")
            OIDCIdentity.objects.create(user=user, issuer=ISSUER, subject=subject)

        results = {"A": {}, "B": {}}
        thread_state = threading.local()
        start_barrier = threading.Barrier(3)
        winner_locked = threading.Event()
        release_winner = threading.Event()
        non_winner_finished = threading.Event()
        original_lock = __import__(
            "core.auth.oidc", fromlist=["_acquire_oidc_identity_lock"]
        )._acquire_oidc_identity_lock

        def gated_lock(cursor, identity):
            label = thread_state.label
            if label != winner and not winner_locked.wait(timeout=5):
                raise RuntimeError("winner lock gate timed out")
            original_lock(cursor, identity)
            if label == winner:
                winner_locked.set()
                if not release_winner.wait(timeout=5):
                    raise RuntimeError("winner post-lock gate timed out")

        def worker(label):
            self._worker(label, subject, start_barrier, results, thread_state)
            if label != winner:
                non_winner_finished.set()

        threads = [threading.Thread(target=worker, args=(label,)) for label in ("A", "B")]
        with patch("core.auth.oidc._acquire_oidc_identity_lock", side_effect=gated_lock):
            for thread in threads:
                thread.start()
            start_barrier.wait(timeout=5)
            self.assertTrue(winner_locked.wait(timeout=5))
            self.assertFalse(non_winner_finished.wait(timeout=0.2))
            release_winner.set()
            for thread in threads:
                thread.join(timeout=15)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(results["A"].get("error_type"), None)
        self.assertEqual(results["B"].get("error_type"), None)
        self.assertNotEqual(results["A"].get("pid"), results["B"].get("pid"))
        self.assertEqual(results["A"].get("user_id"), results["B"].get("user_id"))

        identity = OIDCIdentity.objects.get(issuer=ISSUER, subject=subject)
        membership = Membership.objects.get(user_id=identity.user_id, tenant=self.tenant)
        fingerprint = {
            "user_count": User.objects.filter(pk=identity.user_id).count(),
            "binding_count": OIDCIdentity.objects.filter(issuer=ISSUER, subject=subject).count(),
            "membership_count": Membership.objects.filter(user_id=identity.user_id, tenant=self.tenant).count(),
            "holder_count": AssetHolder.objects.filter(user_id=identity.user_id, tenant=self.tenant).count(),
            "direct_grant_count": RoleGrant.objects.filter(membership=membership, role=self.role).count(),
            "own_scope_count": RoleGrantScope.objects.filter(
                role_grant__membership=membership,
                role_grant__role=self.role,
                scope_type=RoleGrantScope.SCOPE_OWN,
            ).count(),
        }
        self.assertEqual(
            fingerprint,
            {
                "user_count": 1,
                "binding_count": 1,
                "membership_count": 1,
                "holder_count": 1,
                "direct_grant_count": 1,
                "own_scope_count": 1,
            },
        )
        return fingerprint

    def test_advisory_winner_order_a_converges_one_access_aggregate(self):
        fingerprint = self._run_same_identity_race("winner-a", "A")
        self.assertEqual(fingerprint["direct_grant_count"], 1)

    def test_advisory_winner_order_b_has_same_semantic_fingerprint(self):
        fingerprint = self._run_same_identity_race("winner-b", "B")
        self.assertEqual(fingerprint["direct_grant_count"], 1)

    def test_existing_binding_concurrency_resolves_one_user_and_one_aggregate(self):
        self._run_same_identity_race("existing-binding", "A", existing_binding=True)
        self.assertEqual(User.objects.filter(username="existing-existing-binding").count(), 1)

    def test_unique_constraint_backstop_rereads_exact_binding_after_savepoint(self):
        subject = "unique-backstop"
        start_barrier = threading.Barrier(2)
        lookup_empty = threading.Event()
        release_lookup = threading.Event()
        result = {}
        backend = TenantOIDCBackend()
        claims = {"email": "worker@example.test", "given_name": "Worker"}
        backend.get_userinfo = lambda *args, **kwargs: claims
        backend.verify_claims = lambda user_info: True
        original_select = backend._select_identity_binding
        select_calls = {"count": 0}

        def paused_select(identity, *, for_update=False):
            selected = original_select(identity, for_update=for_update)
            if select_calls["count"] == 0 and for_update and selected is None:
                select_calls["count"] += 1
                lookup_empty.set()
                if not release_lookup.wait(timeout=5):
                    raise RuntimeError("unique backstop gate timed out")
            return selected

        backend._select_identity_binding = paused_select

        def worker():
            close_old_connections()
            try:
                set_current_tenant(Tenant._base_manager.get(pk=self.tenant.pk))
                start_barrier.wait(timeout=5)
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

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            start_barrier.wait(timeout=5)
            self.assertTrue(lookup_empty.wait(timeout=5))

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
        self.assertEqual(result.get("error_type"), None)
        self.assertEqual(result.get("user_id"), canonical_user.pk)
        self.assertEqual(OIDCIdentity.objects.filter(issuer=ISSUER, subject=subject).count(), 1)
        self.assertEqual(User.objects.filter(pk=canonical_user.pk).count(), 1)
        self.assertEqual(canonical_binding.user_id, canonical_user.pk)
        membership = Membership.objects.get(user=canonical_user, tenant=self.tenant)
        self.assertEqual(Membership.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(RoleGrant.objects.filter(membership=membership, role=self.role).count(), 1)
        self.assertEqual(
            RoleGrantScope.objects.filter(
                role_grant__membership=membership,
                role_grant__role=self.role,
                scope_type=RoleGrantScope.SCOPE_OWN,
            ).count(),
            1,
        )
