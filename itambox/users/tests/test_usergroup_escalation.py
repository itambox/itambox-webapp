"""Regression tests for the UserGroup grant-path escalation guards (RBAC review §3-B/§3-C).

Two write paths could grant cross-container access without checking whether the acting
group-admin actually held the permissions being conferred:

  * §3-B — ``UserGroupForm.provider``: a provider-A group admin could point a group at
    provider B (a provider they do not administer), handing B's SCIM-synced staff every
    role the group already carried (cross-provider takeover). The ``provider`` value was
    never scoped or validated against the actor's ``manage_groups`` on that provider.
  * §3-C — ``UserGroupAssignUsersView``: adding a member is itself a grant (the member
    inherits every role the group carries, plus each role's tenant access), but the view
    added members with no escalation check — gated only by the weak "manage_groups on ANY
    one provider" capability.

These tests assert both paths now reject a low-privilege actor and still allow superusers
and legitimate same-provider grants.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.managers import set_current_tenant, set_current_membership
from core.tests.mixins import TenantTestMixin
from organization.models import Tenant, Provider, Membership, Role
from users.models import UserGroup

User = get_user_model()


class UserGroupEscalationTests(TenantTestMixin, TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)

        # Two providers. The acting group-admin manages only provider A.
        self.provider_a = Provider.objects.create(name="MSP A", slug="msp-a")
        self.provider_b = Provider.objects.create(name="MSP B", slug="msp-b")

        # Provider-A group admin: a provider-scoped role granting manage_groups on A.
        self.admin_a = User.objects.create_user(
            username="admin_a", email="admin_a@example.com", password="pw",
        )
        role_a = Role.objects.create(
            provider=self.provider_a, name="A Group Admin",
            permissions=["organization.manage_groups"],
        )
        self.staff_a = Membership.objects.create(
            user=self.admin_a, provider=self.provider_a, is_active=True,
        )
        self.staff_a.roles.add(role_a)

        self.superuser = User.objects.create_superuser(
            username="root", email="root@example.com", password="pw",
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _flush(self, user):
        for attr in list(user.__dict__):
            if (attr.startswith('_perms_') or attr.startswith('_tenant_membership_')
                    or attr in ('_global_caps_cache', '_is_provider_staff_cache')):
                delattr(user, attr)

    # ------------------------------------------------------------------ §3-B provider

    def test_provider_admin_cannot_set_foreign_provider_on_create(self):
        """(a) A provider-A group admin cannot create a group scoped to provider B."""
        from users.forms import UserGroupForm
        self._flush(self.admin_a)
        data = {
            'name': 'Takeover', 'roles': [], 'members': [],
            'provider': self.provider_b.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.admin_a)
        self.assertFalse(form.is_valid())
        self.assertIn('provider', form.errors)

    def test_provider_admin_provider_queryset_scoped(self):
        """The provider choice is scoped to providers the actor manages (B excluded)."""
        from users.forms import UserGroupForm
        self._flush(self.admin_a)
        form = UserGroupForm(user=self.admin_a)
        provider_pks = set(form.fields['provider'].queryset.values_list('pk', flat=True))
        self.assertIn(self.provider_a.pk, provider_pks)
        self.assertNotIn(self.provider_b.pk, provider_pks)

    def test_provider_admin_cannot_change_group_to_foreign_provider(self):
        """(b) Cannot move an existing group onto a provider they don't manage."""
        from users.forms import UserGroupForm
        group = UserGroup.objects.create(name="Existing", provider=self.provider_a)
        self._flush(self.admin_a)
        data = {
            'name': 'Existing', 'roles': [], 'members': [],
            'provider': self.provider_b.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, instance=group, user=self.admin_a)
        self.assertFalse(form.is_valid())
        self.assertIn('provider', form.errors)

    def test_provider_admin_same_provider_is_allowed(self):
        """(e-provider) A legitimate same-provider group creation succeeds."""
        from users.forms import UserGroupForm
        self._flush(self.admin_a)
        data = {
            'name': 'Legit', 'roles': [], 'members': [],
            'provider': self.provider_a.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.admin_a)
        self.assertTrue(form.is_valid(), form.errors)

    def test_superuser_can_set_any_provider(self):
        """(d-provider) A superuser bypasses the provider ownership guard."""
        from users.forms import UserGroupForm
        data = {
            'name': 'SU Group', 'roles': [], 'members': [],
            'provider': self.provider_b.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.superuser)
        self.assertTrue(form.is_valid(), form.errors)

    # ------------------------------------------------------------------ §3-C member-assign

    def _group_with_foreign_role(self):
        """A group carrying a tenant-Administrator role the provider-A admin does NOT hold."""
        tenant = Tenant.objects.create(name="Cust", slug="cust")
        admin_role = Role.objects.create(
            tenant=tenant, name="Administrator",
            permissions=["assets.delete_asset", "assets.change_asset"],
        )
        group = UserGroup.objects.create(name="Priv Team")
        group.roles.add(admin_role)
        return group

    def test_assign_view_blocks_grant_of_unheld_role_perms(self):
        """(c) The assign view blocks adding a member to a group whose roles' perms the
        actor lacks — no member is added and the actor stays on the form."""
        group = self._group_with_foreign_role()
        target = User.objects.create_user(
            username="victim", email="victim@example.com", password="pw",
        )
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
        self.client.force_login(self.superuser)
        url = reverse('users:usergroup_assign_users', kwargs={'pk': group.pk})
        resp = self.client.post(url, {'users': [target.pk]})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(group.members.filter(pk=target.pk).exists())

    def test_assign_view_allows_grant_of_held_role_perms(self):
        """(e) A same-container role whose perms the actor DOES hold is grantable."""
        # Grant the provider-A admin manage_groups + a specific asset perm on provider A,
        # then a group carrying a provider-A role requiring only that held perm.
        held_role = Role.objects.create(
            provider=self.provider_a, name="A View",
            permissions=["assets.view_asset"],
        )
        self.staff_a.roles.add(held_role)
        group = UserGroup.objects.create(name="A Viewers")
        group.roles.add(
            Role.objects.create(
                provider=self.provider_a, name="A Viewers Role",
                permissions=["assets.view_asset"],
            )
        )
        target = User.objects.create_user(
            username="grantee", email="grantee@example.com", password="pw",
        )
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
