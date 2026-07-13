from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.managers import (
    set_current_membership,
    set_current_tenant,
    set_current_tenant_group,
)
from core.tests.mixins import grant
from organization.models import (
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
    TenantGroup,
)
from organization.services import visible_to_containers
from users.models import UserGroup


User = get_user_model()


class RoleBulkDeleteObjectAuthorizationTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        self.tenant_group = TenantGroup.objects.create(
            name='Bulk role region', slug='bulk-role-region',
        )
        self.tenant_a = Tenant.objects.create(
            name='Bulk role A', slug='bulk-role-a', group=self.tenant_group,
        )
        self.tenant_b = Tenant.objects.create(
            name='Bulk role B', slug='bulk-role-b', group=self.tenant_group,
        )
        self.actor = User.objects.create_user(
            username='bulk-role-actor', password='pw',
        )
        delete_role = Role.objects.create(
            tenant=self.tenant_a,
            name='Role deleter',
            permissions=['organization.view_role', 'organization.delete_role'],
        )
        view_only_role = Role.objects.create(
            tenant=self.tenant_b,
            name='Role viewer',
            permissions=['organization.view_role'],
        )
        grant(self.actor, self.tenant_a, delete_role)
        grant(self.actor, self.tenant_b, view_only_role)
        self.deletable = Role.objects.create(
            tenant=self.tenant_a, name='Delete me', permissions=[],
        )
        self.protected = Role.objects.create(
            tenant=self.tenant_b, name='Do not delete me', permissions=[],
        )

        self.client.force_login(self.actor)
        session = self.client.session
        session['active_tenant_group_id'] = self.tenant_group.pk
        session.pop('active_tenant_id', None)
        session.save()

    def tearDown(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)

    def test_group_scope_bulk_delete_checks_each_roles_owner_tenant(self):
        response = self.client.post(reverse('organization:role_bulk_delete'), {
            'pk': [self.deletable.pk, self.protected.pk],
            '_confirm': '1',
            'return_url': reverse('organization:role_list'),
        })

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(
            Role.all_objects.get(pk=self.deletable.pk).deleted_at,
        )
        self.assertIsNone(
            Role.all_objects.get(pk=self.protected.pk).deleted_at,
        )


class DirectRoleGrantWriterTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.provider = Tenant.objects.create(
            name='Provider', slug='writer-provider', is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name='Customer', slug='writer-customer', managed_by=self.provider,
        )
        self.actor = User.objects.create_superuser(
            username='grant-writer', email='writer@example.com', password='pw',
        )
        self.target = User.objects.create_user(
            username='grant-target', email='target@example.com', password='pw',
        )
        self.membership = Membership.objects.create(
            user=self.target, tenant=self.provider,
        )
        self.viewer = Role.objects.create(
            tenant=self.provider,
            name='Direct viewer',
            permissions=['assets.view_asset'],
        )
        self.editor = Role.objects.create(
            tenant=self.provider,
            name='Direct editor',
            permissions=['assets.change_asset'],
        )
        self.client.force_login(self.actor)
        session = self.client.session
        session['active_tenant_id'] = self.provider.pk
        session.save()

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _expiry_value(self):
        return (timezone.now() + timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M')

    def test_role_assign_users_creates_canonical_own_scope(self):
        response = self.client.post(
            reverse('organization:role_assign_users', kwargs={'pk': self.viewer.pk}),
            {'users': [self.target.pk]},
        )

        self.assertEqual(response.status_code, 302)
        grant = RoleGrant.objects.get(membership=self.membership, role=self.viewer)
        self.assertTrue(
            grant.scopes.filter(scope_type=RoleGrantScope.SCOPE_OWN).exists()
        )

    def test_role_assign_users_requires_terms_for_elevated_direct_grant(self):
        url = reverse('organization:role_assign_users', kwargs={'pk': self.editor.pk})
        denied = self.client.post(url, {'users': [self.target.pk]})
        self.assertEqual(denied.status_code, 200)
        self.assertFalse(
            RoleGrant.objects.filter(membership=self.membership, role=self.editor).exists()
        )

        allowed = self.client.post(url, {
            'users': [self.target.pk],
            'reason': 'Temporary incident response',
            'valid_until': self._expiry_value(),
        })
        self.assertEqual(allowed.status_code, 302)
        grant = RoleGrant.objects.get(membership=self.membership, role=self.editor)
        self.assertEqual(grant.reason, 'Temporary incident response')
        self.assertGreater(grant.valid_until, timezone.now())

    def test_bulk_remove_own_scope_preserves_managed_scope(self):
        grant = RoleGrant.objects.create(
            membership=self.membership,
            role=self.editor,
            granted_by=self.actor,
            reason='Temporary incident response',
            valid_until=timezone.now() + timedelta(hours=2),
        )
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer,
        )

        response = self.client.post(reverse('organization:membership_bulk_edit'), {
            'pk': [self.membership.pk],
            '_apply': '1',
            'roles_to_remove': [self.editor.pk],
            'return_url': reverse('organization:membership_list'),
        })

        self.assertEqual(response.status_code, 302)
        grant.refresh_from_db()
        self.assertFalse(
            grant.scopes.filter(scope_type=RoleGrantScope.SCOPE_OWN).exists()
        )
        self.assertTrue(
            grant.scopes.filter(
                scope_type=RoleGrantScope.SCOPE_TENANT,
                tenant=self.customer,
            ).exists()
        )


class RoleGrantContainerVisibilityTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name='Visible', slug='visible-grants')
        self.other = Tenant.objects.create(name='Hidden', slug='hidden-grants')
        self.actor = User.objects.create_user(username='grant-auditor')
        actor_membership = Membership.objects.create(user=self.actor, tenant=self.tenant)
        audit_role = Role.objects.create(
            tenant=self.tenant,
            name='Grant auditor',
            permissions=['organization.view_rolegrant'],
        )
        audit_grant = RoleGrant.objects.create(
            membership=actor_membership,
            role=audit_role,
        )
        RoleGrantScope.objects.create(
            role_grant=audit_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )

        direct_user = User.objects.create_user(username='direct-principal')
        direct_membership = Membership.objects.create(
            user=direct_user, tenant=self.tenant,
        )
        role = Role.objects.create(
            tenant=self.tenant,
            name='Visible role',
            permissions=['assets.view_asset'],
        )
        self.direct_grant = RoleGrant.objects.create(
            membership=direct_membership,
            role=role,
        )
        RoleGrantScope.objects.create(
            role_grant=self.direct_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )

        group = UserGroup.objects.create(tenant=self.tenant, name='Visible group')
        self.group_grant = RoleGrant.objects.create(user_group=group, role=role)
        RoleGrantScope.objects.create(
            role_grant=self.group_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )

        hidden_role = Role.objects.create(
            tenant=self.other,
            name='Hidden role',
            permissions=['assets.view_asset'],
        )
        hidden_group = UserGroup.objects.create(tenant=self.other, name='Hidden group')
        self.hidden_grant = RoleGrant.objects.create(
            user_group=hidden_group,
            role=hidden_role,
        )
        RoleGrantScope.objects.create(
            role_grant=self.hidden_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_visibility_supports_both_canonical_principal_shapes(self):
        visible = visible_to_containers(
            self.actor,
            RoleGrant.objects.all(),
            'organization.view_rolegrant',
        )

        self.assertIn(self.direct_grant, visible)
        self.assertIn(self.group_grant, visible)
        self.assertNotIn(self.hidden_grant, visible)
