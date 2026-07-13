"""Canonical UserGroup, GroupMembership, and group RoleGrant coverage."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from core.managers import set_current_membership, set_current_tenant
from core.tests.mixins import grant
from organization.models import (
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
    TenantGroup,
)
from organization.rbac import accessible_tenant_ids, effective_permissions
from users.filters import UserGroupFilterSet
from users.forms import GroupManagedRoleGrantForm, UserGroupForm
from users.models import GroupMembership, UserGroup

User = get_user_model()


def make_tenant(name, *, provider=False, managed_by=None, group=None):
    return Tenant.objects.create(
        name=name,
        slug=name.lower().replace(' ', '-'),
        is_provider=provider,
        managed_by=managed_by,
        group=group,
    )


def make_user(name):
    return User.objects.create_user(
        username=name,
        email=f'{name}@example.com',
        password='pw',
    )


def make_group_grant(group, role, *scope_specs, granted_by=None):
    role_grant = RoleGrant.objects.create(
        user_group=group,
        role=role,
        granted_by=granted_by,
    )
    for scope_type, target in scope_specs:
        kwargs = {'role_grant': role_grant, 'scope_type': scope_type}
        if scope_type == RoleGrantScope.SCOPE_TENANT:
            kwargs['tenant'] = target
        elif scope_type == RoleGrantScope.SCOPE_TENANT_GROUP:
            kwargs['tenant_group'] = target
        RoleGrantScope.objects.create(**kwargs)
    return role_grant


def managed_form_data(rows, *, initial_forms=0):
    data = {
        'managed-TOTAL_FORMS': str(len(rows)),
        'managed-INITIAL_FORMS': str(initial_forms),
        'managed-MIN_NUM_FORMS': '0',
        'managed-MAX_NUM_FORMS': '1000',
    }
    for index, row in enumerate(rows):
        prefix = f'managed-{index}'
        data[f'{prefix}-id'] = row.get('id', '')
        data[f'{prefix}-role'] = row['role'].pk
        data[f'{prefix}-managed_scope'] = row['scope']
        data[f'{prefix}-scope_group'] = (
            row.get('scope_group').pk if row.get('scope_group') else ''
        )
        data[f'{prefix}-assigned_tenants'] = [
            tenant.pk for tenant in row.get('tenants', [])
        ]
        data[f'{prefix}-DELETE'] = row.get('delete', '')
    return data


class CanonicalGroupModelTests(TestCase):
    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_group_requires_owner_and_owner_is_immutable(self):
        ownerless = UserGroup(name='Ownerless')
        with self.assertRaises(ValidationError):
            ownerless.full_clean()

        tenant_a = make_tenant('Tenant A')
        tenant_b = make_tenant('Tenant B')
        group = UserGroup.objects.create(name='Ops', tenant=tenant_a)
        group.tenant = tenant_b
        with self.assertRaises(ValidationError):
            group.save()

    def test_names_are_unique_per_live_tenant(self):
        tenant_a = make_tenant('Tenant A')
        tenant_b = make_tenant('Tenant B')
        UserGroup.objects.create(name='Ops', tenant=tenant_a)
        UserGroup.objects.create(name='Ops', tenant=tenant_b)
        duplicate = UserGroup(name='Ops', tenant=tenant_a)
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_group_membership_requires_same_owner_membership(self):
        tenant_a = make_tenant('Tenant A')
        tenant_b = make_tenant('Tenant B')
        user = make_user('member')
        membership_b = Membership.objects.create(user=user, tenant=tenant_b)
        group = UserGroup.objects.create(name='A Team', tenant=tenant_a)
        with self.assertRaises(ValidationError):
            GroupMembership.objects.create(
                user_group=group,
                membership=membership_b,
            )

    def test_group_role_must_be_owned_by_group_tenant(self):
        tenant_a = make_tenant('Tenant A')
        tenant_b = make_tenant('Tenant B')
        group = UserGroup.objects.create(name='A Team', tenant=tenant_a)
        foreign_role = Role.objects.create(tenant=tenant_b, name='Foreign')
        with self.assertRaises(ValidationError):
            RoleGrant.objects.create(user_group=group, role=foreign_role)

    def test_privileged_group_grant_may_be_permanent(self):
        provider = make_tenant('Provider', provider=True)
        group = UserGroup.objects.create(name='Administrators', tenant=provider)
        role = Role.objects.create(
            tenant=provider,
            name='Administrator',
            permissions=['assets.delete_asset'],
        )
        role_grant = RoleGrant.objects.create(user_group=group, role=role)
        RoleGrantScope.objects.create(
            role_grant=role_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        self.assertEqual(role_grant.reason, '')
        self.assertIsNone(role_grant.valid_until)


class GroupResolverTests(TestCase):
    def setUp(self):
        self.root_group = TenantGroup.objects.create(name='Customers', slug='customers')
        self.child_group = TenantGroup.objects.create(
            name='North', slug='north', parent=self.root_group,
        )
        self.provider = make_tenant('Provider', provider=True)
        self.customer_a = make_tenant(
            'Customer A', managed_by=self.provider, group=self.child_group,
        )
        self.customer_b = make_tenant('Customer B', managed_by=self.provider)
        self.user = make_user('technician')
        membership = Membership.objects.create(user=self.user, tenant=self.provider)
        self.group = UserGroup.objects.create(name='Provider Team', tenant=self.provider)
        GroupMembership.objects.create(user_group=self.group, membership=membership)
        self.role = Role.objects.create(
            tenant=self.provider,
            name='Technician',
            permissions=['assets.view_asset'],
        )

    def test_specific_tenant_scope_projects_provider_group_role(self):
        make_group_grant(
            self.group,
            self.role,
            (RoleGrantScope.SCOPE_TENANT, self.customer_a),
        )
        self.assertIn('assets.view_asset', effective_permissions(self.user, self.customer_a))
        self.assertNotIn('assets.view_asset', effective_permissions(self.user, self.customer_b))
        self.assertEqual(
            accessible_tenant_ids(self.user),
            {self.provider.pk, self.customer_a.pk},
        )

    def test_tenant_group_scope_includes_descendants_only(self):
        make_group_grant(
            self.group,
            self.role,
            (RoleGrantScope.SCOPE_TENANT_GROUP, self.root_group),
        )
        self.assertIn('assets.view_asset', effective_permissions(self.user, self.customer_a))
        self.assertNotIn('assets.view_asset', effective_permissions(self.user, self.customer_b))

    def test_inactive_membership_and_group_are_both_inert(self):
        make_group_grant(
            self.group,
            self.role,
            (RoleGrantScope.SCOPE_TENANT, self.customer_a),
        )
        membership = self.group.group_memberships.get().membership
        membership.is_active = False
        membership.save()
        self.assertNotIn('assets.view_asset', effective_permissions(self.user, self.customer_a))
        membership.is_active = True
        membership.save()
        self.group.is_active = False
        self.group.save()
        self.assertNotIn('assets.view_asset', effective_permissions(self.user, self.customer_a))


class UserGroupFormTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username='root', email='root@example.com', password='pw',
        )
        self.provider = make_tenant('Provider', provider=True)
        self.customer = make_tenant('Customer', managed_by=self.provider)
        self.tenant_group = TenantGroup.objects.create(name='Managed', slug='managed')
        self.role = Role.objects.create(
            tenant=self.provider,
            name='Technician',
            permissions=['assets.view_asset'],
        )
        self.member = Membership.objects.create(
            user=make_user('member'),
            tenant=self.provider,
        )

    def base_data(self, **overrides):
        data = {
            'name': 'Provider Team',
            'description': '',
            'tenant': self.provider.pk,
            'roles': [self.role.pk],
            'members': [self.member.pk],
            'is_active': True,
        }
        data.update(overrides)
        return data

    def test_form_creates_one_grant_with_own_and_managed_scopes(self):
        data = self.base_data()
        data.update(managed_form_data([{
            'role': self.role,
            'scope': GroupManagedRoleGrantForm.SCOPE_EXPLICIT,
            'tenants': [self.customer],
        }]))
        form = UserGroupForm(data=data, user=self.superuser, tenant=self.provider)
        self.assertTrue(form.is_valid(), form.errors)
        group = form.save()

        role_grant = group.role_grants.get(role=self.role)
        self.assertEqual(
            set(role_grant.scopes.values_list('scope_type', 'tenant_id')),
            {
                (RoleGrantScope.SCOPE_OWN, None),
                (RoleGrantScope.SCOPE_TENANT, self.customer.pk),
            },
        )
        self.assertTrue(
            group.group_memberships.filter(membership=self.member).exists()
        )
        self.assertEqual(role_grant.reason, '')
        self.assertIsNone(role_grant.valid_until)

    def test_edit_preserves_grant_provenance_and_authors_additive_scopes(self):
        group = UserGroup.objects.create(name='Provider Team', tenant=self.provider)
        role_grant = make_group_grant(
            group,
            self.role,
            (RoleGrantScope.SCOPE_OWN, None),
            (RoleGrantScope.SCOPE_TENANT, self.customer),
            granted_by=self.superuser,
        )
        original_granted_at = role_grant.granted_at
        data = self.base_data(members=[])
        data.update(managed_form_data([
            {
                'id': role_grant.pk,
                'role': self.role,
                'scope': GroupManagedRoleGrantForm.SCOPE_EXPLICIT,
                'tenants': [self.customer],
            },
            {
                'id': role_grant.pk,
                'role': self.role,
                'scope': RoleGrantScope.SCOPE_TENANT_GROUP,
                'scope_group': self.tenant_group,
            },
        ], initial_forms=1))
        form = UserGroupForm(
            data=data,
            instance=group,
            user=self.superuser,
            tenant=self.provider,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        role_grant.refresh_from_db()
        self.assertEqual(role_grant.granted_by, self.superuser)
        self.assertEqual(role_grant.granted_at, original_granted_at)
        self.assertEqual(
            set(role_grant.scopes.values_list(
                'scope_type', 'tenant_id', 'tenant_group_id',
            )),
            {
                (RoleGrantScope.SCOPE_OWN, None, None),
                (RoleGrantScope.SCOPE_TENANT, self.customer.pk, None),
                (RoleGrantScope.SCOPE_TENANT_GROUP, None, self.tenant_group.pk),
            },
        )

    def test_tampered_grant_id_cannot_touch_another_group(self):
        other_group = UserGroup.objects.create(name='Other', tenant=self.provider)
        other_grant = make_group_grant(
            other_group,
            self.role,
            (RoleGrantScope.SCOPE_OWN, None),
        )
        target_group = UserGroup.objects.create(name='Target', tenant=self.provider)
        data = self.base_data(name='Target', roles=[], members=[])
        data.update(managed_form_data([{
            'id': other_grant.pk,
            'role': self.role,
            'scope': GroupManagedRoleGrantForm.SCOPE_EXPLICIT,
            'tenants': [self.customer],
        }]))
        form = UserGroupForm(
            data=data,
            instance=target_group,
            user=self.superuser,
            tenant=self.provider,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.assertEqual(
            list(other_grant.scopes.values_list('scope_type', flat=True)),
            [RoleGrantScope.SCOPE_OWN],
        )
        self.assertTrue(
            target_group.role_grants.filter(
                role=self.role,
                scopes__tenant=self.customer,
            ).exists()
        )

    def test_form_only_removes_manual_group_memberships(self):
        tenant = make_tenant('Local')
        group = UserGroup.objects.create(name='Local Team', tenant=tenant)
        manual = Membership.objects.create(user=make_user('manual'), tenant=tenant)
        external = Membership.objects.create(user=make_user('external'), tenant=tenant)
        GroupMembership.objects.create(
            user_group=group,
            membership=manual,
            source=GroupMembership.SOURCE_MANUAL,
        )
        GroupMembership.objects.create(
            user_group=group,
            membership=external,
            source=GroupMembership.SOURCE_SCIM,
            external_id='scim-member-1',
        )
        form = UserGroupForm(
            data={
                'name': group.name,
                'description': '',
                'tenant': tenant.pk,
                'roles': [],
                'members': [],
                'is_active': True,
            },
            instance=group,
            user=self.superuser,
            tenant=tenant,
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.assertFalse(group.group_memberships.filter(membership=manual).exists())
        self.assertTrue(group.group_memberships.filter(membership=external).exists())


class UserGroupObjectScopeTests(TestCase):
    def setUp(self):
        self.tenant_a = make_tenant('Tenant A')
        self.tenant_b = make_tenant('Tenant B')
        self.group_a = UserGroup.objects.create(name='A Group', tenant=self.tenant_a)
        self.group_b = UserGroup.objects.create(name='B Group', tenant=self.tenant_b)
        self.actor = make_user('group-admin')
        admin_role = Role.objects.create(
            tenant=self.tenant_a,
            name='Group Admin',
            permissions=[
                'users.add_usergroup',
                'users.view_usergroup',
                'users.change_usergroup',
                'users.delete_usergroup',
            ],
        )
        grant(self.actor, self.tenant_a, admin_role)
        self.client.force_login(self.actor)

    def test_detail_edit_assign_and_delete_cannot_cross_owner_by_pk(self):
        self.assertEqual(
            self.client.get(reverse('users:usergroup_detail', args=[self.group_a.pk])).status_code,
            200,
        )
        for route in (
            'users:usergroup_detail',
            'users:usergroup_update',
            'users:usergroup_assign_users',
            'users:usergroup_delete',
        ):
            response = self.client.get(reverse(route, args=[self.group_b.pk]))
            self.assertEqual(response.status_code, 404, route)

    def test_list_contains_only_groups_from_permitted_owners(self):
        response = self.client.get(reverse('users:usergroup_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.group_a.name)
        self.assertNotContains(response, self.group_b.name)

    def test_bulk_delete_cannot_smuggle_foreign_group_pk(self):
        response = self.client.post(
            reverse('users:usergroup_bulk_delete'),
            {
                'pk': [self.group_b.pk],
                '_confirm': '1',
                'return_url': reverse('users:usergroup_list'),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(UserGroup.objects.filter(pk=self.group_b.pk).exists())


class UserGroupFilterTests(TestCase):
    def test_filters_follow_canonical_relations(self):
        provider = make_tenant('Provider', provider=True)
        customer = make_tenant('Customer', managed_by=provider)
        user = make_user('member')
        membership = Membership.objects.create(user=user, tenant=provider)
        role = Role.objects.create(tenant=provider, name='Reader')
        group = UserGroup.objects.create(name='Readers', tenant=provider)
        GroupMembership.objects.create(user_group=group, membership=membership)
        make_group_grant(
            group,
            role,
            (RoleGrantScope.SCOPE_TENANT, customer),
        )

        for data in (
            {'roles': [role.pk]},
            {'members': user.pk},
            {'grants_tenant': customer.pk},
        ):
            filtered = UserGroupFilterSet(data=data, queryset=UserGroup.objects.all())
            self.assertEqual(list(filtered.qs), [group])
