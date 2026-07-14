"""Managed scope changes are audited as first-class RoleGrantScope rows."""

import uuid

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from core.managers import set_current_membership, set_current_tenant
from core.models import ObjectChange
from itambox.middleware import _current_user, _request_id
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant


User = get_user_model()


class ManagedScopeAuditTests(TestCase):
    def setUp(self):
        self.provider = Tenant.objects.create(
            name='Scope Audit Provider', slug='scope-audit-provider', is_provider=True,
        )
        self.customer_a = Tenant.objects.create(
            name='Scope Audit A', slug='scope-audit-a', managed_by=self.provider,
        )
        self.customer_b = Tenant.objects.create(
            name='Scope Audit B', slug='scope-audit-b', managed_by=self.provider,
        )
        self.actor = User.objects.create_user(username='scope-audit-actor')
        self.user = User.objects.create_user(username='scope-audit-tech')
        self.membership = Membership.objects.create(user=self.user, tenant=self.provider)
        self.role = Role.objects.create(
            tenant=self.provider,
            name='Scope Audit reader',
            permissions=['assets.view_asset'],
        )
        self.grant = RoleGrant.objects.create(
            membership=self.membership,
            role=self.role,
        )
        self.request_id = uuid.uuid4()
        set_current_tenant(self.provider)
        set_current_membership(None)
        _current_user.set(self.actor)
        _request_id.set(self.request_id)

    def tearDown(self):
        _current_user.set(None)
        _request_id.set(None)
        set_current_tenant(None)
        set_current_membership(None)

    @staticmethod
    def scope_content_type():
        return ContentType.objects.get_for_model(RoleGrantScope)

    def changes_for(self, scope):
        return ObjectChange._base_manager.filter(
            changed_object_type=self.scope_content_type(),
            changed_object_id=scope.pk,
        ).order_by('time', 'pk')

    def create_scope(self, tenant):
        return RoleGrantScope.objects.create(
            role_grant=self.grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=tenant,
        )

    def test_scope_creation_records_target_and_actor(self):
        scope = self.create_scope(self.customer_a)

        change = self.changes_for(scope).get(action='create')
        self.assertEqual(change.postchange_data['tenant'], self.customer_a.pk)
        self.assertEqual(change.postchange_data['role_grant'], self.grant.pk)
        self.assertEqual(change.user_id, self.actor.pk)
        self.assertEqual(change.request_id, self.request_id)
        # A specific-tenant scope is audited in the target tenant's stream.
        self.assertEqual(change.tenant_id, self.customer_a.pk)

    def test_scope_deletion_leaves_an_audited_tombstone(self):
        scope = self.create_scope(self.customer_a)
        scope_pk = scope.pk

        scope.delete()

        deleted = ObjectChange._base_manager.get(
            changed_object_type=self.scope_content_type(),
            changed_object_id=scope_pk,
            action='delete',
        )
        self.assertEqual(deleted.prechange_data['tenant'], self.customer_a.pk)
        self.assertEqual(deleted.user_id, self.actor.pk)

    def test_replacing_target_is_explicit_delete_plus_create(self):
        old_scope = self.create_scope(self.customer_a)
        old_pk = old_scope.pk

        old_scope.delete()
        new_scope = self.create_scope(self.customer_b)

        self.assertTrue(ObjectChange._base_manager.filter(
            changed_object_type=self.scope_content_type(),
            changed_object_id=old_pk,
            action='delete',
        ).exists())
        created = self.changes_for(new_scope).get(action='create')
        self.assertEqual(created.postchange_data['tenant'], self.customer_b.pk)

    def test_multiple_tenant_scopes_each_have_an_independent_audit_row(self):
        first = self.create_scope(self.customer_a)
        second = self.create_scope(self.customer_b)

        self.assertEqual(self.changes_for(first).filter(action='create').count(), 1)
        self.assertEqual(self.changes_for(second).filter(action='create').count(), 1)
