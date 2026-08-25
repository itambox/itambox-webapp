from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.management.commands.sync_tenant_ldap import Command
from core.tasks.context import TaskContext
from itambox.middleware import get_current_user
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.services.identity_provisioning import (
    LDAP_DIRECTORY_SYNC_MEMBER_PERMISSION_LIST,
    LDAP_DIRECTORY_SYNC_REASON,
    LDAPDirectoryIdentityCommand,
    provision_ldap_directory_identity,
)

User = get_user_model()


class LDAPGrantReconciliationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="LDAP tenant", slug="ldap-tenant")
        self.user = User.objects.create_user(username="ldap-user")
        self.actor = User.objects.create_superuser(username="ldap-actor")
        self.membership = Membership.objects.create(
            user=self.user,
            tenant=self.tenant,
        )

    def command(self):
        return LDAPDirectoryIdentityCommand(user=self.user, tenant=self.tenant)

    @staticmethod
    def add_own_scope(grant):
        return RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )

    def make_member_role(self):
        return Role.objects.create(
            tenant=self.tenant,
            name="Member",
            permissions=list(LDAP_DIRECTORY_SYNC_MEMBER_PERMISSION_LIST),
        )

    def test_ldap_grant_is_idempotent_and_has_fixed_provenance(self):
        first_role = self.make_member_role()

        provision_ldap_directory_identity(self.command())
        first = RoleGrant.objects.get(membership=self.membership, role=first_role)
        first_pk = first.pk
        first_expiry = first.valid_until

        provision_ldap_directory_identity(self.command())

        second = RoleGrant.objects.get(pk=first_pk)
        self.assertEqual(second.reason, LDAP_DIRECTORY_SYNC_REASON)
        self.assertIsNone(second.granted_by)
        self.assertEqual(
            RoleGrant.objects.filter(
                membership=self.membership,
                role=first_role,
                reason=LDAP_DIRECTORY_SYNC_REASON,
            ).count(),
            1,
        )
        self.assertGreater(second.valid_until, first_expiry)
        self.assertEqual(
            second.scopes.filter(scope_type=RoleGrantScope.SCOPE_OWN).count(),
            1,
        )

    def test_privileged_ldap_grant_has_reason_and_future_expiry(self):
        self.make_member_role()
        before = timezone.now()

        provision_ldap_directory_identity(self.command())

        grant = RoleGrant.objects.get(membership=self.membership)
        self.assertEqual(grant.reason, LDAP_DIRECTORY_SYNC_REASON)
        self.assertIsNone(grant.granted_by)
        self.assertGreater(grant.valid_until, before)
        self.assertLessEqual(grant.valid_until, before + timedelta(days=1, seconds=1))
        grant.full_clean()

    def test_active_manual_equivalent_is_untouched_and_satisfies_access(self):
        role = self.make_member_role()
        manual = RoleGrant.objects.create(
            membership=self.membership,
            role=role,
            granted_by=self.actor,
            reason="Approved by operator",
            valid_until=timezone.now() + timedelta(days=2),
        )
        self.add_own_scope(manual)

        provision_ldap_directory_identity(self.command())

        manual.refresh_from_db()
        self.assertEqual(manual.reason, "Approved by operator")
        self.assertEqual(manual.granted_by, self.actor)
        self.assertIsNotNone(manual.valid_until)
        self.assertEqual(self.membership.role_grants.filter(role=role).count(), 1)

    def test_exact_reason_with_manual_actor_is_not_reclassified_as_ldap(self):
        role = self.make_member_role()
        manual = RoleGrant.objects.create(
            membership=self.membership,
            role=role,
            granted_by=self.actor,
            reason=LDAP_DIRECTORY_SYNC_REASON,
            valid_until=timezone.now() + timedelta(days=2),
        )
        self.add_own_scope(manual)

        provision_ldap_directory_identity(self.command())

        manual.refresh_from_db()
        self.assertEqual(manual.granted_by, self.actor)
        self.assertEqual(self.membership.role_grants.filter(role=role).count(), 1)

    def test_expired_manual_grant_is_preserved_and_new_ldap_grant_is_created(self):
        role = self.make_member_role()
        expired_at = timezone.now() - timedelta(hours=1)
        manual = RoleGrant.objects.create(
            membership=self.membership,
            role=role,
            granted_by=self.actor,
            reason="Expired manual approval",
            valid_until=timezone.now() + timedelta(minutes=1),
        )
        RoleGrant._base_manager.filter(pk=manual.pk).update(valid_until=expired_at)
        self.add_own_scope(manual)

        provision_ldap_directory_identity(self.command())

        manual.refresh_from_db()
        self.assertEqual(manual.reason, "Expired manual approval")
        self.assertEqual(manual.granted_by, self.actor)
        self.assertEqual(manual.valid_until, expired_at)
        self.assertEqual(
            RoleGrant.objects.filter(
                membership=self.membership,
                role=role,
                reason=LDAP_DIRECTORY_SYNC_REASON,
            ).count(),
            1,
        )

    def test_existing_ldap_grant_refreshes_only_when_origin_is_exact(self):
        role = self.make_member_role()
        grant = RoleGrant.objects.create(
            membership=self.membership,
            role=role,
            reason=LDAP_DIRECTORY_SYNC_REASON,
            granted_by=None,
            valid_until=timezone.now() + timedelta(hours=1),
        )
        self.add_own_scope(grant)

        provision_ldap_directory_identity(self.command())

        grant.refresh_from_db()
        self.assertGreater(grant.valid_until, timezone.now() + timedelta(hours=23))

    def test_ambiguous_expired_ldap_rows_are_not_overwritten(self):
        role = self.make_member_role()
        expired_at = timezone.now() - timedelta(hours=1)
        old_grants = []
        for _ in range(2):
            grant = RoleGrant.objects.create(
                membership=self.membership,
                role=role,
                reason=LDAP_DIRECTORY_SYNC_REASON,
                granted_by=None,
                valid_until=timezone.now() + timedelta(minutes=1),
            )
            RoleGrant._base_manager.filter(pk=grant.pk).update(valid_until=expired_at)
            self.add_own_scope(grant)
            old_grants.append(grant)

        provision_ldap_directory_identity(self.command())

        for grant in old_grants:
            grant.refresh_from_db()
            self.assertEqual(grant.valid_until, expired_at)
        self.assertEqual(
            RoleGrant.objects.filter(
                membership=self.membership,
                role=role,
                reason=LDAP_DIRECTORY_SYNC_REASON,
            ).count(),
            2,
        )

    def test_nested_command_context_preserves_outer_task_actor(self):
        observed_actor_ids = []

        def observe_actor(command, tenant):
            del command, tenant
            observed_actor_ids.append(get_current_user().pk)

        with TaskContext(tenant_id=self.tenant.pk, user_id=self.actor.pk):
            from unittest.mock import patch

            with patch.object(Command, "_run_sync", autospec=True, side_effect=observe_actor):
                call_command("sync_tenant_ldap", tenant=self.tenant.slug)
            self.assertEqual(get_current_user(), self.actor)

        self.assertEqual(observed_actor_ids, [self.actor.pk])
