"""Canonical MembershipForm grant-aggregate coverage."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.tests.mixins import grant
from organization.forms.membership_form import MembershipForm
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.rbac import effective_permissions
from organization.tests._membership_form_helpers import membership_post_data
from users.models import GroupMembership, UserGroup

User = get_user_model()


class MembershipGrantEditorTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_superuser(username='grant-editor')
        self.user = User.objects.create_user(username='technician')
        self.other_user = User.objects.create_user(username='other-technician')
        self.provider = Tenant.objects.create(
            name='Provider', slug='provider', is_provider=True,
        )
        self.customer_a = Tenant.objects.create(
            name='Customer A', slug='customer-a', managed_by=self.provider,
        )
        self.customer_z = Tenant.objects.create(
            name='Customer Z', slug='customer-z', managed_by=self.provider,
        )
        self.read_role = Role.objects.create(
            tenant=self.provider,
            name='Reader',
            permissions=['assets.view_asset'],
        )
        self.admin_role = Role.objects.create(
            tenant=self.provider,
            name='Admin',
            permissions=['assets.view_asset', 'assets.change_asset'],
        )

    def _create_form(self, **overrides):
        data = membership_post_data(
            tenant=self.provider.pk,
            user=self.user.pk,
            who=MembershipForm.WHO_EXISTING,
            own_roles=[],
            managed=[],
        )
        data.update(overrides)
        return MembershipForm(data=data, user=self.actor, tenant=self.provider)

    def _edit_form(self, membership, *, own_roles=None, managed=None, **metadata):
        data = membership_post_data(
            tenant=self.provider.pk,
            user=membership.user_id,
            own_roles=own_roles or [],
            managed=managed or [],
            **metadata,
        )
        return MembershipForm(
            data=data,
            instance=membership,
            user=self.actor,
            tenant=self.provider,
        )

    def test_create_writes_separate_own_and_managed_aggregates(self):
        form = self._create_form()
        form.data = membership_post_data(
            tenant=self.provider.pk,
            user=self.user.pk,
            who=MembershipForm.WHO_EXISTING,
            own_roles=[self.read_role.pk],
            managed=[{
                'role': self.read_role.pk,
                'managed_scope': 'explicit',
                'assigned_tenants': [self.customer_a.pk, self.customer_z.pk],
            }],
        )
        form = MembershipForm(data=form.data, user=self.actor, tenant=self.provider)

        self.assertTrue(form.is_valid(), form.errors.as_json())
        membership = form.save()

        grants = list(membership.role_grants.prefetch_related('scopes'))
        self.assertEqual(len(grants), 2)
        self.assertEqual(
            sorted(scope.scope_type for item in grants for scope in item.scopes.all()),
            ['own', 'tenant', 'tenant'],
        )

    def test_noop_edit_preserves_grant_provenance(self):
        existing = grant(
            self.user,
            self.provider,
            self.read_role,
            reach='managed',
            assigned_tenants=[self.customer_a],
            granted_by=self.actor,
        )
        granted_at = existing.granted_at
        form = self._edit_form(
            existing.membership,
            managed=[{
                'id': existing.pk,
                'role': self.read_role.pk,
                'managed_scope': 'explicit',
                'assigned_tenants': [self.customer_a.pk],
            }],
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        existing.refresh_from_db()
        self.assertEqual(existing.granted_by, self.actor)
        self.assertEqual(existing.granted_at, granted_at)
        self.assertEqual(existing.scopes.get().tenant, self.customer_a)

    def test_scope_change_updates_children_without_replacing_grant(self):
        existing = grant(
            self.user,
            self.provider,
            self.read_role,
            reach='managed',
            assigned_tenants=[self.customer_a],
        )
        form = self._edit_form(
            existing.membership,
            managed=[{
                'id': existing.pk,
                'role': self.read_role.pk,
                'managed_scope': RoleGrantScope.SCOPE_ALL_MANAGED,
            }],
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        existing.refresh_from_db()
        self.assertEqual(
            list(existing.scopes.values_list('scope_type', flat=True)),
            [RoleGrantScope.SCOPE_ALL_MANAGED],
        )

    def test_removing_managed_row_preserves_own_scope_on_same_grant(self):
        existing = grant(self.user, self.provider, self.read_role)
        RoleGrantScope.objects.create(
            role_grant=existing,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )
        form = self._edit_form(
            existing.membership,
            own_roles=[self.read_role.pk],
            managed=[{
                'id': existing.pk,
                'role': self.read_role.pk,
                'managed_scope': 'explicit',
                'assigned_tenants': [self.customer_a.pk],
                'delete': True,
            }],
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        self.assertTrue(RoleGrant.objects.filter(pk=existing.pk).exists())
        self.assertEqual(
            list(existing.scopes.values_list('scope_type', flat=True)),
            [RoleGrantScope.SCOPE_OWN],
        )

    def test_privileged_own_grant_requires_reason_and_future_expiry(self):
        invalid = self._create_form(own_roles=[self.admin_role.pk])
        self.assertFalse(invalid.is_valid())
        self.assertIn('reason', invalid.errors)
        self.assertIn('valid_until', invalid.errors)

        expiry = timezone.now() + timedelta(hours=2)
        valid = self._create_form(
            own_roles=[self.admin_role.pk],
            reason='Temporary incident response',
            valid_until=expiry.isoformat(),
        )
        self.assertTrue(valid.is_valid(), valid.errors.as_json())
        membership = valid.save()
        role_grant = membership.role_grants.get(role=self.admin_role)
        self.assertEqual(role_grant.reason, 'Temporary incident response')
        self.assertIsNotNone(role_grant.valid_until)

    def test_privileged_managed_row_has_per_row_metadata(self):
        expiry = timezone.now() + timedelta(hours=2)
        form = self._create_form()
        data = membership_post_data(
            tenant=self.provider.pk,
            user=self.user.pk,
            who=MembershipForm.WHO_EXISTING,
            own_roles=[],
            managed=[{
                'role': self.admin_role.pk,
                'managed_scope': 'explicit',
                'assigned_tenants': [self.customer_a.pk],
                'reason': 'Customer incident escalation',
                'valid_until': expiry.isoformat(),
            }],
        )
        form = MembershipForm(data=data, user=self.actor, tenant=self.provider)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        role_grant = form.save().role_grants.get(role=self.admin_role)
        self.assertEqual(role_grant.reason, 'Customer incident escalation')

    def test_tampered_grant_id_cannot_modify_another_membership(self):
        foreign = grant(
            self.other_user,
            self.provider,
            self.read_role,
            reach='managed',
            assigned_tenants=[self.customer_z],
        )
        membership = Membership.objects.create(user=self.user, tenant=self.provider)
        form = self._edit_form(
            membership,
            managed=[{
                'id': foreign.pk,
                'role': self.read_role.pk,
                'managed_scope': 'explicit',
                'assigned_tenants': [self.customer_a.pk],
            }],
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        foreign.refresh_from_db()
        self.assertEqual(foreign.scopes.get().tenant, self.customer_z)
        self.assertEqual(membership.role_grants.get().scopes.get().tenant, self.customer_a)

    def test_expired_grant_is_not_seeded_as_active_selection(self):
        expired = RoleGrant.objects.create(
            membership=Membership.objects.create(user=self.user, tenant=self.provider),
            role=self.read_role,
            valid_until=timezone.now() - timedelta(minutes=1),
        )
        RoleGrantScope.objects.create(
            role_grant=expired,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )

        form = MembershipForm(instance=expired.membership, user=self.actor)
        self.assertEqual(list(form.fields['own_roles'].initial), [])


class MembershipGroupReactivationTests(TestCase):
    def setUp(self):
        self.provider = Tenant.objects.create(
            name='Reactivation Provider',
            slug='reactivation-provider',
            is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name='Reactivation Customer',
            slug='reactivation-customer',
            managed_by=self.provider,
        )
        self.actor = User.objects.create_user(
            username='membership-reactivation-admin', password='pw',
        )
        actor_role = Role.objects.create(
            tenant=self.provider,
            name='Membership reactivation manager',
            permissions=[
                'organization.change_membership',
                'organization.add_rolegrant',
                'assets.delete_asset',
            ],
        )
        self.actor_grant = grant(self.actor, self.provider, actor_role)

        self.target_user = User.objects.create_user(
            username='inactive-provider-tech', password='pw',
        )
        self.membership = Membership.objects.create(
            user=self.target_user,
            tenant=self.provider,
            is_active=False,
        )
        self.group = UserGroup.objects.create(
            tenant=self.provider,
            name='Projected asset administrators',
        )
        GroupMembership.objects.create(
            user_group=self.group,
            membership=self.membership,
        )
        projected_role = Role.objects.create(
            tenant=self.provider,
            name='Projected asset deleter',
            permissions=['assets.delete_asset'],
        )
        group_grant = RoleGrant.objects.create(
            user_group=self.group,
            role=projected_role,
        )
        RoleGrantScope.objects.create(
            role_grant=group_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer,
        )

    def reactivation_data(self):
        return membership_post_data(
            tenant=self.provider.pk,
            user=self.target_user.pk,
            own_roles=[],
            managed=[],
            is_active=True,
        )

    def form_for(self, actor):
        return MembershipForm(
            data=self.reactivation_data(),
            instance=self.membership,
            user=actor,
            tenant=self.provider,
        )

    def login_at_provider(self, actor):
        self.client.force_login(actor)
        session = self.client.session
        session['active_tenant_id'] = self.provider.pk
        session.pop('active_tenant_group_id', None)
        session.save()

    def test_form_blocks_restoring_projected_group_outside_actor_reach(self):
        form = self.form_for(self.actor)

        self.assertFalse(form.is_valid())
        self.assertIn('outside your own reach', ' '.join(form.non_field_errors()).lower())

    def test_edit_view_leaves_membership_inactive_when_group_cannot_be_granted(self):
        self.login_at_provider(self.actor)

        response = self.client.post(
            reverse('organization:membership_update', args=[self.membership.pk]),
            self.reactivation_data(),
        )

        self.assertEqual(response.status_code, 200)
        self.membership.refresh_from_db()
        self.assertFalse(self.membership.is_active)
        self.assertIn(
            'outside your own reach',
            ' '.join(response.context['form'].non_field_errors()).lower(),
        )

    def test_form_allows_actor_with_matching_projected_authority(self):
        RoleGrantScope.objects.create(
            role_grant=self.actor_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer,
        )
        form = self.form_for(self.actor)

        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        self.assertIn(
            'assets.delete_asset',
            effective_permissions(self.target_user, self.customer),
        )

    def test_inactive_group_does_not_block_membership_reactivation(self):
        self.group.is_active = False
        self.group.save(update_fields=['is_active'])
        form = self.form_for(self.actor)

        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        self.assertNotIn(
            'assets.delete_asset',
            effective_permissions(self.target_user, self.customer),
        )

    def test_soft_deleted_group_does_not_block_membership_reactivation(self):
        self.group.delete()
        form = self.form_for(self.actor)

        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        self.assertNotIn(
            'assets.delete_asset',
            effective_permissions(self.target_user, self.customer),
        )

    def test_edit_view_allows_superuser_reactivation(self):
        superuser = User.objects.create_superuser(
            username='membership-reactivation-root', password='pw',
        )
        self.login_at_provider(superuser)

        response = self.client.post(
            reverse('organization:membership_update', args=[self.membership.pk]),
            self.reactivation_data(),
        )

        self.assertEqual(response.status_code, 302)
        self.membership.refresh_from_db()
        self.assertTrue(self.membership.is_active)
