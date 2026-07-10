"""Cohesive per-surface privilege-escalation test suite (RBAC_STABILIZATION_REVIEW.md P1 #8).

Thesis under test (review §5): the escalation guard (``core.auth.guards.validate_permission_grant``
/ ``validate_group_membership_grant``) is enforced *per call-site*, not at the model layer, so
EVERY role/permission/membership/group-membership WRITE PATH must independently call it. This
module asserts, for each known write path, that:

  * a non-superuser actor who lacks permission P cannot grant a role/group/provider that would
    confer P to someone else (the guard fires: form invalid / ``ValidationError`` /
    ``PermissionDenied`` / no DB mutation), and
  * an actor who already holds those permissions (or a superuser) CAN perform the same grant.

Surfaces covered (one test class per surface):
  1. ``MembershipForm`` (organization/forms/membership_form.py)
  2. ``MembershipBulkRoleForm`` / ``MembershipBulkEditView`` (organization/views/membership_views.py)
  3. ``RoleAssignUsersView`` (organization/views/role_views.py)
  4. ``UserGroupForm`` (users/forms.py) — role grant + provider ownership
  5. ``UserGroupAssignUsersView`` (users/views.py)
  6. ``TechnicianQuickForm`` (organization/forms/provider_form.py)

(The former surface #4, the tenant-invitation flow, was deleted wholesale on 2026-07-10 —
users are provisioned by admins, SCIM, or SSO JIT; there is no invite write path anymore.)

Each write path already has its own dedicated regression test module (test_usergroup_escalation.py,
test_role_assignment.py, etc.) — this module is the SINGLE cohesive
sweep across all of them so a reviewer can see the whole escalation-guard surface at a glance, and
so a newly-added write path with no guard shows up here as a gap rather than being missed entirely.
"""
import unittest

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from core.managers import set_current_tenant, set_current_membership
from core.tests.mixins import TenantTestMixin
from organization.models import (
    Membership, Role, Tenant, Provider,
)
from organization.forms import (
    MembershipForm, MembershipBulkRoleForm,
)
from organization.forms.provider_form import TechnicianQuickForm
from users.models import UserGroup
from users.forms import UserGroupForm

User = get_user_model()

# A permission no seed/fixture role grants by default, used as the "narrow" permission a
# low-privilege actor holds, plus a disjoint "broad" set representing an over-privileged role.
NARROW_PERM = 'assets.view_asset'
BROAD_PERMS = ['assets.delete_asset', 'organization.delete_tenant']


def _flush_perm_cache(user):
    """Clear any per-request permission caches ``MembershipBackend`` memoizes on the user
    instance, so a freshly-added role/membership is picked up within the same test."""
    for attr in list(user.__dict__):
        if (attr.startswith('_perms_') or attr.startswith('_tenant_membership_')
                or attr in ('_global_caps_cache', '_is_provider_staff_cache')):
            delattr(user, attr)


