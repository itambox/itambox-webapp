"""Real PostgreSQL concurrency proofs for the #443 identity aggregate."""

from __future__ import annotations

import json
import threading
import time
from datetime import timedelta
from threading import Barrier, BrokenBarrierError, Event
from unittest import mock

import pytest
from django.db import connection, connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

import organization.services.identity_provisioning as identity_service
from core.identity_provisioning import ExternalIdentityProfile, ExternalIdentityProvisioningCommand, ProviderStaffIntent
from organization.models import AssetHolder, Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.services.identity_provisioning import (
    LDAP_DIRECTORY_SYNC_REASON,
    LDAPDirectoryIdentityCommand,
    OrganizationIdentityProvisioner,
    provision_ldap_directory_identity,
)
from users.models import OIDCIdentity, User

pytestmark = pytest.mark.serial_only


class IdentityServiceConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.provisioner = OrganizationIdentityProvisioner()
        self.provider = Tenant.objects.create(
            name="Concurrency Provider", slug="concurrency-provider", is_provider=True
        )
        self.customer = Tenant.objects.create(
            name="Concurrency Customer",
            slug="concurrency-customer",
            managed_by=self.provider,
        )
        self.member_role = Role.objects.create(
            tenant=self.customer,
            name="Member",
            permissions=["assets.view_asset"],
        )
        self.provider_role = Role.objects.create(
            tenant=self.provider,
            name="ProviderStaff",
            permissions=["assets.view_asset"],
        )

    def _command(self, user, *, role="Member", provider_staff=None, email=None, upn=None):
        return ExternalIdentityProvisioningCommand(
            user=user,
            customer_tenant=self.customer,
            profile=ExternalIdentityProfile(
                source="OIDC",
                email=email or user.email,
                upn=upn or f"{user.username}@concurrency.invalid",
                first_name="Concurrent",
                last_name="Identity",
            ),
            customer_role_name=role,
            provider_staff=provider_staff,
        )

    @staticmethod
    def _set_local_timeouts():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '2s'")
            cursor.execute("SET LOCAL statement_timeout = '10s'")

    @staticmethod
    def _run(command, output, before_execute=None, after_execute=None, provision=None):
        try:
            connections.close_all()
            connection.ensure_connection()
            backend = connection.connection
            if backend is None:
                raise AssertionError("worker connection did not initialize")
            output["backend_pid"] = backend.get_backend_pid()
            user_id = getattr(command.user, "pk", "unknown")
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT set_config('application_name', %s, false)",
                    [f"itambox443r-{threading.current_thread().name}-user-{user_id}"],
                )
                cursor.execute("SET SESSION lock_timeout = '10s'")
                cursor.execute("SET SESSION statement_timeout = '30s'")
            operation = provision or OrganizationIdentityProvisioner().provision
            if before_execute is None:
                output["result"] = operation(command)
            else:

                def wrapper(execute, sql, params, many, context):
                    before_execute(sql)
                    result = execute(sql, params, many, context)
                    if after_execute is not None:
                        after_execute(sql)
                    return result

                with connection.execute_wrapper(wrapper):
                    output["result"] = operation(command)
        except Exception as exc:  # the main thread asserts the exact class
            output["error"] = exc
        finally:
            connections.close_all()

    @staticmethod
    def _run_function(function, output, before_execute=None):
        try:
            connections.close_all()
            if before_execute is None:
                output["result"] = function()
            else:

                def wrapper(execute, sql, params, many, context):
                    before_execute(sql)
                    return execute(sql, params, many, context)

                with connection.execute_wrapper(wrapper):
                    output["result"] = function()
        except Exception as exc:
            output["error"] = exc
        finally:
            connections.close_all()

    def _join_bounded(self, threads):
        for thread in threads:
            thread.join(timeout=15)
            self.assertFalse(thread.is_alive(), f"worker {thread.name} exceeded the bounded join")

    def _wait_for_user_lock_wait(self, *, waiting_pid, blocking_pid, user_id):
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
                application_name = row[10] or ""
                if (
                    row[0] == waiting_pid
                    and row[1] == "Lock"
                    and row[3] in {"transactionid", "tuple"}
                    and row[7] == blocking_pid
                    and 'from "users_user"' in waiting_query
                    and "for update" in waiting_query
                    and f"user-{user_id}" in application_name
                ):
                    return row
            Event().wait(0.02)
        self.fail(f"backend {waiting_pid} never waited on User row held by {blocking_pid}: {observations[-5:]}")

    def test_same_canonical_user_concurrent_customer_calls_converge_one_aggregate(self):
        user = User.objects.create_user(username="same-user-443", email="same-user-443@example.invalid")
        first_locked = Event()
        release_first = Event()
        first_thread_name = "same-user-first"

        def checkpoint(stage):
            if stage != "locks.user":
                return
            self._set_local_timeouts()
            if threading.current_thread().name == first_thread_name:
                first_locked.set()
                self.assertTrue(release_first.wait(timeout=8))

        outputs = [{}, {}]
        threads = [
            threading.Thread(
                target=self._run,
                name=first_thread_name,
                args=(self._command(user), outputs[0]),
            ),
            threading.Thread(
                target=self._run,
                name="same-user-second",
                args=(self._command(user), outputs[1]),
            ),
        ]
        with mock.patch("organization.services.identity_provisioning._stage_checkpoint", side_effect=checkpoint):
            for thread in threads:
                thread.start()
            self.assertTrue(first_locked.wait(timeout=8), "first worker did not reach the User lock")
            release_first.set()
            self._join_bounded(threads)

        self.assertEqual([output.get("error") for output in outputs], [None, None])
        self.assertEqual(Membership.objects.filter(user=user, tenant=self.customer).count(), 1)
        membership = Membership.objects.get(user=user, tenant=self.customer)
        self.assertEqual(RoleGrant.objects.filter(membership=membership).count(), 1)
        self.assertEqual(RoleGrantScope.objects.filter(role_grant__membership=membership).count(), 1)
        self.assertEqual(AssetHolder.objects.filter(user=user, tenant=self.customer).count(), 1)
        self.assertEqual({output["result"].membership_id for output in outputs}, {membership.pk})

    def test_public_ldap_calls_serialize_missing_role_membership_and_grant(self):
        self.member_role.delete()
        user = User.objects.create_user(username="ldap-race-443", email="ldap-race-443@example.invalid")
        first_name = "ldap-race-first"
        first_role_insert_reached = Event()
        role_insert_barrier = Barrier(2)
        first_membership_insert_reached = Event()
        membership_insert_barrier = Barrier(2)
        second_backend_started = Event()

        def observe_first(sql):
            normalized = " ".join(sql.split()).lower()
            if threading.current_thread().name != first_name:
                second_backend_started.set()
                return
            if normalized.startswith('insert into "organization_role"'):
                first_role_insert_reached.set()
                try:
                    role_insert_barrier.wait(timeout=8)
                except BrokenBarrierError as exc:
                    raise AssertionError("LDAP Role INSERT barrier was not released") from exc
            elif normalized.startswith('insert into "organization_membership"'):
                first_membership_insert_reached.set()
                try:
                    membership_insert_barrier.wait(timeout=8)
                except BrokenBarrierError as exc:
                    raise AssertionError("LDAP Membership INSERT barrier was not released") from exc

        command = LDAPDirectoryIdentityCommand(user=user, tenant=self.customer)
        outputs = [{}, {}]
        first = threading.Thread(
            target=self._run,
            name=first_name,
            args=(command, outputs[0]),
            kwargs={"before_execute": observe_first, "provision": provision_ldap_directory_identity},
        )
        second = threading.Thread(
            target=self._run,
            name="ldap-race-second",
            args=(command, outputs[1]),
            kwargs={"before_execute": observe_first, "provision": provision_ldap_directory_identity},
        )
        started_threads = []
        role_barrier_released = False
        membership_barrier_released = False
        try:
            first.start()
            started_threads.append(first)
            self.assertTrue(first_role_insert_reached.wait(timeout=8), "LDAP call did not reach real Role INSERT")
            second.start()
            started_threads.append(second)
            self.assertTrue(second_backend_started.wait(timeout=8), "second LDAP backend did not start")
            observed_wait = self._wait_for_user_lock_wait(
                waiting_pid=outputs[1]["backend_pid"],
                blocking_pid=outputs[0]["backend_pid"],
                user_id=user.pk,
            )
            self.assertEqual(observed_wait[0], outputs[1]["backend_pid"])
            self.assertEqual(observed_wait[7], outputs[0]["backend_pid"])
            role_insert_barrier.wait(timeout=8)
            role_barrier_released = True
            self.assertTrue(
                first_membership_insert_reached.wait(timeout=8),
                "LDAP call did not reach real Membership INSERT",
            )
            membership_insert_barrier.wait(timeout=8)
            membership_barrier_released = True
        finally:
            if not role_barrier_released:
                role_insert_barrier.abort()
            if not membership_barrier_released:
                membership_insert_barrier.abort()
            self._join_bounded(started_threads)

        self.assertEqual([output.get("error") for output in outputs], [None, None])
        self.assertEqual(
            Role._base_manager.filter(tenant=self.customer, name="Member", deleted_at__isnull=True).count(), 1
        )
        role = Role._base_manager.get(tenant=self.customer, name="Member", deleted_at__isnull=True)
        membership = Membership.objects.get(user=user, tenant=self.customer)
        grants = list(
            RoleGrant._base_manager.filter(
                membership=membership,
                role=role,
                reason=LDAP_DIRECTORY_SYNC_REASON,
                granted_by__isnull=True,
            )
        )
        self.assertEqual(Membership.objects.filter(user=user, tenant=self.customer).count(), 1)
        self.assertEqual(len(grants), 1)
        grant = grants[0]
        self.assertIsNone(grant.granted_by_id)
        self.assertIsNotNone(grant.valid_until)
        now = timezone.now()
        self.assertGreaterEqual(grant.valid_until, now + timedelta(hours=23))
        self.assertLessEqual(grant.valid_until, now + timedelta(days=1, minutes=1))
        self.assertEqual(
            list(RoleGrantScope.objects.filter(role_grant=grant).values_list("scope_type", flat=True)),
            [RoleGrantScope.SCOPE_OWN],
        )
        self.assertEqual(AssetHolder.objects.filter(user=user, tenant=self.customer).count(), 0)

    def test_different_users_same_tenant_shared_lock_coexists(self):
        users = [
            User.objects.create_user(username="different-a-443", email="different-a-443@example.invalid"),
            User.objects.create_user(username="different-b-443", email="different-b-443@example.invalid"),
        ]
        user_lock_barrier = Barrier(2)

        def checkpoint(stage):
            if stage != "locks.user":
                return
            self._set_local_timeouts()
            try:
                user_lock_barrier.wait(timeout=8)
            except BrokenBarrierError as exc:
                raise AssertionError("same-tenant users did not coexist after independent User locks") from exc

        outputs = [{}, {}]
        threads = [
            threading.Thread(target=self._run, name="different-user-a", args=(self._command(users[0]), outputs[0])),
            threading.Thread(target=self._run, name="different-user-b", args=(self._command(users[1]), outputs[1])),
        ]
        with mock.patch("organization.services.identity_provisioning._stage_checkpoint", side_effect=checkpoint):
            for thread in threads:
                thread.start()
            self._join_bounded(threads)

        self.assertEqual([output.get("error") for output in outputs], [None, None])
        self.assertEqual(Membership.objects.filter(tenant=self.customer).count(), 2)
        self.assertEqual(RoleGrant.objects.filter(membership__tenant=self.customer).count(), 2)
        self.assertEqual(AssetHolder.objects.filter(tenant=self.customer).count(), 2)

    def _run_ordered_provider_customer(self, first_mode: str):
        user = User.objects.create_user(
            username=f"order-{first_mode}-443",
            email=f"order-{first_mode}-443@example.invalid",
        )
        provider_command = self._command(
            user,
            provider_staff=ProviderStaffIntent(provider_tenant=self.provider, role_name="ProviderStaff"),
        )
        customer_command = self._command(user)
        first_command = provider_command if first_mode == "provider" else customer_command
        second_command = customer_command if first_mode == "provider" else provider_command
        first_name = f"{first_mode}-first"
        first_locked = Event()
        second_backend_started = Event()
        release_first = Event()

        def checkpoint(stage):
            if stage != "locks.user":
                return
            self._set_local_timeouts()
            if threading.current_thread().name == first_name:
                first_locked.set()
                if not release_first.wait(timeout=8):
                    raise AssertionError(f"{first_mode} winner was not released")

        def observe_second_backend(sql):
            del sql
            if threading.current_thread().name != first_name:
                second_backend_started.set()

        outputs = [{}, {}]
        first = threading.Thread(target=self._run, name=first_name, args=(first_command, outputs[0]))
        second = threading.Thread(
            target=self._run,
            name=f"{first_mode}-second",
            args=(second_command, outputs[1]),
            kwargs={"before_execute": observe_second_backend},
        )
        started_threads = []
        try:
            with mock.patch("organization.services.identity_provisioning._stage_checkpoint", side_effect=checkpoint):
                first.start()
                started_threads.append(first)
                self.assertTrue(first_locked.wait(timeout=8), f"{first_mode} command did not reach the User lock")
                second.start()
                started_threads.append(second)
                self.assertTrue(second_backend_started.wait(timeout=8), f"{first_mode} second backend did not start")
                observed_wait = self._wait_for_user_lock_wait(
                    waiting_pid=outputs[1]["backend_pid"],
                    blocking_pid=outputs[0]["backend_pid"],
                    user_id=user.pk,
                )
                self.assertEqual(observed_wait[0], outputs[1]["backend_pid"])
                self.assertEqual(observed_wait[7], outputs[0]["backend_pid"])
                release_first.set()
        finally:
            release_first.set()
            self._join_bounded(started_threads)

        self.assertEqual([output.get("error") for output in outputs], [None, None])
        provider_membership = Membership.objects.get(user=user, tenant=self.provider)
        self.assertTrue(provider_membership.is_active)
        self.assertFalse(Membership.objects.filter(user=user, tenant=self.customer).exists())
        self.assertFalse(RoleGrant.objects.filter(membership=provider_membership).exists())
        self.assertFalse(AssetHolder.objects.filter(user=user, tenant=self.customer, user__isnull=False).exists())
        self.assertEqual(Membership.objects.filter(user=user).count(), 1)

    def test_provider_first_then_stale_customer_is_provider_only(self):
        self._run_ordered_provider_customer("provider")

    def test_customer_first_then_provider_transition_is_provider_only(self):
        self._run_ordered_provider_customer("customer")

    def test_repeated_provider_transition_remains_provider_only(self):
        user = User.objects.create_user(username="repeat-provider-443", email="repeat-provider-443@example.invalid")
        command = self._command(
            user,
            provider_staff=ProviderStaffIntent(provider_tenant=self.provider, role_name="ProviderStaff"),
        )
        first = self.provisioner.provision(command)
        second = self.provisioner.provision(command)

        self.assertEqual(first.mode, "provider_staff")
        self.assertEqual(second.mode, "provider_staff")
        self.assertEqual(Membership.objects.filter(user=user, tenant=self.provider).count(), 1)
        self.assertFalse(Membership.objects.filter(user=user, tenant=self.customer).exists())
        self.assertFalse(RoleGrant.objects.filter(membership__user=user).exists())

    def test_real_role_uniqueness_conflict_rereads_exact_live_role(self):
        users = [
            User.objects.create_user(username="role-race-a-443", email="role-race-a-443@example.invalid"),
            User.objects.create_user(username="role-race-b-443", email="role-race-b-443@example.invalid"),
        ]
        role_lock_barrier = Barrier(2)
        role_lock_seen = threading.local()

        def observe_role_lock(sql):
            normalized = " ".join(sql.split()).lower()
            if (
                'from "organization_role"' in normalized
                and normalized.startswith("select")
                and not getattr(role_lock_seen, "initial", False)
            ):
                role_lock_seen.initial = True
                role_lock_barrier.wait(timeout=8)

        outputs = [{}, {}]
        threads = [
            threading.Thread(
                target=self._run,
                name="role-race-a",
                args=(self._command(users[0], role="Manager"), outputs[0]),
                kwargs={"before_execute": observe_role_lock},
            ),
            threading.Thread(
                target=self._run,
                name="role-race-b",
                args=(self._command(users[1], role="Manager"), outputs[1]),
                kwargs={"before_execute": observe_role_lock},
            ),
        ]
        for thread in threads:
            thread.start()
        self._join_bounded(threads)

        self.assertEqual([output.get("error") for output in outputs], [None, None])
        roles = list(Role._base_manager.filter(tenant=self.customer, name="Manager"))
        self.assertEqual(len(roles), 1)
        self.assertEqual({output["result"].role_id for output in outputs}, {roles[0].pk})
        self.assertEqual(Membership.objects.filter(tenant=self.customer).count(), 2)

    def test_real_membership_uniqueness_conflict_rereads_exact_user_tenant_pair(self):
        user = User.objects.create_user(username="membership-race-443", email="membership-race-443@example.invalid")
        insert_barrier = Barrier(2)

        def observe_membership_insert(sql):
            normalized = " ".join(sql.split()).lower()
            if normalized.startswith('insert into "organization_membership"'):
                insert_barrier.wait(timeout=8)

        outputs = [{}, {}]

        def create_membership():
            with transaction.atomic():
                return identity_service._create_membership(user_id=user.pk, tenant_id=self.customer.pk)

        threads = [
            threading.Thread(
                target=self._run_function,
                name="membership-race-a",
                args=(create_membership, outputs[0]),
                kwargs={"before_execute": observe_membership_insert},
            ),
            threading.Thread(
                target=self._run_function,
                name="membership-race-b",
                args=(create_membership, outputs[1]),
                kwargs={"before_execute": observe_membership_insert},
            ),
        ]
        for thread in threads:
            thread.start()
        self._join_bounded(threads)

        self.assertEqual([output.get("error") for output in outputs], [None, None])
        membership = Membership.objects.get(user=user, tenant=self.customer)
        self.assertEqual({output["result"][0].pk for output in outputs}, {membership.pk})

    def test_public_holder_uniqueness_conflict_keeps_one_winner_and_loser_aggregate(self):
        Role.objects.create(
            tenant=self.customer,
            name="Manager",
            permissions=["assets.view_asset"],
        )
        users = [
            User.objects.create_user(username="holder-race-a-443", email="holder-race-a-443@example.invalid"),
            User.objects.create_user(username="holder-race-b-443", email="holder-race-b-443@example.invalid"),
        ]
        shared_upn = "shared-holder-race-443@example.invalid"
        emails = [user.email for user in users]
        insert_barrier = Barrier(2)

        def observe_holder_insert(sql):
            normalized = " ".join(sql.split()).lower()
            if normalized.startswith('insert into "organization_assetholder"'):
                try:
                    insert_barrier.wait(timeout=8)
                except BrokenBarrierError as exc:
                    raise AssertionError("both public calls did not reach the real holder INSERT") from exc

        commands = [
            self._command(users[0], role="Member", email=emails[0], upn=shared_upn),
            self._command(users[1], role="Manager", email=emails[1], upn=shared_upn),
        ]
        outputs = [{}, {}]
        threads = [
            threading.Thread(
                target=self._run,
                name="holder-race-a",
                args=(commands[0], outputs[0]),
                kwargs={"before_execute": observe_holder_insert},
            ),
            threading.Thread(
                target=self._run,
                name="holder-race-b",
                args=(commands[1], outputs[1]),
                kwargs={"before_execute": observe_holder_insert},
            ),
        ]
        with self.assertLogs("itambox.organization.identity", level="WARNING") as logs:
            for thread in threads:
                thread.start()
            self._join_bounded(threads)

        self.assertEqual([output.get("error") for output in outputs], [None, None])
        holders = list(AssetHolder._base_manager.filter(tenant=self.customer, upn=shared_upn))
        self.assertEqual(len(holders), 1)
        holder = holders[0]
        result_holder_ids = [output["result"].holder_id for output in outputs]
        self.assertCountEqual(result_holder_ids, [holder.pk, None])
        self.assertIn(holder.user_id, {users[0].pk, users[1].pk})
        for user, output in zip(users, outputs, strict=True):
            membership = Membership.objects.get(user=user, tenant=self.customer)
            self.assertEqual(Membership.objects.filter(user=user, tenant=self.customer).count(), 1)
            self.assertEqual(RoleGrant.objects.filter(membership=membership).count(), 1)
            grant = RoleGrant.objects.get(membership=membership)
            self.assertEqual(RoleGrantScope.objects.filter(role_grant=grant).count(), 1)
            self.assertEqual(output["result"].membership_id, membership.pk)
        self.assertGreaterEqual(len(logs.records), 1)
        for record in logs.records:
            self.assertEqual(record.__dict__.get("reason_code"), "holder_collision_unresolved")
            self.assertIsNone(record.exc_info)
            rendered = json.dumps(record.__dict__, default=str, sort_keys=True)
            self.assertNotIn(shared_upn, rendered)
            for email in emails:
                self.assertNotIn(email, rendered)

    def test_real_scope_uniqueness_conflict_rereads_exact_scope(self):
        membership = Membership.objects.create(
            user=User.objects.create_user(username="scope-race-443"), tenant=self.customer
        )
        grant = RoleGrant.objects.create(membership=membership, role=self.member_role)
        insert_barrier = Barrier(2)

        def observe_scope_insert(sql):
            normalized = " ".join(sql.split()).lower()
            if normalized.startswith('insert into "organization_rolegrantscope"'):
                insert_barrier.wait(timeout=8)

        def ensure_scope():
            live_grant = RoleGrant._base_manager.get(pk=grant.pk)
            return identity_service._ensure_own_scope(live_grant)

        outputs = [{}, {}]
        threads = [
            threading.Thread(
                target=self._run_function,
                name="scope-race-a",
                args=(ensure_scope, outputs[0]),
                kwargs={"before_execute": observe_scope_insert},
            ),
            threading.Thread(
                target=self._run_function,
                name="scope-race-b",
                args=(ensure_scope, outputs[1]),
                kwargs={"before_execute": observe_scope_insert},
            ),
        ]
        for thread in threads:
            thread.start()
        self._join_bounded(threads)

        self.assertEqual([output.get("error") for output in outputs], [None, None])
        self.assertEqual(
            RoleGrantScope.objects.filter(role_grant=grant, scope_type=RoleGrantScope.SCOPE_OWN).count(), 1
        )

    def _synthetic_onboarding_worker(self, user, events, output):
        try:
            connections.close_all()
            with transaction.atomic():
                Tenant._base_manager.select_for_update().get(pk=self.customer.pk)
                events["onboarding_tenant_locked"].set()
                if not events["service_tenant_attempted"].wait(timeout=8):
                    raise AssertionError("service did not reach the contested Tenant lock")
                User._base_manager.select_for_update().get(pk=user.pk)
                events["onboarding_user_locked"].set()
        except Exception as exc:
            output["error"] = exc
        finally:
            connections.close_all()

    def _synthetic_service_worker(self, user, binding, events, output):
        try:
            connections.close_all()
            with transaction.atomic():
                OIDCIdentity._base_manager.select_for_update().get(pk=binding.pk)
                events["binding_locked"].set()
                if not events["onboarding_tenant_locked"].wait(timeout=8):
                    raise AssertionError("onboarding did not acquire Tenant first")

                def observe(sql):
                    normalized = " ".join(sql.split()).lower()
                    if "organization_tenant" in normalized and "for share" in normalized:
                        events["service_tenant_attempted"].set()

                self._set_local_timeouts()
                with connection.execute_wrapper(
                    lambda execute, sql, params, many, context: (
                        observe(sql),
                        execute(sql, params, many, context),
                    )[1]
                ):
                    output["result"] = OrganizationIdentityProvisioner().provision(self._command(user))
        except Exception as exc:
            output["error"] = exc
        finally:
            connections.close_all()

    def test_binding_row_lock_and_onboarding_tenant_user_order_are_deadlock_compatible(self):
        user = User.objects.create_user(username="binding-handoff-443", email="binding-handoff-443@example.invalid")
        binding = OIDCIdentity.objects.create(
            user=user,
            issuer="https://issuer.binding-handoff-443.invalid",
            subject="subject-binding-handoff-443",
        )
        events = {
            "onboarding_tenant_locked": Event(),
            "service_tenant_attempted": Event(),
            "binding_locked": Event(),
            "onboarding_user_locked": Event(),
        }
        outputs = [{}, {}]
        onboarding = threading.Thread(
            target=self._synthetic_onboarding_worker,
            name="synthetic-onboarding",
            args=(user, events, outputs[1]),
        )
        service = threading.Thread(
            target=self._synthetic_service_worker,
            name="binding-then-service",
            args=(user, binding, events, outputs[0]),
        )
        onboarding.start()
        self.assertTrue(events["onboarding_tenant_locked"].wait(timeout=8))
        service.start()
        self.assertTrue(events["binding_locked"].wait(timeout=8))
        self.assertTrue(events["service_tenant_attempted"].wait(timeout=8))
        self.assertTrue(events["onboarding_user_locked"].wait(timeout=8))
        self._join_bounded((onboarding, service))

        self.assertEqual(outputs[1].get("error"), None)
        self.assertEqual(outputs[0].get("error"), None)
        self.assertEqual(outputs[0]["result"].mode, "customer")
        self.assertEqual(Membership.objects.filter(user=user, tenant=self.customer).count(), 1)
