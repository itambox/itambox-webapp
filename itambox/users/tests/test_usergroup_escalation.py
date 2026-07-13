"""Regression tests for the UserGroup grant-path escalation guards (RBAC review §3-B/§3-C).

Two write paths could grant cross-tenant access without checking whether the acting
group-admin actually held the permissions being conferred:

  * §3-B — ``UserGroupForm.tenant`` (the group's owning/SCIM-scope tenant, formerly
    ``provider``): an MSP-A group admin could point a group at MSP-B (a managing
    tenant they do not administer), handing B's SCIM-synced staff every role the
    group already carried (cross-tenant takeover). The ``tenant`` value must be
    scoped and validated against the actor's ``users.change_usergroup`` on that
    tenant.
  * §3-C — ``UserGroupAssignUsersView``: adding a member is itself a grant (the member
    inherits every role the group carries, plus each role's tenant access), but the view
    must gate this on an actual per-role permission check, not just group-admin status
    on some unrelated tenant.

These tests assert both paths reject a low-privilege actor and still allow superusers
and legitimate same-tenant grants.

Post RBAC structural collapse (RBAC_STAGE2_SPEC.md): ``organization.Provider`` is
deleted — a "provider"/MSP tenant is now ``Tenant(is_provider=True)``; grants are
per-``RoleAssignment`` rows (``reach='own'`` in these tests) hung off a
``Membership``, created here via ``core.tests.mixins.grant``.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.managers import set_current_tenant, set_current_membership
from core.tests.mixins import TenantTestMixin, grant
from organization.models import Membership, Tenant, Role
from users.models import UserGroup

User = get_user_model()


class UserGroupEscalationTests(TenantTestMixin, TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)

        # Two managing (MSP) tenants. The acting group-admin manages only tenant A.
        self.tenant_a = Tenant.objects.create(name="MSP A", slug="msp-a", is_provider=True)
        self.tenant_b = Tenant.objects.create(name="MSP B", slug="msp-b", is_provider=True)

        # Tenant-A group admin: an own-reach role granting users.change_usergroup at A.
        self.admin_a = User.objects.create_user(
            username="admin_a", email="admin_a@example.com", password="pw",
        )
        role_a = Role.objects.create(
            tenant=self.tenant_a, name="A Group Admin",
            permissions=["users.change_usergroup"],
        )
        self.assignment_a = grant(self.admin_a, self.tenant_a, role_a)
        self.staff_a = self.assignment_a.membership

        self.superuser = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw",
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _flush(self, user):
        for attr in list(user.__dict__):
            if attr.startswith('_perms_') or attr.startswith('_tenant_membership_'):
                delattr(user, attr)

    # ------------------------------------------------------------------ §3-B tenant scope

    def test_group_admin_cannot_set_foreign_tenant_on_create(self):
        """(a) A tenant-A group admin cannot create a group scoped to tenant B."""
        from users.forms import UserGroupForm
        self._flush(self.admin_a)
        data = {
            'name': 'Takeover', 'roles': [], 'members': [],
            'tenant': self.tenant_b.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.admin_a)
        self.assertFalse(form.is_valid())
        self.assertIn('tenant', form.errors)

    def test_group_admin_tenant_queryset_scoped(self):
        """The owning-tenant choice is scoped to tenants the actor manages (B excluded)."""
        from users.forms import UserGroupForm
        self._flush(self.admin_a)
        form = UserGroupForm(user=self.admin_a)
        tenant_pks = set(form.fields['tenant'].queryset.values_list('pk', flat=True))
        self.assertIn(self.tenant_a.pk, tenant_pks)
        self.assertNotIn(self.tenant_b.pk, tenant_pks)

    def test_group_admin_cannot_change_group_to_foreign_tenant(self):
        """(b) Cannot move an existing group onto a tenant they don't administer."""
        from users.forms import UserGroupForm
        group = UserGroup.objects.create(name="Existing", tenant=self.tenant_a)
        self._flush(self.admin_a)
        data = {
            'name': 'Existing', 'roles': [], 'members': [],
            'tenant': self.tenant_b.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, instance=group, user=self.admin_a)
        self.assertFalse(form.is_valid())
        self.assertIn('tenant', form.errors)

    def test_group_admin_same_tenant_is_allowed(self):
        """(e-tenant) A legitimate same-tenant group creation succeeds."""
        from users.forms import UserGroupForm
        self._flush(self.admin_a)
        data = {
            'name': 'Legit', 'roles': [], 'members': [],
            'tenant': self.tenant_a.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.admin_a)
        self.assertTrue(form.is_valid(), form.errors)

    def test_superuser_can_set_any_tenant(self):
        """(d-tenant) A superuser bypasses the tenant-ownership guard."""
        from users.forms import UserGroupForm
        data = {
            'name': 'SU Group', 'roles': [], 'members': [],
            'tenant': self.tenant_b.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.superuser)
        self.assertTrue(form.is_valid(), form.errors)

    # ------------------------------------------------------------------ §3-C member-assign

    def _group_with_foreign_role(self):
        """A group carrying a tenant-Administrator role the tenant-A admin does NOT hold."""
        tenant = Tenant.objects.create(name="Cust", slug="cust")
        admin_role = Role.objects.create(
            tenant=tenant, name="Administrator",
            permissions=["assets.delete_asset", "assets.change_asset"],
        )
        group = UserGroup.objects.create(name="Priv Team", tenant=tenant)
        group.roles.add(admin_role)
        return group

    def test_assign_view_blocks_grant_of_unheld_role_perms(self):
        """(c) The assign view blocks adding a member to a group whose roles' perms the
        actor lacks — no member is added and the actor stays on the form."""
        group = self._group_with_foreign_role()
        target = User.objects.create_user(
            username="victim", email="victim@example.com", password="pw",
        )
        Membership.objects.create(user=target, tenant=group.tenant)
        self.client.force_login(self.admin_a)
        url = reverse('users:usergroup_assign_users', kwargs={'pk': group.pk})
        resp = self.client.post(url, {'users': [target.pk]})
        # Re-rendered form (200), no redirect to detail, member NOT added.
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(group.members.filter(pk=target.pk).exists())

    def test_assign_view_superuser_bypasses(self):
        """(d) A superuser may add members regardless of the group's roles."""
        group = self._group_with_foreign_role()
        target = User.objects.create_user(
            username="ok_user", email="ok_user@example.com", password="pw",
        )
        Membership.objects.create(user=target, tenant=group.tenant)
        self.client.force_login(self.superuser)
        url = reverse('users:usergroup_assign_users', kwargs={'pk': group.pk})
        resp = self.client.post(url, {'users': [target.pk]})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(group.members.filter(pk=target.pk).exists())

    def test_assign_view_allows_grant_of_held_role_perms(self):
        """(e) A same-tenant role whose perms the actor DOES hold is grantable."""
        # Grant the tenant-A admin an extra own-reach role holding only
        # assets.view_asset at tenant A, then a group carrying a tenant-A role
        # requiring only that held perm.
        held_role = Role.objects.create(
            tenant=self.tenant_a, name="A View",
            permissions=["assets.view_asset"],
        )
        grant(self.admin_a, self.tenant_a, held_role)
        group = UserGroup.objects.create(name="A Viewers", tenant=self.tenant_a)
        group.roles.add(
            Role.objects.create(
                tenant=self.tenant_a, name="A Viewers Role",
                permissions=["assets.view_asset"],
            )
        )
        target = User.objects.create_user(
            username="grantee", email="grantee@example.com", password="pw",
        )
        Membership.objects.create(user=target, tenant=self.tenant_a)
        self.client.force_login(self.admin_a)
        url = reverse('users:usergroup_assign_users', kwargs={'pk': group.pk})
        resp = self.client.post(url, {'users': [target.pk]})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(group.members.filter(pk=target.pk).exists())

    def test_form_members_field_escalation_guard(self):
        """The form path is covered too: adding members to a group carrying roles the actor
        cannot grant errors on the ``members`` field (not just the assign view)."""
        from users.forms import UserGroupForm
        group = self._group_with_foreign_role()
        target = User.objects.create_user(
            username="form_victim", email="form_victim@example.com", password="pw",
        )
        # Keep the group's existing (foreign) roles; add a member via the form.
        role_pks = list(group.roles.values_list('pk', flat=True))
        self._flush(self.admin_a)
        data = {
            'name': group.name, 'roles': role_pks, 'members': [target.pk],
            'is_active': True,
        }
        form = UserGroupForm(data=data, instance=group, user=self.admin_a)
        self.assertFalse(form.is_valid())
        # Failure is attributed to members (or roles); the message mentions escalation.
        errs = ' '.join(e for el in form.errors.values() for e in el).lower()
        self.assertIn("escalation", errs)
