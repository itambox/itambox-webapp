"""Real PostgreSQL concurrency proofs for the #443 identity aggregate."""

from __future__ import annotations

import threading
from threading import Barrier, BrokenBarrierError, Event
from unittest import mock

import pytest
from django.db import connection, connections, transaction
from django.test import TransactionTestCase

import organization.services.identity_provisioning as identity_service
from core.identity_provisioning import ExternalIdentityProfile, ExternalIdentityProvisioningCommand, ProviderStaffIntent
from organization.models import AssetHolder, Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.services.identity_provisioning import OrganizationIdentityProvisioner
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
    def _run(command, output, before_execute=None):
        try:
            connections.close_all()
            if before_execute is None:
                output["result"] = OrganizationIdentityProvisioner().provision(command)
            else:

                def wrapper(execute, sql, params, many, context):
                    before_execute(sql)
                    return execute(sql, params, many, context)

                with connection.execute_wrapper(wrapper):
                    output["result"] = OrganizationIdentityProvisioner().provision(command)
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
        second_lock_attempted = Event()
        release_first = Event()

        def checkpoint(stage):
            if stage != "locks.user":
                return
            self._set_local_timeouts()
            if threading.current_thread().name == first_name:
                first_locked.set()
                self.assertTrue(release_first.wait(timeout=8))

        def observe_second_lock(sql):
            normalized = " ".join(sql.split()).lower()
            if 'from "users_user"' in normalized and "for update" in normalized:
                second_lock_attempted.set()

        outputs = [{}, {}]
        first = threading.Thread(target=self._run, name=first_name, args=(first_command, outputs[0]))
        second = threading.Thread(
            target=self._run,
            name=f"{first_mode}-second",
            args=(second_command, outputs[1]),
            kwargs={"before_execute": observe_second_lock},
        )
        with mock.patch("organization.services.identity_provisioning._stage_checkpoint", side_effect=checkpoint):
            first.start()
            self.assertTrue(first_locked.wait(timeout=8), f"{first_mode} command did not reach the User lock")
            second.start()
            self.assertTrue(
                second_lock_attempted.wait(timeout=8),
                f"{first_mode} second transaction did not reach the contested User lock before release",
            )
            release_first.set()
            self._join_bounded((first, second))

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

    def test_real_holder_uniqueness_conflict_never_steals_linked_holder(self):
        users = [
            User.objects.create_user(username="holder-race-a-443", email="holder-race-a-443@example.invalid"),
            User.objects.create_user(username="holder-race-b-443", email="holder-race-b-443@example.invalid"),
        ]
        insert_barrier = Barrier(2)

        def observe_holder_insert(sql):
            normalized = " ".join(sql.split()).lower()
            if normalized.startswith('insert into "organization_assetholder"'):
                insert_barrier.wait(timeout=8)

        def create_holder(user_id):
            live_user = User._base_manager.get(pk=user_id)
            return identity_service._create_holder(
                user=live_user,
                tenant_id=self.customer.pk,
                upn="shared-holder-race-443@example.invalid",
                email=f"holder-{user_id}@example.invalid",
                first_name="Concurrent",
                last_name="Holder",
                source="OIDC",
            )

        outputs = [{}, {}]
        threads = [
            threading.Thread(
                target=self._run_function,
                name="holder-race-a",
                args=(lambda: create_holder(users[0].pk), outputs[0]),
                kwargs={"before_execute": observe_holder_insert},
            ),
            threading.Thread(
                target=self._run_function,
                name="holder-race-b",
                args=(lambda: create_holder(users[1].pk), outputs[1]),
                kwargs={"before_execute": observe_holder_insert},
            ),
        ]
        for thread in threads:
            thread.start()
        self._join_bounded(threads)

        self.assertEqual([output.get("error") for output in outputs], [None, None])
        holders = list(
            AssetHolder._base_manager.filter(tenant=self.customer, upn="shared-holder-race-443@example.invalid")
        )
        self.assertEqual(len(holders), 1)
        self.assertEqual(
            {output["result"].pk if output["result"] is not None else None for output in outputs}, {holders[0].pk, None}
        )
        self.assertIn(holders[0].user_id, {users[0].pk, users[1].pk})

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

    def test_real_directory_grant_uniqueness_conflict_rereads_exact_semantic_identity(self):
        user = User.objects.create_user(username="grant-race-443", email="grant-race-443@example.invalid")
        membership = Membership.objects.create(user=user, tenant=self.customer)
        index_name = "itambox443rservicefix_grant_semantic_uq"
        quoted_index_name = connection.ops.quote_name(index_name)
        with connection.cursor() as cursor:
            cursor.execute(
                f'CREATE UNIQUE INDEX {quoted_index_name} ON "organization_rolegrant" '
                '("membership_id", "role_id", "reason") '
                'WHERE "membership_id" IS NOT NULL AND "granted_by_id" IS NULL'
            )
        insert_barrier = Barrier(2)

        def observe_grant_insert(sql):
            normalized = " ".join(sql.split()).lower()
            if normalized.startswith('insert into "organization_rolegrant"'):
                insert_barrier.wait(timeout=8)

        def ensure_grant():
            with transaction.atomic():
                live_membership = Membership._base_manager.get(pk=membership.pk)
                live_role = Role._base_manager.get(pk=self.member_role.pk)
                return identity_service._ensure_directory_grant(
                    membership=live_membership,
                    role=live_role,
                    existing_grants=[],
                    existing_scopes=[],
                )

        outputs = [{}, {}]
        threads = [
            threading.Thread(
                target=self._run_function,
                name="grant-race-a",
                args=(ensure_grant, outputs[0]),
                kwargs={"before_execute": observe_grant_insert},
            ),
            threading.Thread(
                target=self._run_function,
                name="grant-race-b",
                args=(ensure_grant, outputs[1]),
                kwargs={"before_execute": observe_grant_insert},
            ),
        ]
        try:
            for thread in threads:
                thread.start()
            self._join_bounded(threads)
        finally:
            connections.close_all()
            with connection.cursor() as cursor:
                cursor.execute(f"DROP INDEX IF EXISTS {quoted_index_name}")

        self.assertEqual([output.get("error") for output in outputs], [None, None])
        grants = list(
            RoleGrant._base_manager.filter(
                membership=membership,
                role=self.member_role,
                reason="LDAP directory synchronization",
                granted_by__isnull=True,
            )
        )
        self.assertEqual(len(grants), 1)

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
