"""Regression tests for the tenant-invitation privilege-escalation guard (§3-A).

Covers the fix for defect #1:
  * ``TenantInvitationForm.clean()`` must reject an inviter selecting a role that
    carries permissions the inviter does not themselves hold in the tenant.
  * ``InviteUserMixin.test_func`` must gate strictly on the real
    ``organization.add_tenantinvitation`` permission and NOT on a role literally
    named "admin".
"""
from django.test import TestCase
from django.contrib.auth import get_user_model

from core.tests.mixins import TenantTestMixin
from organization.models import Membership, Role
from organization.forms import TenantInvitationForm
from organization.views.invitation_views import InviteUserMixin

User = get_user_model()

INVITE_PERM = 'organization.add_tenantinvitation'


class _StubRequest:
    """Minimal stand-in for the attributes ``InviteUserMixin.test_func`` reads."""
    def __init__(self, user, active_tenant, active_membership):
        self.user = user
        self.active_tenant = active_tenant
        self.active_membership = active_membership


class TenantInvitationEscalationTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.clear_tenant_context()
        # Base tenant + a plain user with no membership yet.
        self.setup_tenant_context(name="Acme", slug="acme")

        # The tenant's full-power role (the escalation target).
        self.admin_role = Role.objects.create(
            tenant=self.tenant,
            name="Administrator",
            permissions=[INVITE_PERM, 'assets.view_asset', 'assets.delete_asset'],
        )

        # A low-privilege inviter: holds ONLY the invite permission, nothing else.
        self.inviter = User.objects.create_user(
            username='inviter', email='inviter@acme.test', password='password123',
        )
        self.inviter_role = Role.objects.create(
            tenant=self.tenant,
            name="Inviter Only",
            permissions=[INVITE_PERM],
        )
        self.inviter_membership = Membership.objects.create(
            user=self.inviter, tenant=self.tenant, is_active=True,
        )
        self.inviter_membership.roles.add(self.inviter_role)

        # A full admin: holds every permission the admin role carries.
        self.admin = User.objects.create_user(
            username='tenantadmin', email='tenantadmin@acme.test', password='password123',
        )
        self.admin_membership = Membership.objects.create(
            user=self.admin, tenant=self.tenant, is_active=True,
        )
        self.admin_membership.roles.add(self.admin_role)

    def tearDown(self):
        self.clear_tenant_context()

    # ---------------------------------------------------------------- form clean()
    def test_low_privilege_inviter_cannot_grant_higher_role(self):
        """(a) A user holding only add_tenantinvitation cannot invite into the
        Administrator role — the form is invalid with the escalation error."""
        self.set_active_tenant(self.tenant, self.inviter_membership)
        form = TenantInvitationForm(
            data={'email': 'victim@acme.test', 'role': self.admin_role.pk},
            tenant=self.tenant,
            requesting_user=self.inviter,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)
        self.assertTrue(
            any("Privilege escalation detected" in e for e in form.errors['__all__']),
            form.errors,
        )

    def test_inviter_can_grant_role_within_own_permissions(self):
        """The inviter CAN invite into a role whose permissions are a subset of theirs."""
        self.set_active_tenant(self.tenant, self.inviter_membership)
        subset_role = Role.objects.create(
            tenant=self.tenant, name="Inviter Clone", permissions=[INVITE_PERM],
        )
        form = TenantInvitationForm(
            data={'email': 'peer@acme.test', 'role': subset_role.pk},
            tenant=self.tenant,
            requesting_user=self.inviter,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_admin_can_grant_admin_role(self):
        """(b) An admin holding all the role's permissions CAN invite into it."""
        self.set_active_tenant(self.tenant, self.admin_membership)
        form = TenantInvitationForm(
            data={'email': 'newadmin@acme.test', 'role': self.admin_role.pk},
            tenant=self.tenant,
            requesting_user=self.admin,
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_superuser_bypasses_guard(self):
        """A superuser can grant any role (guard is a no-op for superusers)."""
        superuser = User.objects.create_superuser(
            username='root', email='root@acme.test', password='password123',
        )
        self.set_active_tenant(self.tenant, None)
        form = TenantInvitationForm(
            data={'email': 'anyone@acme.test', 'role': self.admin_role.pk},
            tenant=self.tenant,
            requesting_user=superuser,
        )
        self.assertTrue(form.is_valid(), form.errors)

    # ---------------------------------------------------------------- test_func gate
    def test_test_func_denies_admin_named_role_without_permission(self):
        """(c) A user whose only qualifying role is literally named 'admin' but that
        carries NO add_tenantinvitation permission must be DENIED — the old magic-string
        branch is gone."""
        user = User.objects.create_user(
            username='fakeadmin', email='fakeadmin@acme.test', password='password123',
        )
        name_only_role = Role.objects.create(
            tenant=self.tenant, name="admin", permissions=['assets.view_asset'],
        )
        membership = Membership.objects.create(
            user=user, tenant=self.tenant, is_active=True,
        )
        membership.roles.add(name_only_role)
        self.set_active_tenant(self.tenant, membership)

        mixin = InviteUserMixin()
        mixin.request = _StubRequest(user, self.tenant, membership)
        self.assertFalse(mixin.test_func())

    def test_test_func_allows_user_with_invite_permission(self):
        """A user actually holding add_tenantinvitation passes the gate."""
        self.set_active_tenant(self.tenant, self.inviter_membership)
        mixin = InviteUserMixin()
        mixin.request = _StubRequest(self.inviter, self.tenant, self.inviter_membership)
        self.assertTrue(mixin.test_func())

    def test_test_func_denies_without_active_tenant(self):
        """No active tenant => no invite access (fail closed)."""
        self.set_active_tenant(None, None)
        mixin = InviteUserMixin()
        mixin.request = _StubRequest(self.inviter, None, None)
        self.assertFalse(mixin.test_func())