class EscalationSurfaceTestCase(TenantTestMixin, TestCase):
    """Shared fixture: one tenant, a narrow-permission actor, a full-permission actor,
    and a superuser — reused (with surface-specific extras) by every surface below."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)

        self.tenant = Tenant.objects.create(name="Acme", slug="acme-escalation")

        # Low-privilege actor: holds ONLY NARROW_PERM in the tenant.
        self.narrow_role = Role.objects.create(
            tenant=self.tenant, name="Narrow", permissions=[NARROW_PERM],
        )
        self.low_priv = User.objects.create_user(
            username='low_priv', email='low_priv@acme.test', password='pw',
        )
        self.low_priv_membership = Membership.objects.create(
            user=self.low_priv, tenant=self.tenant, is_active=True,
        )
        self.low_priv_membership.roles.add(self.narrow_role)

        # High-privilege actor: holds NARROW_PERM + BROAD_PERMS in the tenant.
        self.broad_role = Role.objects.create(
            tenant=self.tenant, name="Broad Admin", permissions=[NARROW_PERM] + BROAD_PERMS,
        )
        self.high_priv = User.objects.create_user(
            username='high_priv', email='high_priv@acme.test', password='pw',
        )
        self.high_priv_membership = Membership.objects.create(
            user=self.high_priv, tenant=self.tenant, is_active=True,
        )
        self.high_priv_membership.roles.add(self.broad_role)

        # The over-privileged role that a legitimate grant should carry, and that the
        # low-privilege actor must NOT be able to hand out.
        self.overpriv_role = Role.objects.create(
            tenant=self.tenant, name="Over-Privileged", permissions=list(BROAD_PERMS),
        )

        self.superuser = User.objects.create_superuser(
            username='root', email='root@acme.test', password='pw',
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def _login(self, user, tenant=None):
        self.client.force_login(user)
        session = self.client.session
        session['active_tenant_id'] = (tenant or self.tenant).pk
        session.save()


# --------------------------------------------------------------------------------------------- #
# 1. MembershipForm
# --------------------------------------------------------------------------------------------- #
class MembershipFormEscalationTests(EscalationSurfaceTestCase):
    """Surface #1: attaching a role whose permissions exceed the actor's is rejected by
    ``MembershipForm.clean()`` (organization/forms/membership_form.py)."""

    def setUp(self):
        super().setUp()
        self.victim = User.objects.create_user(
            username='victim', email='victim@acme.test', password='pw',
        )

    def test_low_privilege_actor_cannot_grant_overprivileged_role_via_membership_form(self):
        """A narrow-permission actor cannot create a Membership carrying a role whose
        permissions they do not themselves hold."""
        _flush_perm_cache(self.low_priv)
        form = MembershipForm(
            data={
                'user': self.victim.pk,
                'tenant': self.tenant.pk,
                'roles': [self.overpriv_role.pk],
                'is_active': True,
            },
            user=self.low_priv,
            tenant=self.tenant,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)
        self.assertTrue(
            any('escalation' in e.lower() for e in form.errors['__all__']), form.errors,
        )
        self.assertFalse(Membership.objects.filter(user=self.victim, tenant=self.tenant).exists())

    def test_high_privilege_actor_can_grant_role_within_their_permissions(self):
        """An actor holding every permission the role carries CAN create the membership."""
        _flush_perm_cache(self.high_priv)
        form = MembershipForm(
            data={
                'user': self.victim.pk,
                'tenant': self.tenant.pk,
                'roles': [self.overpriv_role.pk],
                'is_active': True,
            },
            user=self.high_priv,
            tenant=self.tenant,
        )
        self.assertTrue(form.is_valid(), form.errors)
        membership = form.save()
        self.assertIn(self.overpriv_role, membership.roles.all())

    def test_superuser_bypasses_membership_form_guard(self):
        """A superuser may grant any role via ``MembershipForm`` (guard is a no-op)."""
        form = MembershipForm(
            data={
                'user': self.victim.pk,
                'tenant': self.tenant.pk,
                'roles': [self.overpriv_role.pk],
                'is_active': True,
            },
            user=self.superuser,
            tenant=self.tenant,
        )
        self.assertTrue(form.is_valid(), form.errors)


# --------------------------------------------------------------------------------------------- #
# 2. MembershipBulkRoleForm / MembershipBulkEditView
# --------------------------------------------------------------------------------------------- #
class MembershipBulkEditEscalationTests(EscalationSurfaceTestCase):
    """Surface #2: bulk role-add via ``MembershipBulkEditView`` is guarded (the view calls
    ``validate_permission_grant`` on the union of ``roles_to_add`` before mutating)."""

    def setUp(self):
        super().setUp()
        self.target_user = User.objects.create_user(
            username='bulk_target', email='bulk_target@acme.test', password='pw',
        )
        self.target_membership = Membership.objects.create(
            user=self.target_user, tenant=self.tenant, is_active=True,
        )

    def _url(self):
        return reverse('organization:membership_bulk_edit')

    def test_low_privilege_actor_cannot_bulk_grant_overprivileged_role(self):
        """A narrow-permission actor's bulk role-add of an over-privileged role is rejected;
        no role is attached to the target membership."""
        self._login(self.low_priv)
        resp = self.client.post(self._url(), {
            'pk': [self.target_membership.pk],
            '_apply': '1',
            'roles_to_add': [self.overpriv_role.pk],
            'return_url': reverse('organization:membership_list'),
        })
        # Guarded by change_membership perm check first (low_priv lacks it too) OR the
        # escalation guard — either way the mutation must not happen.
        self.assertIn(resp.status_code, (302, 403))
        self.target_membership.refresh_from_db()
        self.assertNotIn(self.overpriv_role, self.target_membership.roles.all())

    def test_actor_with_change_membership_and_narrow_perms_still_blocked_from_broad_grant(self):
        """Even an actor who can administer memberships in the tenant (has
        ``change_membership``) but lacks the broad role's permissions is blocked by the
        escalation guard specifically (isolates the guard from the coarser perm check)."""
        manager_role = Role.objects.create(
            tenant=self.tenant, name="Membership Manager",
            # Also grant view_membership so the post-redirect landing page (the
            # membership list) doesn't itself 403 when the test follows the redirect —
            # that would be a red herring unrelated to the escalation guard under test.
            permissions=['organization.change_membership', 'organization.view_membership', NARROW_PERM],
        )
        manager = User.objects.create_user(
            username='mem_manager', email='mem_manager@acme.test', password='pw',
        )
        manager_membership = Membership.objects.create(
            user=manager, tenant=self.tenant, is_active=True,
        )
        manager_membership.roles.add(manager_role)
        _flush_perm_cache(manager)

        self._login(manager)
        resp = self.client.post(self._url(), {
            'pk': [self.target_membership.pk],
            '_apply': '1',
            'roles_to_add': [self.overpriv_role.pk],
            'return_url': reverse('organization:membership_list'),
        }, follow=True)
        self.target_membership.refresh_from_db()
        self.assertNotIn(self.overpriv_role, self.target_membership.roles.all())
        content = resp.content.decode().lower()
        self.assertIn('privilege escalation', content)

    def test_high_privilege_actor_can_bulk_grant_role_within_their_permissions(self):
        """An actor holding the role's permissions AND ``change_membership`` succeeds."""
        admin_role = Role.objects.create(
            tenant=self.tenant, name="Full Admin",
            permissions=['organization.change_membership', NARROW_PERM] + BROAD_PERMS,
        )
        admin = User.objects.create_user(
            username='bulk_admin', email='bulk_admin@acme.test', password='pw',
        )
        admin_membership = Membership.objects.create(
            user=admin, tenant=self.tenant, is_active=True,
        )
        admin_membership.roles.add(admin_role)
        _flush_perm_cache(admin)

        self._login(admin)
        resp = self.client.post(self._url(), {
            'pk': [self.target_membership.pk],
            '_apply': '1',
            'roles_to_add': [self.overpriv_role.pk],
            'return_url': reverse('organization:membership_list'),
        })
        self.assertEqual(resp.status_code, 302)
        self.target_membership.refresh_from_db()
        self.assertIn(self.overpriv_role, self.target_membership.roles.all())

    def test_superuser_can_bulk_grant_any_role(self):
        """A superuser bypasses the guard entirely."""
        self._login(self.superuser)
        resp = self.client.post(self._url(), {
            'pk': [self.target_membership.pk],
            '_apply': '1',
            'roles_to_add': [self.overpriv_role.pk],
            'return_url': reverse('organization:membership_list'),
        })
        self.assertEqual(resp.status_code, 302)
        self.target_membership.refresh_from_db()
        self.assertIn(self.overpriv_role, self.target_membership.roles.all())

    def test_bulk_role_form_itself_has_no_field_level_guard(self):
        """Documents that ``MembershipBulkRoleForm`` is a plain field-selection form — the
        escalation guard lives in the VIEW (``MembershipBulkEditView.post``), not the form's
        own ``clean()``. Guards against a future refactor silently dropping the view-level
        check under the assumption the form already covers it."""
        form = MembershipBulkRoleForm(data={'roles_to_add': [self.overpriv_role.pk]})
        # The bare form validates fine — it is the view, not the form, that must guard.
        self.assertTrue(form.is_valid(), form.errors)


