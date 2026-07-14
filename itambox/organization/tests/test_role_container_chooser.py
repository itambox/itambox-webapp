"""Successor coverage for the deleted /roles/add/ tenant-vs-provider container chooser.

Pre-collapse, ``Role`` had two possible owners (``Tenant`` XOR ``Provider``) and
``RoleEditView`` rendered a picker (``allow_container_choice``) so the operator could
choose which container a new role belonged to; ``RoleForm.clean()`` derived
``Role.scope`` from whichever container was picked and rejected "both" / "neither".

Post-collapse there is exactly one container (``Tenant``; a provider is just a
``Tenant`` with ``is_provider=True``) and exactly one way to pick it: context. A role's
owner comes from the ``?tenant=`` deep-link or the active tenant — never a form field —
and is immutable once the role exists. The chooser, ``allow_container_choice``,
``is_container_chooser``, and ``Role.scope`` are gone; there is nothing left to choose
and nothing left to derive.

This module now covers the successor invariants (``organization/forms/role_form.py``):

  * ``RoleForm`` requires a resolvable tenant (``?tenant=`` kwarg or ambient active
    tenant) and fails closed with no owner picker to fall back on.
  * the owner is rendered read-only (a disabled text input, never a submittable field)
    and is locked to the instance's own tenant on edit regardless of any kwarg passed.
  * ``shared_with_managed`` — the sharing flag that replaces the old provider-role
    capability model — is only offered (and only meaningful) when the owner tenant is
    itself a managing (``is_provider``) tenant, and it round-trips through save/reload.
  * the permission matrix carries the canonical ``rolegrant`` row (RoleGrant is
    itself a first-class, permissionable model now).
  * the privilege-escalation guard (``validate_permission_grant``) still fires on save,
    both at the form layer and end-to-end through the view — a low-privilege actor
    holding the CRUD permission to create a role cannot use that screen to grant
    permissions they do not themselves hold.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.tests.mixins import TenantTestMixin
from organization.forms import RoleForm
from organization.forms.role_form import MATRIX_MODELS
from organization.models import (
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
    TenantGroup,
)
from users.models import UserGroup


User = get_user_model()


class RoleFormOwnerResolutionTests(TenantTestMixin, TestCase):
    """The owner tenant comes from context; there is no picker to submit."""

    def setUp(self):
        self.clear_tenant_context()
        self.setup_tenant_context()
        self.superuser = self.tenant_admin  # a superuser, per the mixin

    def tearDown(self):
        self.clear_tenant_context()

    def test_no_tenant_context_is_rejected(self):
        # No ?tenant= kwarg, no active tenant context -> the form has nowhere to bind
        # the new role and fails closed instead of silently creating an orphan.
        form = RoleForm(data={'name': 'Homeless'}, user=self.superuser)
        self.assertFalse(form.is_valid())
        errs = ' '.join(form.non_field_errors()).lower()
        self.assertIn('tenant context', errs)

    def test_tenant_kwarg_binds_owner_on_create(self):
        form = RoleForm(data={'name': 'Tenant Ops'}, tenant=self.tenant, user=self.superuser)
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        self.assertEqual(role.tenant_id, self.tenant.pk)

    def test_active_tenant_context_binds_owner_when_no_kwarg(self):
        # Falls back to the ambient active tenant (the ?tenant= deep-link is optional).
        with self.tenant_context(self.tenant, self.tenant_membership):
            form = RoleForm(data={'name': 'Ambient Ops'}, user=self.superuser)
            self.assertTrue(form.is_valid(), form.errors)
            role = form.save()
        self.assertEqual(role.tenant_id, self.tenant.pk)

    def test_tenant_kwarg_takes_precedence_over_active_context(self):
        other = Tenant.objects.create(name='Other Co', slug='other-co')
        with self.tenant_context(other):
            form = RoleForm(data={'name': 'Explicit Ops'}, tenant=self.tenant, user=self.superuser)
            self.assertTrue(form.is_valid(), form.errors)
            role = form.save()
        self.assertEqual(role.tenant_id, self.tenant.pk)

    def test_owner_is_not_a_submittable_field(self):
        # There is deliberately no owner picker: 'tenant' never appears as a form
        # field, so nothing in POST data can retarget a role's owner.
        form = RoleForm(data={'name': 'X'}, tenant=self.tenant, user=self.superuser)
        self.assertNotIn('tenant', form.fields)
        self.assertNotIn('provider', form.fields)

    def test_edit_locks_owner_to_instance_tenant_ignoring_kwarg(self):
        other = Tenant.objects.create(name='Other Co 2', slug='other-co-2')
        role = Role.objects.create(tenant=self.tenant, name='Editable', permissions=[])
        form = RoleForm(
            data={'name': 'Editable (renamed)'},
            instance=role, tenant=other, user=self.superuser,
        )
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.tenant_id, self.tenant.pk)
        self.assertNotEqual(saved.tenant_id, other.pk)


class RoleFormSharedWithManagedTests(TenantTestMixin, TestCase):
    """``shared_with_managed`` only exists (and only means something) for a
    managing (``is_provider``) owner tenant."""

    def setUp(self):
        self.clear_tenant_context()
        self.setup_tenant_context()
        self.superuser = self.tenant_admin
        self.msp_tenant = Tenant.objects.create(
            name='Northwind MSP', slug='northwind-msp', is_provider=True,
        )

    def tearDown(self):
        self.clear_tenant_context()

    def test_checkbox_absent_for_non_provider_owner(self):
        form = RoleForm(tenant=self.tenant, user=self.superuser)
        self.assertNotIn('shared_with_managed', form.fields)

    def test_checkbox_present_for_provider_owner(self):
        form = RoleForm(tenant=self.msp_tenant, user=self.superuser)
        self.assertIn('shared_with_managed', form.fields)

    def test_checkbox_present_via_active_context_too(self):
        with self.tenant_context(self.msp_tenant):
            form = RoleForm(user=self.superuser)
        self.assertIn('shared_with_managed', form.fields)

    def test_shared_with_managed_persists_through_save_and_reload(self):
        form = RoleForm(
            data={'name': 'MSP Technician', 'shared_with_managed': 'on'},
            tenant=self.msp_tenant, user=self.superuser,
        )
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        self.assertTrue(role.shared_with_managed)

        role.refresh_from_db()
        self.assertTrue(role.shared_with_managed)

        edit_form = RoleForm(instance=role, user=self.superuser)
        self.assertIn('shared_with_managed', edit_form.fields)
        self.assertTrue(edit_form['shared_with_managed'].value())

    def test_unchecked_default_does_not_set_the_flag(self):
        form = RoleForm(data={'name': 'Local Only'}, tenant=self.msp_tenant, user=self.superuser)
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        self.assertFalse(role.shared_with_managed)


class RoleFormMatrixIncludesRoleGrantTests(TenantTestMixin, TestCase):
    """Canonical grant models are permissionable, so the matrix must offer them."""

    def setUp(self):
        self.clear_tenant_context()
        self.setup_tenant_context()
        self.superuser = self.tenant_admin

    def tearDown(self):
        self.clear_tenant_context()

    def test_matrix_declares_a_rolegrant_row(self):
        self.assertIn('rolegrant', MATRIX_MODELS)
        info = MATRIX_MODELS['rolegrant']
        self.assertEqual(info['app'], 'organization')
        self.assertEqual(info['model_name'], 'rolegrant')

    def test_form_renders_rolegrant_checkboxes(self):
        form = RoleForm(tenant=self.tenant, user=self.superuser)
        for action in ('read', 'create', 'edit', 'delete'):
            self.assertIn(f'perm_rolegrant_{action}', form.fields)

    def test_rolegrant_permissions_round_trip_through_save(self):
        data = {
            'name': 'Grant Manager',
            'perm_rolegrant_read': 'on',
            'perm_rolegrant_create': 'on',
            'perm_rolegrant_edit': 'on',
            'perm_rolegrant_delete': 'on',
        }
        form = RoleForm(data=data, tenant=self.tenant, user=self.superuser)
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        for codename in ('view_rolegrant', 'add_rolegrant',
                          'change_rolegrant', 'delete_rolegrant'):
            self.assertIn(f'organization.{codename}', role.permissions)

    def test_tenantresourcegrant_permissions_round_trip_through_save(self):
        self.assertIn('tenantresourcegrant', MATRIX_MODELS)
        info = MATRIX_MODELS['tenantresourcegrant']
        self.assertEqual(info['app'], 'organization')
        self.assertEqual(info['model_name'], 'tenantresourcegrant')

        data = {
            'name': 'Resource Grant Manager',
            'perm_tenantresourcegrant_read': 'on',
            'perm_tenantresourcegrant_create': 'on',
            'perm_tenantresourcegrant_edit': 'on',
            'perm_tenantresourcegrant_delete': 'on',
        }
        form = RoleForm(data=data, tenant=self.tenant, user=self.superuser)
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        for codename in (
            'view_tenantresourcegrant', 'add_tenantresourcegrant',
            'change_tenantresourcegrant', 'delete_tenantresourcegrant',
        ):
            self.assertIn(f'organization.{codename}', role.permissions)


class RoleFormEscalationGuardTests(TenantTestMixin, TestCase):
    """The privilege-escalation guard still fires on save (form layer)."""

    def setUp(self):
        self.clear_tenant_context()
        # self.tenant_user comes out of the mixin bound (via grant()) to a role with
        # an empty permission set — the low-privilege actor for these tests.
        self.setup_tenant_context()

    def tearDown(self):
        self.clear_tenant_context()

    def test_actor_without_the_permission_cannot_grant_it(self):
        form = RoleForm(
            data={'name': 'Rogue Admin', 'perm_asset_delete': 'on'},
            tenant=self.tenant, user=self.tenant_user,
        )
        self.assertFalse(form.is_valid())
        errs = ' '.join(form.non_field_errors())
        self.assertIn('Privilege escalation detected', errs)
        self.assertFalse(Role._base_manager.filter(name='Rogue Admin').exists())

    def test_actor_holding_the_permission_can_grant_it(self):
        # Give tenant_user assets.delete_asset via a second grant() call, then confirm
        # they can now create a role carrying that same permission. Any non-empty save
        # also auto-adds the dashboard perms (see RoleForm.clean()), so the actor's own
        # held set must cover those too, or the guard rejects the auto-added perms.
        asset_role = Role.objects.create(
            tenant=self.tenant, name='Asset Admin',
            permissions=[
                'assets.delete_asset',
                'extras.view_dashboard', 'extras.change_dashboard', 'extras.add_dashboard',
            ],
        )
        self.grant(self.tenant_user, self.tenant, asset_role)

        form = RoleForm(
            data={'name': 'Delegate', 'perm_asset_delete': 'on'},
            tenant=self.tenant, user=self.tenant_user,
        )
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        self.assertIn('assets.delete_asset', role.permissions)

    def test_superuser_bypasses_the_guard(self):
        form = RoleForm(
            data={'name': 'Superuser Admin', 'perm_asset_delete': 'on'},
            tenant=self.tenant, user=self.tenant_admin,
        )
        self.assertTrue(form.is_valid(), form.errors)
        role = form.save()
        self.assertIn('assets.delete_asset', role.permissions)


class RoleFormRetainedProjectionGuardTests(TenantTestMixin, TestCase):
    """Editing a live role must be safe in every tenant it already reaches."""

    BASE_PERMISSIONS = [
        'assets.view_asset',
        'extras.view_dashboard',
        'extras.change_dashboard',
        'extras.add_dashboard',
    ]
    RESULT_PERMISSIONS = [*BASE_PERMISSIONS, 'assets.change_asset']

    def setUp(self):
        self.clear_tenant_context()
        self.provider = Tenant.objects.create(
            name='Projection Provider',
            slug='projection-provider',
            is_provider=True,
        )
        self.scope_group = TenantGroup.objects.create(
            name='Projection Customers',
            slug='projection-customers',
        )
        self.customer = Tenant.objects.create(
            name='Projection Customer',
            slug='projection-customer',
            managed_by=self.provider,
            group=self.scope_group,
        )
        self.actor = User.objects.create_user(username='projection-editor')
        actor_home_role = Role.objects.create(
            tenant=self.provider,
            name='Projection role editor authority',
            permissions=[
                *self.RESULT_PERMISSIONS,
                'organization.change_rolegrant',
            ],
        )
        self.grant(self.actor, self.provider, actor_home_role)
        self.role = Role.objects.create(
            tenant=self.provider,
            name='Projected operator',
            permissions=self.BASE_PERMISSIONS,
            shared_with_managed=True,
        )

    def tearDown(self):
        self.clear_tenant_context()

    def _membership(self, tenant, suffix):
        user = User.objects.create_user(username=f'projection-{suffix}')
        return Membership.objects.create(user=user, tenant=tenant)

    def _direct_grant(self, membership):
        return RoleGrant.objects.create(
            membership=membership,
            role=self.role,
            reason='Retained projection test grant',
            valid_until=timezone.now() + timedelta(days=1),
        )

    def _edit_form(self, *, shared=True, add_change=True, user=None):
        data = {
            'name': self.role.name,
            'perm_asset_read': 'on',
        }
        if shared:
            data['shared_with_managed'] = 'on'
        if add_change:
            data['perm_asset_edit'] = 'on'
        return RoleForm(
            data=data,
            instance=self.role,
            tenant=self.provider,
            user=user or self.actor,
        )

    def _give_actor_explicit_customer_authority(self, permissions):
        role = Role.objects.create(
            tenant=self.provider,
            name=f'Actor customer authority {len(permissions)}',
            permissions=list(permissions),
        )
        self.grant(
            self.actor,
            self.provider,
            role,
            reach=RoleGrant.REACH_MANAGED,
            assigned_tenants=[self.customer],
        )

    def _give_actor_all_managed_authority(self):
        role = Role.objects.create(
            tenant=self.provider,
            name='Actor all-managed authority',
            permissions=self.RESULT_PERMISSIONS,
        )
        self.grant(
            self.actor,
            self.provider,
            role,
            reach=RoleGrant.REACH_MANAGED,
            managed_scope=RoleGrantScope.SCOPE_ALL_MANAGED,
        )

    def test_provider_home_authority_cannot_broaden_customer_own_scope(self):
        grant = self._direct_grant(
            self._membership(self.customer, 'customer-own'),
        )
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )

        form = self._edit_form()

        self.assertFalse(form.is_valid())
        self.assertIn('assets.change_asset', str(form.non_field_errors()))

    def test_reenabling_sharing_revalidates_unchanged_permissions(self):
        self.role.permissions = self.RESULT_PERMISSIONS
        self.role.save(update_fields=['permissions'])
        grant = self._direct_grant(
            self._membership(self.customer, 'customer-reenable'),
        )
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        self.role.shared_with_managed = False
        self.role.save(update_fields=['shared_with_managed'])

        form = self._edit_form(add_change=True)

        self.assertFalse(form.is_valid())
        self.assertIn('Privilege escalation detected', str(form.non_field_errors()))

    def test_explicit_scope_requires_resulting_permissions_in_target(self):
        self._give_actor_explicit_customer_authority(self.BASE_PERMISSIONS)
        grant = self._direct_grant(
            self._membership(self.provider, 'explicit-principal'),
        )
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer,
        )

        form = self._edit_form()

        self.assertFalse(form.is_valid())
        self.assertIn('assets.change_asset', str(form.non_field_errors()))

    def test_tenant_group_scope_requires_all_managed_authority(self):
        self._give_actor_explicit_customer_authority(self.RESULT_PERMISSIONS)
        group = UserGroup.objects.create(
            tenant=self.provider,
            name='Projected provider team',
        )
        grant = RoleGrant.objects.create(user_group=group, role=self.role)
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_TENANT_GROUP,
            tenant_group=self.scope_group,
        )

        form = self._edit_form()

        self.assertFalse(form.is_valid())
        self.assertIn('dynamic managed-tenant scope', str(form.non_field_errors()))

    def test_all_managed_scope_requires_all_managed_authority(self):
        self._give_actor_explicit_customer_authority(self.RESULT_PERMISSIONS)
        grant = self._direct_grant(
            self._membership(self.provider, 'all-principal'),
        )
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_ALL_MANAGED,
        )

        form = self._edit_form()

        self.assertFalse(form.is_valid())
        self.assertIn('dynamic managed-tenant scope', str(form.non_field_errors()))

    def test_actor_with_matching_all_managed_authority_can_edit_every_scope(self):
        self._give_actor_all_managed_authority()
        customer_grant = self._direct_grant(
            self._membership(self.customer, 'valid-customer-own'),
        )
        RoleGrantScope.objects.create(
            role_grant=customer_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        provider_grant = self._direct_grant(
            self._membership(self.provider, 'valid-provider-principal'),
        )
        RoleGrantScope.objects.create(
            role_grant=provider_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer,
        )
        RoleGrantScope.objects.create(
            role_grant=provider_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT_GROUP,
            tenant_group=self.scope_group,
        )
        RoleGrantScope.objects.create(
            role_grant=provider_grant,
            scope_type=RoleGrantScope.SCOPE_ALL_MANAGED,
        )

        form = self._edit_form()

        self.assertTrue(form.is_valid(), form.errors)

    def test_superuser_can_edit_role_with_retained_projection(self):
        grant = self._direct_grant(
            self._membership(self.customer, 'superuser-customer'),
        )
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        superuser = User.objects.create_superuser(
            username='projection-root',
            email='projection-root@example.com',
            password='pw',
        )

        form = self._edit_form(user=superuser)

        self.assertTrue(form.is_valid(), form.errors)


class RoleFormPrivilegeTransitionTests(TenantTestMixin, TestCase):
    """Editing a role cannot bypass temporary-direct-grant policy."""

    def setUp(self):
        self.clear_tenant_context()
        self.setup_tenant_context()
        self.role = Role.objects.create(
            tenant=self.tenant,
            name='Viewer',
            permissions=['assets.view_asset'],
        )
        self.direct_grant = RoleGrant.objects.create(
            membership=self.tenant_membership,
            role=self.role,
        )
        RoleGrantScope.objects.create(
            role_grant=self.direct_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )

    def tearDown(self):
        self.clear_tenant_context()

    def _elevating_form(self):
        return RoleForm(
            data={'name': 'Viewer', 'perm_asset_edit': 'on'},
            instance=self.role,
            tenant=self.tenant,
            user=self.tenant_admin,
        )

    def test_rejects_elevation_with_permanent_direct_grant(self):
        form = self._elevating_form()
        self.assertFalse(form.is_valid())
        self.assertIn('direct grants', str(form.non_field_errors()))

    def test_allows_elevation_after_direct_grant_gets_expiration_metadata(self):
        self.direct_grant.reason = 'Temporary operational escalation'
        self.direct_grant.valid_until = timezone.now() + timedelta(hours=2)
        self.direct_grant.save(update_fields=['reason', 'valid_until'])

        form = self._elevating_form()
        self.assertTrue(form.is_valid(), form.errors)


class RoleFormViewIntegrationTests(TenantTestMixin, TestCase):
    """End-to-end coverage through ``RoleEditView`` for the same invariants."""

    def setUp(self):
        self.clear_tenant_context()
        self.setup_tenant_context()
        self.superuser = self.tenant_admin
        self.client.force_login(self.superuser)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

    def tearDown(self):
        self.clear_tenant_context()

    def test_add_page_renders_owner_read_only_with_no_container_picker(self):
        resp = self.client.get(reverse('organization:role_create'))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        # The deleted chooser never comes back: no owner-picker inputs.
        self.assertNotIn('name="tenant"', html)
        self.assertNotIn('name="provider"', html)
        self.assertNotIn('role-container-chooser', html)
        # The owner is shown read-only (a disabled display, not an input).
        self.assertIn(self.tenant.name, html)
        # The matrix includes the canonical role-grant row.
        self.assertIn('name="perm_rolegrant_read"', html)

    def test_add_page_via_tenant_deep_link_creates_role_bound_to_that_tenant(self):
        other = Tenant.objects.create(name='Deep Link Co', slug='deep-link-co')
        url = reverse('organization:role_create') + f'?tenant={other.pk}'
        resp = self.client.post(url, {'name': 'Deep Link Role'})
        self.assertIn(resp.status_code, (301, 302))
        role = Role._base_manager.get(name='Deep Link Role')
        self.assertEqual(role.tenant_id, other.pk)

    def test_edit_page_hides_sharing_for_a_non_provider_owned_role(self):
        role = Role.objects.create(tenant=self.tenant, name='Editable', permissions=[])
        resp = self.client.get(reverse('organization:role_update', kwargs={'pk': role.pk}))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertNotIn('name="shared_with_managed"', html)
        self.assertIn(self.tenant.name, html)

    def test_edit_page_offers_sharing_for_a_provider_owned_role(self):
        msp = Tenant.objects.create(name='Provider Co', slug='provider-co', is_provider=True)
        role = Role.objects.create(tenant=msp, name='Shared Role', permissions=[])
        # The active tenant must be the role's own tenant: RoleEditView resolves the
        # edit target through the tenant-scoped default manager.
        session = self.client.session
        session['active_tenant_id'] = msp.pk
        session.save()
        resp = self.client.get(reverse('organization:role_update', kwargs={'pk': role.pk}))
        self.assertEqual(resp.status_code, 200)
        self.assertIn('name="shared_with_managed"', resp.content.decode())

    def test_escalation_guard_fires_through_the_view(self):
        # A low-privilege actor who holds ONLY the CRUD permission to reach this
        # screen (organization.add_role) still cannot use it to grant a permission
        # they do not themselves hold — the guard is independent of the view's
        # object-level permission gate.
        grantor_role = Role.objects.create(
            tenant=self.tenant, name='Role Manager',
            permissions=['organization.add_role', 'organization.view_role'],
        )
        self.grant(self.tenant_user, self.tenant, grantor_role)
        self.client.force_login(self.tenant_user)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

        resp = self.client.post(
            reverse('organization:role_create'),
            {'name': 'Rogue Admin', 'perm_asset_delete': 'on'},
        )
        self.assertEqual(resp.status_code, 200)  # redisplayed with errors, not redirected
        self.assertFalse(Role._base_manager.filter(name='Rogue Admin').exists())
