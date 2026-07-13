from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.management.commands.sync_tenant_ldap import (
    LDAP_GRANT_REASON,
    Command,
    _ensure_ldap_role_grant,
)
from core.tasks.context import TaskContext
from itambox.middleware import get_current_user
from organization.models import (
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
)


User = get_user_model()


class LDAPGrantReconciliationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name='LDAP tenant', slug='ldap-tenant')
        self.user = User.objects.create_user(username='ldap-user')
        self.actor = User.objects.create_superuser(username='ldap-actor')
        self.membership = Membership.objects.create(
            user=self.user,
            tenant=self.tenant,
        )

    def make_role(self, name, permissions):
        return Role.objects.create(
            tenant=self.tenant,
            name=name,
            permissions=permissions,
        )

    @staticmethod
    def add_own_scope(grant):
        return RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )

    def test_non_privileged_ldap_grant_is_indefinite_and_idempotent(self):
        role = self.make_role('Directory reader', ['assets.view_asset'])

        first = _ensure_ldap_role_grant(self.membership, role)
        second = _ensure_ldap_role_grant(self.membership, role)

        self.assertEqual(first.pk, second.pk)
        first.refresh_from_db()
        self.assertEqual(first.reason, LDAP_GRANT_REASON)
        self.assertIsNone(first.granted_by)
        self.assertIsNone(first.valid_until)
        self.assertTrue(first.scopes.filter(
            scope_type=RoleGrantScope.SCOPE_OWN,
        ).exists())
        self.assertEqual(self.membership.role_grants.filter(role=role).count(), 1)

    def test_privileged_ldap_grant_has_reason_and_future_expiry(self):
        role = self.make_role('Directory editor', ['assets.change_asset'])
        before = timezone.now()

        grant = _ensure_ldap_role_grant(self.membership, role)

        self.assertEqual(grant.reason, LDAP_GRANT_REASON)
        self.assertIsNone(grant.granted_by)
        self.assertGreater(grant.valid_until, before)
        self.assertLessEqual(grant.valid_until, before + timedelta(days=1, seconds=1))
        grant.full_clean()

    def test_active_manual_equivalent_is_untouched_and_satisfies_access(self):
        role = self.make_role('Manual reader', ['assets.view_asset'])
        manual = RoleGrant.objects.create(
            membership=self.membership,
            role=role,
            granted_by=self.actor,
            reason='Approved by operator',
            valid_until=None,
        )
        self.add_own_scope(manual)

        result = _ensure_ldap_role_grant(self.membership, role)

        self.assertEqual(result.pk, manual.pk)
        manual.refresh_from_db()
        self.assertEqual(manual.reason, 'Approved by operator')
        self.assertEqual(manual.granted_by, self.actor)
        self.assertIsNone(manual.valid_until)
        self.assertEqual(self.membership.role_grants.filter(role=role).count(), 1)

    def test_exact_reason_with_manual_actor_is_not_reclassified_as_ldap(self):
        role = self.make_role('Manual marker reader', ['assets.view_asset'])
        manual = RoleGrant.objects.create(
            membership=self.membership,
            role=role,
            granted_by=self.actor,
            reason=LDAP_GRANT_REASON,
            valid_until=None,
        )
        self.add_own_scope(manual)

        _ensure_ldap_role_grant(self.membership, role)

        manual.refresh_from_db()
        self.assertEqual(manual.granted_by, self.actor)
        self.assertEqual(self.membership.role_grants.filter(role=role).count(), 1)

    def test_expired_manual_grant_is_preserved_and_new_ldap_grant_is_created(self):
        role = self.make_role('Expired manual reader', ['assets.view_asset'])
        expired_at = timezone.now() - timedelta(hours=1)
        manual = RoleGrant.objects.create(
            membership=self.membership,
            role=role,
            granted_by=self.actor,
            reason='Expired manual approval',
            valid_until=expired_at,
        )
        self.add_own_scope(manual)

        ldap_grant = _ensure_ldap_role_grant(self.membership, role)

        self.assertNotEqual(ldap_grant.pk, manual.pk)
        manual.refresh_from_db()
        self.assertEqual(manual.reason, 'Expired manual approval')
        self.assertEqual(manual.granted_by, self.actor)
        self.assertEqual(manual.valid_until, expired_at)
        self.assertEqual(ldap_grant.reason, LDAP_GRANT_REASON)
        self.assertIsNone(ldap_grant.valid_until)

    def test_existing_ldap_grant_is_normalized_only_when_origin_is_exact(self):
        role = self.make_role('Legacy LDAP reader', ['assets.view_asset'])
        grant = RoleGrant.objects.create(
            membership=self.membership,
            role=role,
            reason=LDAP_GRANT_REASON,
            granted_by=None,
            valid_until=timezone.now() + timedelta(hours=1),
        )
        self.add_own_scope(grant)

        result = _ensure_ldap_role_grant(self.membership, role)

        self.assertEqual(result.pk, grant.pk)
        grant.refresh_from_db()
        self.assertIsNone(grant.valid_until)

    def test_ambiguous_expired_ldap_rows_are_not_overwritten(self):
        role = self.make_role('Ambiguous LDAP reader', ['assets.view_asset'])
        expired_at = timezone.now() - timedelta(hours=1)
        old_grants = []
        for _ in range(2):
            grant = RoleGrant.objects.create(
                membership=self.membership,
                role=role,
                reason=LDAP_GRANT_REASON,
                granted_by=None,
                valid_until=expired_at,
            )
            self.add_own_scope(grant)
            old_grants.append(grant)

        new_grant = _ensure_ldap_role_grant(self.membership, role)

        self.assertNotIn(new_grant.pk, [grant.pk for grant in old_grants])
        for grant in old_grants:
            grant.refresh_from_db()
            self.assertEqual(grant.valid_until, expired_at)
        self.assertIsNone(new_grant.valid_until)

    def test_nested_command_context_preserves_outer_task_actor(self):
        observed_actor_ids = []

        def observe_actor(command, tenant):
            observed_actor_ids.append(get_current_user().pk)

        with TaskContext(tenant_id=self.tenant.pk, user_id=self.actor.pk):
            with patch.object(Command, '_run_sync', autospec=True, side_effect=observe_actor):
                call_command('sync_tenant_ldap', tenant=self.tenant.slug)
            self.assertEqual(get_current_user(), self.actor)

        self.assertEqual(observed_actor_ids, [self.actor.pk])
