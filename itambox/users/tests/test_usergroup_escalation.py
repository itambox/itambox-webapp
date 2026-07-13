"""Escalation guards for canonical group membership and managed scopes."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.tests.mixins import grant
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant
from users.forms import GroupManagedRoleGrantForm, UserGroupForm
from users.models import GroupMembership, UserGroup

User = get_user_model()


def tenant(name, *, provider=False, managed_by=None):
    return Tenant.objects.create(
        name=name,
        slug=name.lower().replace(' ', '-'),
        is_provider=provider,
        managed_by=managed_by,
    )


def user(name, *, superuser=False):
    creator = User.objects.create_superuser if superuser else User.objects.create_user
    return creator(username=name, email=f'{name}@example.com', password='pw')


def group_grant(group, role, scope_type=RoleGrantScope.SCOPE_OWN, target=None):
    role_grant = RoleGrant.objects.create(user_group=group, role=role)
    kwargs = {'role_grant': role_grant, 'scope_type': scope_type}
    if scope_type == RoleGrantScope.SCOPE_TENANT:
        kwargs['tenant'] = target
    RoleGrantScope.objects.create(**kwargs)
    return role_grant


def managed_data(role, target, *, grant_id='', initial_forms=0):
    return {
        'managed-TOTAL_FORMS': '1',
        'managed-INITIAL_FORMS': str(initial_forms),
        'managed-MIN_NUM_FORMS': '0',
        'managed-MAX_NUM_FORMS': '1000',
        'managed-0-id': grant_id,
        'managed-0-role': role.pk,
        'managed-0-managed_scope': GroupManagedRoleGrantForm.SCOPE_EXPLICIT,
        'managed-0-scope_group': '',
        'managed-0-assigned_tenants': [target.pk],
        'managed-0-DELETE': '',
    }


class GroupShapeEscalationTests(TestCase):
    def setUp(self):
        self.superuser = user('root', superuser=True)
        self.tenant_a = tenant('Tenant A')
        self.tenant_b = tenant('Tenant B')
        self.role_a = Role.objects.create(tenant=self.tenant_a, name='A Reader')
        self.role_b = Role.objects.create(tenant=self.tenant_b, name='B Reader')
        self.membership_a = Membership.objects.create(
            user=user('member-a'), tenant=self.tenant_a,
        )
        self.membership_b = Membership.objects.create(
            user=user('member-b'), tenant=self.tenant_b,
        )

    def test_form_rejects_foreign_role_and_membership(self):
        form = UserGroupForm(
            data={
                'name': 'Invalid',
                'tenant': self.tenant_a.pk,
                'roles': [self.role_b.pk],
                'members': [self.membership_b.pk],
                'is_active': True,
            },
            user=self.superuser,
            tenant=self.tenant_a,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('roles', form.errors)
        self.assertIn('members', form.errors)

    def test_edit_rejects_tampered_owner(self):
        group = UserGroup.objects.create(name='A Team', tenant=self.tenant_a)
        form = UserGroupForm(
            data={
                'name': group.name,
                'tenant': self.tenant_b.pk,
                'roles': [],
                'members': [],
                'is_active': True,
            },
            instance=group,
            user=self.superuser,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('tenant', form.errors)


class ManagedScopeEscalationTests(TestCase):
    def setUp(self):
        self.provider = tenant('Provider', provider=True)
        self.customer_a = tenant('Customer A', managed_by=self.provider)
        self.customer_b = tenant('Customer B', managed_by=self.provider)
        self.actor = user('provider-admin')
        actor_role = Role.objects.create(
            tenant=self.provider,
            name='Group Grant Manager',
            permissions=[
                'users.add_usergroup',
                'users.change_usergroup',
                'organization.add_rolegrant',
                'organization.change_rolegrant',
                'assets.view_asset',
            ],
        )
        actor_grant = grant(self.actor, self.provider, actor_role)
        RoleGrantScope.objects.create(
            role_grant=actor_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )
        self.projected_role = Role.objects.create(
            tenant=self.provider,
            name='Reader',
            permissions=['assets.view_asset'],
        )

    def form_for_target(self, target):
        data = {
            'name': 'Provider Readers',
            'tenant': self.provider.pk,
            'roles': [],
            'members': [],
            'is_active': True,
        }
        data.update(managed_data(self.projected_role, target))
        return UserGroupForm(
            data=data,
            user=self.actor,
            tenant=self.provider,
        )

    def test_actor_can_author_scope_within_own_managed_reach(self):
        form = self.form_for_target(self.customer_a)
        self.assertTrue(form.is_valid(), form.errors)
        group = form.save()
        self.assertTrue(
            group.role_grants.filter(
                role=self.projected_role,
                scopes__tenant=self.customer_a,
            ).exists()
        )

    def test_actor_cannot_author_scope_outside_own_managed_reach(self):
        form = self.form_for_target(self.customer_b)
        self.assertFalse(form.is_valid())
        errors = ' '.join(
            message
            for row_errors in form.managed_formset.errors
            for messages in row_errors.values()
            for message in messages
        ).lower()
        self.assertIn('outside your own reach', errors)


class UserGroupReactivationEscalationTests(TestCase):
    def setUp(self):
        self.tenant = tenant('Reactivation Tenant')
        self.actor = user('reactivation-admin')
        actor_role = Role.objects.create(
            tenant=self.tenant,
            name='Group editor',
            permissions=['users.change_usergroup'],
        )
        grant(self.actor, self.tenant, actor_role)
        self.member = Membership.objects.create(
            user=user('reactivated-member'),
            tenant=self.tenant,
        )
        self.privileged_role = Role.objects.create(
            tenant=self.tenant,
            name='Asset deleter',
            permissions=['assets.delete_asset'],
        )
        self.group = UserGroup.objects.create(
            name='Inactive asset admins',
            tenant=self.tenant,
            is_active=False,
        )
        GroupMembership.objects.create(
            user_group=self.group,
            membership=self.member,
        )
        group_grant(self.group, self.privileged_role)

    def reactivation_data(self):
        return {
            'name': self.group.name,
            'tenant': self.tenant.pk,
            'roles': [self.privileged_role.pk],
            'members': [self.member.pk],
            'is_active': True,
        }

    def test_form_revalidates_existing_own_scope_roles_on_reactivation(self):
        form = UserGroupForm(
            data=self.reactivation_data(),
            instance=self.group,
            user=self.actor,
            tenant=self.tenant,
        )

        self.assertFalse(form.is_valid())
        self.assertIn(
            'assets.delete_asset',
            ' '.join(form.non_field_errors()),
        )

    def test_edit_view_cannot_reactivate_roles_actor_cannot_grant(self):
        self.client.force_login(self.actor)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.pop('active_tenant_group_id', None)
        session.save()

        response = self.client.post(
            reverse('users:usergroup_update', args=[self.group.pk]),
            self.reactivation_data(),
        )

        self.assertEqual(response.status_code, 200)
        self.group.refresh_from_db()
        self.assertFalse(self.group.is_active)
        self.assertIn(
            'assets.delete_asset',
            ' '.join(response.context['form'].non_field_errors()),
        )


class AssignMembershipEscalationTests(TestCase):
    def setUp(self):
        self.tenant = tenant('Tenant')
        self.actor = user('group-admin')
        actor_role = Role.objects.create(
            tenant=self.tenant,
            name='Group Admin',
            permissions=['users.change_usergroup', 'assets.view_asset'],
        )
        grant(self.actor, self.tenant, actor_role)
        self.target_membership = Membership.objects.create(
            user=user('target'),
            tenant=self.tenant,
        )
        self.client.force_login(self.actor)

    def test_assign_blocks_role_permissions_actor_does_not_hold(self):
        group = UserGroup.objects.create(name='Admins', tenant=self.tenant)
        privileged_role = Role.objects.create(
            tenant=self.tenant,
            name='Asset Deleter',
            permissions=['assets.delete_asset'],
        )
        group_grant(group, privileged_role)

        response = self.client.post(
            reverse('users:usergroup_assign_users', args=[group.pk]),
            {'memberships': [self.target_membership.pk]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            GroupMembership.objects.filter(
                user_group=group,
                membership=self.target_membership,
            ).exists()
        )

    def test_assign_allows_held_role_and_writes_group_membership(self):
        group = UserGroup.objects.create(name='Readers', tenant=self.tenant)
        reader_role = Role.objects.create(
            tenant=self.tenant,
            name='Reader',
            permissions=['assets.view_asset'],
        )
        group_grant(group, reader_role)

        response = self.client.post(
            reverse('users:usergroup_assign_users', args=[group.pk]),
            {'memberships': [self.target_membership.pk]},
        )
        self.assertEqual(response.status_code, 302)
        row = GroupMembership.objects.get(
            user_group=group,
            membership=self.target_membership,
        )
        self.assertEqual(row.source, GroupMembership.SOURCE_MANUAL)
        self.assertEqual(row.added_by, self.actor)