# --------------------------------------------------------------------------------------------- #
# 3. RoleAssignUsersView (organization)
# --------------------------------------------------------------------------------------------- #
class RoleAssignUsersViewEscalationTests(EscalationSurfaceTestCase):
    """Surface #3: assigning users to an over-privileged Role via ``RoleAssignUsersView``
    is blocked for an actor who does not hold that role's permissions."""

    def setUp(self):
        super().setUp()
        self.target_user = User.objects.create_user(
            username='assign_target', email='assign_target@acme.test', password='pw',
        )
        # Grant the actors the coarse add/change_membership perms too, so the escalation
        # guard (not the coarse permission check) is what's isolated/exercised.
        self.narrow_role.permissions = [NARROW_PERM, 'organization.add_membership', 'organization.change_membership']
        self.narrow_role.save()
        self.broad_role.permissions = [NARROW_PERM, 'organization.add_membership', 'organization.change_membership'] + BROAD_PERMS
        self.broad_role.save()

    def _url(self, role=None):
        return reverse('organization:role_assign_users', kwargs={'pk': (role or self.overpriv_role).pk})

    def test_low_privilege_actor_cannot_assign_users_to_overprivileged_role(self):
        """A user with add/change_membership perms but who does not hold the target role's
        permissions is denied (403) and no membership/role grant occurs."""
        _flush_perm_cache(self.low_priv)
        self._login(self.low_priv)
        resp = self.client.post(self._url(), {'users': [self.target_user.pk]})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(
            Membership.objects.filter(user=self.target_user, tenant=self.tenant).exists()
        )

    def test_low_privilege_actor_denied_on_get_too(self):
        """The GET (confirmation form) path is guarded identically to POST."""
        _flush_perm_cache(self.low_priv)
        self._login(self.low_priv)
        resp = self.client.get(self._url())
        self.assertEqual(resp.status_code, 403)

    def test_high_privilege_actor_can_assign_users_to_role_within_their_permissions(self):
        """An actor holding the role's permissions succeeds in assigning it."""
        _flush_perm_cache(self.high_priv)
        self._login(self.high_priv)
        resp = self.client.post(self._url(), {'users': [self.target_user.pk]})
        self.assertIn(resp.status_code, (200, 302))
        mem = Membership.objects.filter(user=self.target_user, tenant=self.tenant).first()
        self.assertIsNotNone(mem)
        self.assertIn(self.overpriv_role, mem.roles.all())

    def test_superuser_can_assign_users_to_any_role(self):
        self._login(self.superuser)
        resp = self.client.post(self._url(), {'users': [self.target_user.pk]})
        self.assertIn(resp.status_code, (200, 302))
        mem = Membership.objects.filter(user=self.target_user, tenant=self.tenant).first()
        self.assertIsNotNone(mem)
        self.assertIn(self.overpriv_role, mem.roles.all())


