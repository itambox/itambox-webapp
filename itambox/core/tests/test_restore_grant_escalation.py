"""Restore-time escalation guards for retained Role and UserGroup grants."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.tests.mixins import grant
from organization.models import (
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Site,
    Tenant,
    TenantGroup,
)
from users.models import GroupMembership, UserGroup

User = get_user_model()


class RestoreGrantEscalationMixin:
    restore_model = None

    def setUp(self):
        self.provider = Tenant.objects.create(
            name='Restore Provider',
            slug='restore-provider',
            is_provider=True,
        )
        self.customer_group = TenantGroup.objects.create(
            name='Restore Customers',
            slug='restore-customers',
        )
        self.customer = Tenant.objects.create(
            name='Restore Customer',
            slug='restore-customer',
            managed_by=self.provider,
            group=self.customer_group,
        )
        self.actor = User.objects.create_user(
            username='restore-actor',
            password='pw',
        )
        actor_role = Role.objects.create(
            tenant=self.provider,
            name='Restore operator',
            permissions=[
                'assets.view_asset',
                'core.change_recyclebin',
                'organization.add_rolegrant',
                'organization.change_rolegrant',
                'organization.change_role',
                'users.change_usergroup',
            ],
        )
        grant(self.actor, self.provider, actor_role)
        self.recipient_membership = Membership.objects.create(
            user=User.objects.create_user(username='restore-recipient'),
            tenant=self.provider,
        )

        self.client.force_login(self.actor)
        session = self.client.session
        session['active_tenant_id'] = self.provider.pk
        session.pop('active_tenant_group_id', None)
        session.save()

    def add_explicit_authority(self):
        role = Role.objects.create(
            tenant=self.provider,
            name='Explicit restore authority',
            permissions=['assets.view_asset'],
        )
        grant(
            self.actor,
            self.provider,
            role,
            reach=RoleGrant.REACH_MANAGED,
            assigned_tenants=[self.customer],
        )

    def add_all_managed_authority(self):
        role = Role.objects.create(
            tenant=self.provider,
            name='Dynamic restore authority',
            permissions=['assets.view_asset'],
        )
        grant(
            self.actor,
            self.provider,
            role,
            reach=RoleGrant.REACH_MANAGED,
            managed_scope=RoleGrantScope.SCOPE_ALL_MANAGED,
        )

    def make_deleted_target(
        self,
        name,
        *,
        permission='assets.view_asset',
        scope_type=RoleGrantScope.SCOPE_OWN,
        is_active=True,
    ):
        role = Role.objects.create(
            tenant=self.provider,
            name=f'{name} role',
            permissions=[permission],
        )
        if self.restore_model is Role:
            target = role
            role_grant = RoleGrant.objects.create(
                membership=self.recipient_membership,
                role=role,
                reason='Retained restore test grant',
                valid_until=timezone.now() + timedelta(days=30),
            )
        else:
            target = UserGroup.objects.create(
                tenant=self.provider,
                name=name,
                is_active=is_active,
            )
            GroupMembership.objects.create(
                user_group=target,
                membership=self.recipient_membership,
            )
            role_grant = RoleGrant.objects.create(
                user_group=target,
                role=role,
            )

        scope_kwargs = {
            'role_grant': role_grant,
            'scope_type': scope_type,
        }
        if scope_type == RoleGrantScope.SCOPE_TENANT:
            scope_kwargs['tenant'] = self.customer
        elif scope_type == RoleGrantScope.SCOPE_TENANT_GROUP:
            scope_kwargs['tenant_group'] = self.customer_group
        RoleGrantScope.objects.create(**scope_kwargs)

        target.delete()
        target = type(target)._base_manager.get(pk=target.pk)
        self.assertIsNotNone(target.deleted_at)
        self.assertTrue(RoleGrant.objects.filter(pk=role_grant.pk).exists())
        self.assertTrue(role_grant.scopes.exists())
        return target

    def restore(self, target):
        content_type = ContentType.objects.get_for_model(type(target))
        return self.client.post(reverse('object_restore', kwargs={
            'content_type_id': content_type.pk,
            'object_id': target.pk,
        }))

    def retained_grant(self, target):
        if self.restore_model is Role:
            return RoleGrant.objects.get(
                role_id=target.pk,
                membership=self.recipient_membership,
            )
        return RoleGrant.objects.get(user_group_id=target.pk)

    def assert_blocked(self, target):
        response = self.restore(target)
        self.assertEqual(response.status_code, 403)
        target.refresh_from_db()
        self.assertIsNotNone(target.deleted_at)

    def assert_restored(self, target):
        response = self.restore(target)
        self.assertEqual(response.status_code, 302)
        target.refresh_from_db()
        self.assertIsNone(target.deleted_at)

    def test_own_scope_rejects_permission_the_actor_cannot_grant(self):
        target = self.make_deleted_target(
            'Unsafe own restore',
            permission='assets.delete_asset',
        )
        self.assert_blocked(target)

    def test_own_scope_allows_permission_the_actor_holds(self):
        target = self.make_deleted_target('Safe own restore')
        self.assert_restored(target)

    def test_explicit_projection_rejects_target_outside_actor_reach(self):
        target = self.make_deleted_target(
            'Unsafe explicit restore',
            scope_type=RoleGrantScope.SCOPE_TENANT,
        )
        self.assert_blocked(target)

    def test_explicit_projection_allows_matching_permission_and_reach(self):
        self.add_explicit_authority()
        target = self.make_deleted_target(
            'Safe explicit restore',
            scope_type=RoleGrantScope.SCOPE_TENANT,
        )
        self.assert_restored(target)

    def test_group_projection_rejects_narrower_explicit_authority(self):
        self.add_explicit_authority()
        target = self.make_deleted_target(
            'Unsafe dynamic group restore',
            scope_type=RoleGrantScope.SCOPE_TENANT_GROUP,
        )
        self.assert_blocked(target)

    def test_group_projection_allows_equivalent_dynamic_authority(self):
        self.add_all_managed_authority()
        target = self.make_deleted_target(
            'Safe dynamic group restore',
            scope_type=RoleGrantScope.SCOPE_TENANT_GROUP,
        )
        self.assert_restored(target)

    def test_all_managed_projection_rejects_narrower_explicit_authority(self):
        self.add_explicit_authority()
        target = self.make_deleted_target(
            'Unsafe all-managed restore',
            scope_type=RoleGrantScope.SCOPE_ALL_MANAGED,
        )
        self.assert_blocked(target)

    def test_all_managed_projection_allows_equivalent_dynamic_authority(self):
        self.add_all_managed_authority()
        target = self.make_deleted_target(
            'Safe all-managed restore',
            scope_type=RoleGrantScope.SCOPE_ALL_MANAGED,
        )
        self.assert_restored(target)

    def test_expired_historical_grant_does_not_block_restore(self):
        target = self.make_deleted_target(
            'Expired unsafe restore',
            permission='assets.delete_asset',
        )
        RoleGrant.objects.filter(pk=self.retained_grant(target).pk).update(
            valid_until=timezone.now() - timedelta(seconds=1),
        )
        self.assert_restored(target)

    def test_severed_explicit_scope_does_not_block_restore(self):
        target = self.make_deleted_target(
            'Severed explicit restore',
            permission='assets.delete_asset',
            scope_type=RoleGrantScope.SCOPE_TENANT,
        )
        self.customer.managed_by = None
        self.customer.save(update_fields=['managed_by'])
        self.assert_restored(target)

    def test_deleted_tenant_group_scope_does_not_block_restore(self):
        target = self.make_deleted_target(
            'Deleted group scope restore',
            permission='assets.delete_asset',
            scope_type=RoleGrantScope.SCOPE_TENANT_GROUP,
        )
        TenantGroup._base_manager.filter(pk=self.customer_group.pk).update(
            deleted_at=timezone.now(),
        )
        self.assert_restored(target)

    def test_empty_all_managed_scope_remains_guarded(self):
        target = self.make_deleted_target(
            'Empty all-managed restore',
            permission='assets.delete_asset',
            scope_type=RoleGrantScope.SCOPE_ALL_MANAGED,
        )
        self.customer.managed_by = None
        self.customer.save(update_fields=['managed_by'])
        self.assert_blocked(target)

    def test_bulk_restore_skips_unsafe_row_and_restores_safe_row(self):
        safe = self.make_deleted_target('Bulk safe restore')
        unsafe = self.make_deleted_target(
            'Bulk unsafe restore',
            permission='assets.delete_asset',
        )
        content_type = ContentType.objects.get_for_model(self.restore_model)

        response = self.client.post(
            reverse('object_bulk_restore', kwargs={
                'content_type_id': content_type.pk,
            }),
            {'pk': [safe.pk, unsafe.pk]},
        )

        self.assertEqual(response.status_code, 302)
        safe.refresh_from_db()
        unsafe.refresh_from_db()
        self.assertIsNone(safe.deleted_at)
        self.assertIsNotNone(unsafe.deleted_at)
        rendered_messages = [
            str(message) for message in get_messages(response.wsgi_request)
        ]
        self.assertTrue(any(
            'Skipped 1' in message
            and 'outside your authority' in message
            for message in rendered_messages
        ), rendered_messages)


class RoleRestoreGrantEscalationTests(RestoreGrantEscalationMixin, TestCase):
    restore_model = Role

    def make_role_with_group_principal(self, name, *, group_active=True):
        role = Role.objects.create(
            tenant=self.provider,
            name=name,
            permissions=['assets.delete_asset'],
        )
        principal_group = UserGroup.objects.create(
            tenant=self.provider,
            name=f'{name} principal',
            is_active=group_active,
        )
        GroupMembership.objects.create(
            user_group=principal_group,
            membership=self.recipient_membership,
        )
        role_grant = RoleGrant.objects.create(
            user_group=principal_group,
            role=role,
        )
        RoleGrantScope.objects.create(
            role_grant=role_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        role.delete()
        return Role._base_manager.get(pk=role.pk), principal_group

    def test_inactive_membership_principal_does_not_block_restore(self):
        target = self.make_deleted_target(
            'Inactive membership restore',
            permission='assets.delete_asset',
        )
        self.recipient_membership.is_active = False
        self.recipient_membership.save(update_fields=['is_active'])
        self.assert_restored(target)

    def test_inactive_user_group_principal_does_not_block_restore(self):
        target, _group = self.make_role_with_group_principal(
            'Inactive group principal restore',
            group_active=False,
        )
        self.assert_restored(target)

    def test_deleted_user_group_principal_does_not_block_restore(self):
        target, principal_group = self.make_role_with_group_principal(
            'Deleted group principal restore',
        )
        principal_group.delete()
        self.assert_restored(target)

    def test_deleted_principal_tenant_does_not_block_restore(self):
        role = Role.objects.create(
            tenant=self.provider,
            name='Deleted tenant principal restore',
            permissions=['assets.delete_asset'],
            shared_with_managed=True,
        )
        membership = Membership.objects.create(
            user=User.objects.create_user(username='deleted-tenant-recipient'),
            tenant=self.customer,
        )
        role_grant = RoleGrant.objects.create(
            membership=membership,
            role=role,
            reason='Historical deleted-tenant grant',
            valid_until=timezone.now() + timedelta(days=30),
        )
        RoleGrantScope.objects.create(
            role_grant=role_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        Tenant._base_manager.filter(pk=self.customer.pk).update(
            deleted_at=timezone.now(),
        )
        role.delete()

        self.assert_restored(Role._base_manager.get(pk=role.pk))


class UserGroupRestoreGrantEscalationTests(
    RestoreGrantEscalationMixin,
    TestCase,
):
    restore_model = UserGroup

    def test_inactive_group_restore_does_not_reactivate_its_grants(self):
        target = self.make_deleted_target(
            'Inactive unsafe group',
            permission='assets.delete_asset',
            is_active=False,
        )
        self.assert_restored(target)

    def test_deleted_target_role_does_not_block_group_restore(self):
        target = self.make_deleted_target(
            'Deleted target role group',
            permission='assets.delete_asset',
        )
        role = self.retained_grant(target).role
        role.delete()
        self.assert_restored(target)


class UnrelatedModelRestoreRegressionTests(TestCase):
    def test_restore_behavior_is_unchanged_for_unrelated_soft_delete_model(self):
        tenant = Tenant.objects.create(
            name='Unrelated Restore Tenant',
            slug='unrelated-restore-tenant',
        )
        actor = User.objects.create_user(
            username='unrelated-restore-actor',
            password='pw',
        )
        actor_role = Role.objects.create(
            tenant=tenant,
            name='Site restore operator',
            permissions=[
                'core.change_recyclebin',
                'organization.change_site',
            ],
        )
        grant(actor, tenant, actor_role)
        site = Site.objects.create(name='Unrelated restore site', tenant=tenant)
        site.delete()

        self.client.force_login(actor)
        session = self.client.session
        session['active_tenant_id'] = tenant.pk
        session.save()
        content_type = ContentType.objects.get_for_model(Site)
        response = self.client.post(reverse('object_restore', kwargs={
            'content_type_id': content_type.pk,
            'object_id': site.pk,
        }))

        self.assertEqual(response.status_code, 302)
        site.refresh_from_db()
        self.assertIsNone(site.deleted_at)
