"""Real PostgreSQL concurrency proofs for the #443 identity aggregate."""

from __future__ import annotations

import threading
from threading import Barrier, BrokenBarrierError, Event
from unittest import mock

import pytest
from django.db import connection, connections
from django.test import TransactionTestCase

from core.identity_provisioning import ExternalIdentityProfile, ExternalIdentityProvisioningCommand, ProviderStaffIntent
from organization.models import AssetHolder, Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.services.identity_provisioning import OrganizationIdentityProvisioner
from users.models import User

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

    def _command(self, user, *, provider_staff=None):
        return ExternalIdentityProvisioningCommand(
            user=user,
            customer_tenant=self.customer,
            profile=ExternalIdentityProfile(
                source="OIDC",
                email=user.email,
                upn=f"{user.username}@concurrency.invalid",
                first_name="Concurrent",
                last_name="Identity",
            ),
            customer_role_name="Member",
            provider_staff=provider_staff,
        )

    @staticmethod
    def _set_local_timeouts():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '2s'")
            cursor.execute("SET LOCAL statement_timeout = '10s'")

    @staticmethod
    def _run(command, output):
        try:
            connections.close_all()
            output["result"] = OrganizationIdentityProvisioner().provision(command)
        except Exception as exc:  # the main thread asserts the exact class
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
        release_first = Event()

        def checkpoint(stage):
            if stage != "locks.user":
                return
            self._set_local_timeouts()
            if threading.current_thread().name == first_name:
                first_locked.set()
                self.assertTrue(release_first.wait(timeout=8))

        outputs = [{}, {}]
        first = threading.Thread(target=self._run, name=first_name, args=(first_command, outputs[0]))
        second = threading.Thread(target=self._run, name=f"{first_mode}-second", args=(second_command, outputs[1]))
        with mock.patch("organization.services.identity_provisioning._stage_checkpoint", side_effect=checkpoint):
            first.start()
            self.assertTrue(first_locked.wait(timeout=8), f"{first_mode} command did not reach the User lock")
            second.start()
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