# --------------------------------------------------------------------------------------------- #
# 4. UserGroupForm
# --------------------------------------------------------------------------------------------- #
class UserGroupFormEscalationTests(EscalationSurfaceTestCase):
    """Surface #5: ``UserGroupForm`` rejects (a) attaching an over-privileged role, and
    (b) setting a provider the actor doesn't manage."""

    def setUp(self):
        super().setUp()
        self.provider_a = Provider.objects.create(name="MSP A", slug="msp-a-escalation")
        self.provider_b = Provider.objects.create(name="MSP B", slug="msp-b-escalation")

        # Provider-A group admin: manage_groups on provider A only, no broad perms.
        self.group_admin_role = Role.objects.create(
            provider=self.provider_a, name="A Group Admin",
            permissions=['organization.manage_groups', NARROW_PERM],
        )
        self.group_admin = User.objects.create_user(
            username='group_admin', email='group_admin@acme.test', password='pw',
        )
        self.group_admin_staff = Membership.objects.create(
            user=self.group_admin, provider=self.provider_a, is_active=True,
        )
        self.group_admin_staff.roles.add(self.group_admin_role)

        self.target_user = User.objects.create_user(
            username='ug_target', email='ug_target@acme.test', password='pw',
        )

    def test_low_privilege_group_admin_cannot_attach_overprivileged_tenant_role(self):
        """(a) A group admin cannot attach a role whose tenant-scoped permissions they do
        not hold — even though they may manage groups globally."""
        _flush_perm_cache(self.group_admin)
        data = {
            'name': 'Takeover Group', 'roles': [self.overpriv_role.pk], 'members': [],
            'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.group_admin)
        self.assertFalse(form.is_valid())
        self.assertFalse(UserGroup.objects.filter(name='Takeover Group').exists())

    def test_low_privilege_group_admin_cannot_set_unmanaged_provider(self):
        """(b) A provider-A group admin cannot scope a new group to provider B, which they
        do not manage."""
        _flush_perm_cache(self.group_admin)
        data = {
            'name': 'Cross Provider Group', 'roles': [], 'members': [],
            'provider': self.provider_b.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.group_admin)
        self.assertFalse(form.is_valid())
        self.assertIn('provider', form.errors)

    def test_high_privilege_group_admin_can_attach_role_within_permissions(self):
        """Positive (a): a group admin who also holds the role's permissions succeeds.

        The role being attached is tenant-scoped, so the group admin needs an actual
        tenant Membership carrying that permission (a provider-scoped role cannot be
        attached to a provider Membership to grant tenant perms — the container kinds
        don't mix)."""
        narrow_group_role = Role.objects.create(
            tenant=self.tenant, name="Narrow Group Role", permissions=[NARROW_PERM],
        )
        tenant_membership = Membership.objects.create(
            user=self.group_admin, tenant=self.tenant, is_active=True,
        )
        tenant_membership.roles.add(self.narrow_role)
        _flush_perm_cache(self.group_admin)
        data = {
            'name': 'Legit Group', 'roles': [narrow_group_role.pk], 'members': [],
            'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.group_admin)
        self.assertTrue(form.is_valid(), form.errors)

    def test_high_privilege_group_admin_can_set_own_provider(self):
        """Positive (b): setting the provider the actor actually manages succeeds."""
        _flush_perm_cache(self.group_admin)
        data = {
            'name': 'Own Provider Group', 'roles': [], 'members': [],
            'provider': self.provider_a.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.group_admin)
        self.assertTrue(form.is_valid(), form.errors)

    def test_superuser_bypasses_both_usergroup_form_guards(self):
        data = {
            'name': 'SU Group', 'roles': [self.overpriv_role.pk], 'members': [],
            'provider': self.provider_b.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.superuser)
        self.assertTrue(form.is_valid(), form.errors)


# --------------------------------------------------------------------------------------------- #
# 5. UserGroupAssignUsersView (users)
# --------------------------------------------------------------------------------------------- #
class UserGroupAssignUsersViewEscalationTests(EscalationSurfaceTestCase):
    """Surface #6: adding a member to a role-carrying UserGroup the actor can't fully grant
    is blocked by ``validate_group_membership_grant`` inside the assign view.

    NOTE: the negative case (blocked-grant) is currently ``skip``ped — see its skip reason —
    because this sweep found the guard has a live tenant-scoping gap for actors with no
    active-tenant context (typical of pure provider-staff admins). The positive cases
    (superuser bypass, and a grant the actor genuinely holds) still pass and are exercised
    below."""

    def setUp(self):
        super().setUp()
        self.provider_a = Provider.objects.create(name="MSP A2", slug="msp-a2-escalation")

        self.group_admin_role = Role.objects.create(
            provider=self.provider_a, name="A2 Group Admin",
            permissions=['organization.manage_groups'],
        )
        self.group_admin = User.objects.create_user(
            username='ug_assign_admin', email='ug_assign_admin@acme.test', password='pw',
        )
        self.group_admin_staff = Membership.objects.create(
            user=self.group_admin, provider=self.provider_a, is_active=True,
        )
        self.group_admin_staff.roles.add(self.group_admin_role)

        # A group carrying the over-privileged tenant role — the admin above cannot
        # grant this role's permissions, so adding a member must be blocked.
        self.overpriv_group = UserGroup.objects.create(name="Overpriv Group")
        self.overpriv_group.roles.add(self.overpriv_role)

        self.target_user = User.objects.create_user(
            username='ug_assign_target', email='ug_assign_target@acme.test', password='pw',
        )

    def _url(self, group=None):
        return reverse('users:usergroup_assign_users', kwargs={'pk': (group or self.overpriv_group).pk})

    def test_low_privilege_group_admin_cannot_add_member_to_overprivileged_group(self):
        """A group admin (global capability only) cannot add a member to a group carrying
        a role whose permissions they don't hold — no member is added.

        Regression for the tenant-scoping gap the #8 sweep found: a pure provider-staff group
        admin (Provider Membership, no Tenant Membership, so no active_tenant is resolved) must
        still be blocked. core/auth/guards.validate_group_membership_grant now reads the group's
        roles via Role._base_manager (tenant-context-independent) rather than the scoped default
        manager, so the guard sees the carried role and rejects the grant."""
        _flush_perm_cache(self.group_admin)
        self.client.force_login(self.group_admin)
        resp = self.client.post(self._url(), {'users': [self.target_user.pk]})
        self.assertEqual(resp.status_code, 200)  # re-rendered form, not redirected
        self.assertFalse(self.overpriv_group.members.filter(pk=self.target_user.pk).exists())

    def test_high_privilege_group_admin_can_add_member_to_group_within_permissions(self):
        """Positive: a group admin who holds the carried role's permissions succeeds."""
        narrow_group = UserGroup.objects.create(name="Narrow Group")
        narrow_tenant_role = Role.objects.create(
            provider=self.provider_a, name="A2 Narrow", permissions=[NARROW_PERM],
        )
        narrow_group.roles.add(narrow_tenant_role)
        self.group_admin_staff.roles.add(narrow_tenant_role)
        _flush_perm_cache(self.group_admin)

        self.client.force_login(self.group_admin)
        resp = self.client.post(
            reverse('users:usergroup_assign_users', kwargs={'pk': narrow_group.pk}),
            {'users': [self.target_user.pk]},
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(narrow_group.members.filter(pk=self.target_user.pk).exists())

    def test_superuser_can_add_member_to_any_group(self):
        self.client.force_login(self.superuser)
        resp = self.client.post(self._url(), {'users': [self.target_user.pk]})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(self.overpriv_group.members.filter(pk=self.target_user.pk).exists())


# --------------------------------------------------------------------------------------------- #
# 6. TechnicianQuickForm
# --------------------------------------------------------------------------------------------- #
class TechnicianQuickFormEscalationTests(EscalationSurfaceTestCase):
    """Surface #7: onboarding a technician with an over-privileged PROVIDER role is
    blocked by ``TechnicianQuickForm.clean()``."""

    def setUp(self):
        super().setUp()
        self.provider = Provider.objects.create(name="MSP Onboard", slug="msp-onboard-escalation")

        # Low-priv onboarder: manage_staff on the provider, but no other provider perms.
        self.onboarder_role = Role.objects.create(
            provider=self.provider, name="Onboarder", permissions=['organization.manage_staff'],
        )
        self.onboarder = User.objects.create_user(
            username='onboarder', email='onboarder@acme.test', password='pw',
        )
        self.onboarder_staff = Membership.objects.create(
            user=self.onboarder, provider=self.provider, is_active=True,
        )
        self.onboarder_staff.roles.add(self.onboarder_role)

        # High-priv onboarder: manage_staff + the broad provider-scoped permissions.
        self.provider_broad_role = Role.objects.create(
            provider=self.provider, name="Provider Full Admin",
            permissions=['organization.manage_staff'] + BROAD_PERMS,
        )
        self.senior_onboarder = User.objects.create_user(
            username='senior_onboarder', email='senior_onboarder@acme.test', password='pw',
        )
        self.senior_staff = Membership.objects.create(
            user=self.senior_onboarder, provider=self.provider, is_active=True,
        )
        self.senior_staff.roles.add(self.provider_broad_role)

        # The over-privileged PROVIDER-scoped role being granted during onboarding.
        self.overpriv_provider_role = Role.objects.create(
            provider=self.provider, name="Overpriv Provider Role", permissions=list(BROAD_PERMS),
        )

    def _form_data(self, role):
        return {
            'email': 'newtech@acme.test',
            'first_name': 'New', 'last_name': 'Tech',
            'provider': self.provider.pk,
            'role': role.pk if role else '',
            'tenant_scope': Membership.SCOPE_ALL,
        }

    def test_low_privilege_onboarder_cannot_onboard_with_overprivileged_provider_role(self):
        """A user who can manage staff but does not hold the provider role's permissions
        cannot onboard a technician into that role."""
        _flush_perm_cache(self.onboarder)
        form = TechnicianQuickForm(data=self._form_data(self.overpriv_provider_role), user=self.onboarder)
        self.assertFalse(form.is_valid())
        self.assertIn('role', form.errors)
        self.assertFalse(User.objects.filter(email='newtech@acme.test').exists())

    def test_high_privilege_onboarder_can_onboard_with_role_within_their_permissions(self):
        """An onboarder holding the provider role's permissions can onboard the technician."""
        _flush_perm_cache(self.senior_onboarder)
        form = TechnicianQuickForm(
            data=self._form_data(self.overpriv_provider_role), user=self.senior_onboarder,
        )
        self.assertTrue(form.is_valid(), form.errors)
        user, membership = form.save()
        self.assertIn(self.overpriv_provider_role, membership.roles.all())

    def test_superuser_can_onboard_with_any_provider_role(self):
        form = TechnicianQuickForm(data=self._form_data(self.overpriv_provider_role), user=self.superuser)
        self.assertTrue(form.is_valid(), form.errors)
