"""Cohesive per-surface privilege-escalation test suite (RBAC_STABILIZATION_REVIEW.md P1 #8).

Thesis under test (review §5): the escalation guard (``core.auth.guards.validate_permission_grant``
/ ``validate_assignment_grant`` / ``validate_group_membership_grant``) is enforced *per call-site*,
not at the model layer, so EVERY role/permission/membership/group-membership WRITE PATH must
independently call it. This module asserts, for each known write path, that:

  * a non-superuser actor who lacks permission P cannot grant a role/group/reach that would
    confer P (or broader reach) to someone else (the guard fires: form invalid / ``ValidationError``
    / ``PermissionDenied`` / no DB mutation), and
  * an actor who already holds those permissions/reach (or a superuser) CAN perform the same grant.

Surfaces covered (one test class per surface):
  1. ``MembershipForm`` (organization/forms/membership_form.py) — assignment authoring (own reach)
  2. ``MembershipBulkRoleForm`` / ``MembershipBulkEditView`` (organization/views/membership_views.py)
  3. ``RoleAssignUsersView`` (organization/views/role_views.py)
  4. ``UserGroupForm`` (users/forms.py) — role attach, owning-tenant scope, member grants
  5. ``UserGroupAssignUsersView`` (users/views.py)
  6. Unified ``MembershipForm`` — managed-reach inline-user onboarding
  7. Reach-grant escalation — ``validate_assignment_grant`` itself (core/auth/guards.py), the guard
     that stops an actor handing out MANAGED reach broader than their own, independent of whether
     they hold the role's permission content.

(The former surface, the tenant-invitation flow, was deleted wholesale on 2026-07-10 — users are
provisioned by admins, SCIM, or SSO JIT; there is no invite write path anymore.)

Post RBAC structural collapse (RBAC_STAGE2_SPEC.md): ``organization.Provider`` is deleted — a
"provider"/MSP tenant is now ``Tenant(is_provider=True)`` pointed at via ``managed_by``; grants are
per-``RoleAssignment`` rows (``reach='own'`` or ``reach='managed'``) hung off a ``Membership``,
created here via ``core.tests.mixins.grant`` (re-exported as ``self.grant`` on ``TenantTestMixin``).
There is no capability vocabulary and nothing is stripped in the managed projection — role content
decides what a grant conveys, and ``validate_assignment_grant`` decides who may create it.

Each write path already has its own dedicated regression test module (test_usergroup_escalation.py,
test_role_assignment.py, etc.) — this module is the SINGLE cohesive sweep across all of them so a
reviewer can see the whole escalation-guard surface at a glance, and so a newly-added write path
with no guard shows up here as a gap rather than being missed entirely.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from core.auth.guards import validate_assignment_grant
from core.managers import set_current_tenant, set_current_membership
from core.tests.mixins import TenantTestMixin
from organization.models import Membership, Role, RoleAssignment, Tenant
from organization.forms import MembershipForm, MembershipBulkRoleForm
from users.models import UserGroup
from users.forms import UserGroupForm

User = get_user_model()

# A permission no seed/fixture role grants by default, used as the "narrow" permission a
# low-privilege actor holds, plus a disjoint "broad" set representing an over-privileged role.
NARROW_PERM = 'assets.view_asset'
BROAD_PERMS = ['assets.delete_asset', 'organization.delete_tenant']


def _flush_perm_cache(user):
    """Clear the per-request permission caches ``MembershipBackend`` memoizes on the user
    instance, so a freshly-added assignment/role is picked up within the same test."""
    for attr in list(user.__dict__):
        if attr.startswith('_perms_') or attr.startswith('_tenant_membership_'):
            delattr(user, attr)


class EscalationSurfaceTestCase(TenantTestMixin, TestCase):
    """Shared fixture: one tenant, a narrow-permission actor, a full-permission actor,
    and a superuser — reused (with surface-specific extras) by every surface below."""

    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)

        self.tenant = Tenant.objects.create(name="Acme", slug="acme-escalation")

        # Low-privilege actor: holds ONLY NARROW_PERM in the tenant (own reach).
        self.narrow_role = Role.objects.create(
            tenant=self.tenant, name="Narrow", permissions=[NARROW_PERM],
        )
        self.low_priv = User.objects.create_user(
            username='low_priv', email='low_priv@acme.test', password='pw',
        )
        self.low_priv_assignment = self.grant(self.low_priv, self.tenant, self.narrow_role)
        self.low_priv_membership = self.low_priv_assignment.membership

        # High-privilege actor: holds NARROW_PERM + BROAD_PERMS in the tenant.
        self.broad_role = Role.objects.create(
            tenant=self.tenant, name="Broad Admin", permissions=[NARROW_PERM] + BROAD_PERMS,
        )
        self.high_priv = User.objects.create_user(
            username='high_priv', email='high_priv@acme.test', password='pw',
        )
        self.high_priv_assignment = self.grant(self.high_priv, self.tenant, self.broad_role)
        self.high_priv_membership = self.high_priv_assignment.membership

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
    ``MembershipForm.clean()`` (organization/forms/membership_form.py) via
    ``validate_assignment_grant`` for each selected (own-reach) role."""

    def setUp(self):
        super().setUp()
        self.victim = User.objects.create_user(
            username='victim', email='victim@acme.test', password='pw',
        )

    def _data(self, role):
        return {
            'user': self.victim.pk,
            'tenant': self.tenant.pk,
            'roles': [role.pk],
            'reach': RoleAssignment.REACH_OWN,
            'is_active': True,
        }

    def test_low_privilege_actor_cannot_grant_overprivileged_role_via_membership_form(self):
        """A narrow-permission actor cannot create a Membership carrying a role whose
        permissions they do not themselves hold."""
        _flush_perm_cache(self.low_priv)
        form = MembershipForm(
            data=self._data(self.overpriv_role), user=self.low_priv, tenant=self.tenant,
        )
        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)
        self.assertTrue(
            any('escalation' in e.lower() for e in form.errors['__all__']), form.errors,
        )
        self.assertFalse(Membership.objects.filter(user=self.victim, tenant=self.tenant).exists())

    def test_high_privilege_actor_can_grant_role_within_their_permissions(self):
        """An actor holding every permission the role carries CAN create the membership and
        its own-reach RoleAssignment."""
        _flush_perm_cache(self.high_priv)
        form = MembershipForm(
            data=self._data(self.overpriv_role), user=self.high_priv, tenant=self.tenant,
        )
        self.assertTrue(form.is_valid(), form.errors)
        membership = form.save()
        self.assertTrue(
            membership.assignments.filter(
                role=self.overpriv_role, reach=RoleAssignment.REACH_OWN,
            ).exists()
        )

    def test_superuser_bypasses_membership_form_guard(self):
        """A superuser may grant any role via ``MembershipForm`` (guard is a no-op)."""
        form = MembershipForm(
            data=self._data(self.overpriv_role), user=self.superuser, tenant=self.tenant,
        )
        self.assertTrue(form.is_valid(), form.errors)


# --------------------------------------------------------------------------------------------- #
# 2. MembershipBulkRoleForm / MembershipBulkEditView
# --------------------------------------------------------------------------------------------- #
class MembershipBulkEditEscalationTests(EscalationSurfaceTestCase):
    """Surface #2: bulk own-reach role-add via ``MembershipBulkEditView`` is guarded (the view
    calls ``validate_assignment_grant`` per role in ``roles_to_add`` before mutating)."""

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

    def _has_overpriv_assignment(self):
        return RoleAssignment.objects.filter(
            membership=self.target_membership, role=self.overpriv_role,
            reach=RoleAssignment.REACH_OWN,
        ).exists()

    def test_low_privilege_actor_cannot_bulk_grant_overprivileged_role(self):
        """A narrow-permission actor's bulk role-add of an over-privileged role is rejected;
        no assignment is attached to the target membership."""
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
        self.assertFalse(self._has_overpriv_assignment())

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
        self.grant(manager, self.tenant, manager_role)
        _flush_perm_cache(manager)

        self._login(manager)
        resp = self.client.post(self._url(), {
            'pk': [self.target_membership.pk],
            '_apply': '1',
            'roles_to_add': [self.overpriv_role.pk],
            'return_url': reverse('organization:membership_list'),
        }, follow=True)
        self.assertFalse(self._has_overpriv_assignment())
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
        self.grant(admin, self.tenant, admin_role)
        _flush_perm_cache(admin)

        self._login(admin)
        resp = self.client.post(self._url(), {
            'pk': [self.target_membership.pk],
            '_apply': '1',
            'roles_to_add': [self.overpriv_role.pk],
            'return_url': reverse('organization:membership_list'),
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(self._has_overpriv_assignment())

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
        self.assertTrue(self._has_overpriv_assignment())

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
        permissions is denied (403) and no membership/assignment is created."""
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
        self.assertTrue(
            mem.assignments.filter(role=self.overpriv_role, reach=RoleAssignment.REACH_OWN).exists()
        )

    def test_superuser_can_assign_users_to_any_role(self):
        self._login(self.superuser)
        resp = self.client.post(self._url(), {'users': [self.target_user.pk]})
        self.assertIn(resp.status_code, (200, 302))
        mem = Membership.objects.filter(user=self.target_user, tenant=self.tenant).first()
        self.assertIsNotNone(mem)
        self.assertTrue(
            mem.assignments.filter(role=self.overpriv_role, reach=RoleAssignment.REACH_OWN).exists()
        )


# --------------------------------------------------------------------------------------------- #
# 4. UserGroupForm
# --------------------------------------------------------------------------------------------- #
class UserGroupFormEscalationTests(EscalationSurfaceTestCase):
    """Surface #4: ``UserGroupForm`` rejects (a) attaching an over-privileged role, (b) setting
    a ``tenant`` (owning/SCIM-scope, formerly ``provider``) the actor doesn't administer, and
    (c) adding a member that would confer a role the actor cannot themselves grant."""

    def setUp(self):
        super().setUp()
        self.msp_a = Tenant.objects.create(name="MSP A", slug="msp-a-escalation", is_provider=True)
        self.msp_b = Tenant.objects.create(name="MSP B", slug="msp-b-escalation", is_provider=True)

        # MSP-A group admin: holds users.change_usergroup + NARROW_PERM at A only.
        self.group_admin_role = Role.objects.create(
            tenant=self.msp_a, name="A Group Admin",
            permissions=['users.change_usergroup', NARROW_PERM],
        )
        self.group_admin = User.objects.create_user(
            username='group_admin', email='group_admin@acme.test', password='pw',
        )
        self.grant(self.group_admin, self.msp_a, self.group_admin_role)

        self.target_user = User.objects.create_user(
            username='ug_target', email='ug_target@acme.test', password='pw',
        )

    def test_low_privilege_group_admin_cannot_attach_overprivileged_role(self):
        """(a) A group admin cannot attach a role whose permissions they do not hold — even
        though they administer groups (``users.change_usergroup``) at MSP A."""
        _flush_perm_cache(self.group_admin)
        data = {
            'name': 'Takeover Group', 'roles': [self.overpriv_role.pk], 'members': [],
            'tenant': self.msp_a.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.group_admin)
        self.assertFalse(form.is_valid())
        self.assertFalse(UserGroup.objects.filter(name='Takeover Group').exists())

    def test_low_privilege_group_admin_cannot_set_unmanaged_tenant(self):
        """(b) An MSP-A group admin cannot scope a new group to MSP B, which they do not
        administer."""
        _flush_perm_cache(self.group_admin)
        data = {
            'name': 'Cross Tenant Group', 'roles': [], 'members': [],
            'tenant': self.msp_b.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.group_admin)
        self.assertFalse(form.is_valid())
        self.assertIn('tenant', form.errors)

    def test_low_privilege_group_admin_cannot_add_member_granting_unheld_role(self):
        """(c) Adding a member confers every role the group carries; a group admin who cannot
        grant one of those roles' permissions is blocked on the ``members`` write path too
        (not just a bare role-attach with no members)."""
        _flush_perm_cache(self.group_admin)
        data = {
            'name': 'Overpriv Members Group', 'roles': [self.overpriv_role.pk],
            'members': [self.target_user.pk],
            'tenant': self.msp_a.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.group_admin)
        self.assertFalse(form.is_valid())
        self.assertFalse(UserGroup.objects.filter(name='Overpriv Members Group').exists())

    def test_high_privilege_group_admin_can_attach_role_within_permissions(self):
        """Positive (a): a group admin who also holds the role's permissions (in that role's
        OWN tenant) succeeds."""
        narrow_group_role = Role.objects.create(
            tenant=self.msp_a, name="Narrow Group Role", permissions=[NARROW_PERM],
        )
        _flush_perm_cache(self.group_admin)
        data = {
            'name': 'Legit Group', 'roles': [narrow_group_role.pk], 'members': [],
            'tenant': self.msp_a.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.group_admin)
        self.assertTrue(form.is_valid(), form.errors)

    def test_high_privilege_group_admin_can_set_own_tenant(self):
        """Positive (b): setting the tenant the actor actually administers succeeds."""
        _flush_perm_cache(self.group_admin)
        data = {
            'name': 'Own Tenant Group', 'roles': [], 'members': [],
            'tenant': self.msp_a.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.group_admin)
        self.assertTrue(form.is_valid(), form.errors)

    def test_high_privilege_group_admin_can_add_member_granting_held_role(self):
        """Positive (c): adding a member to a group carrying only a role the actor can
        themselves grant succeeds."""
        narrow_group_role = Role.objects.create(
            tenant=self.msp_a, name="Narrow Member Role", permissions=[NARROW_PERM],
        )
        _flush_perm_cache(self.group_admin)
        data = {
            'name': 'Legit Members Group', 'roles': [narrow_group_role.pk],
            'members': [self.target_user.pk],
            'tenant': self.msp_a.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.group_admin)
        self.assertTrue(form.is_valid(), form.errors)

    def test_superuser_bypasses_all_usergroup_form_guards(self):
        data = {
            'name': 'SU Group', 'roles': [self.overpriv_role.pk],
            'members': [self.target_user.pk],
            'tenant': self.msp_b.pk, 'is_active': True,
        }
        form = UserGroupForm(data=data, user=self.superuser)
        self.assertTrue(form.is_valid(), form.errors)


# --------------------------------------------------------------------------------------------- #
# 5. UserGroupAssignUsersView (users)
# --------------------------------------------------------------------------------------------- #
class UserGroupAssignUsersViewEscalationTests(EscalationSurfaceTestCase):
    """Surface #5: adding a member to a role-carrying UserGroup the actor can't fully grant is
    blocked by ``validate_group_membership_grant`` inside the assign view — even though (unlike
    the form surface above) no ``roles`` field is resubmitted here; the guard reads the group's
    PERSISTED roles via ``Role._base_manager`` (tenant-context-independent)."""

    def setUp(self):
        super().setUp()
        self.msp_a2 = Tenant.objects.create(name="MSP A2", slug="msp-a2-escalation", is_provider=True)

        self.group_admin_role = Role.objects.create(
            tenant=self.msp_a2, name="A2 Group Admin",
            permissions=['users.change_usergroup', NARROW_PERM],
        )
        self.group_admin = User.objects.create_user(
            username='ug_assign_admin', email='ug_assign_admin@acme.test', password='pw',
        )
        self.grant(self.group_admin, self.msp_a2, self.group_admin_role)

        # A group carrying the over-privileged tenant role — the admin above cannot grant this
        # role's permissions, so adding a member must be blocked.
        self.overpriv_group = UserGroup.objects.create(name="Overpriv Group")
        self.overpriv_group.roles.add(self.overpriv_role)

        self.target_user = User.objects.create_user(
            username='ug_assign_target', email='ug_assign_target@acme.test', password='pw',
        )

    def _url(self, group=None):
        return reverse('users:usergroup_assign_users', kwargs={'pk': (group or self.overpriv_group).pk})

    def test_low_privilege_group_admin_cannot_add_member_to_overprivileged_group(self):
        """A group admin (gated in via ``users.change_usergroup`` at MSP A2) cannot add a
        member to a group carrying a role whose permissions they don't hold — no member is
        added."""
        _flush_perm_cache(self.group_admin)
        self.client.force_login(self.group_admin)
        resp = self.client.post(self._url(), {'users': [self.target_user.pk]})
        self.assertEqual(resp.status_code, 200)  # re-rendered form, not redirected
        self.assertFalse(self.overpriv_group.members.filter(pk=self.target_user.pk).exists())

    def test_high_privilege_group_admin_can_add_member_to_group_within_permissions(self):
        """Positive: a group admin who holds the carried role's permissions succeeds."""
        narrow_group = UserGroup.objects.create(name="Narrow Group")
        narrow_tenant_role = Role.objects.create(
            tenant=self.msp_a2, name="A2 Narrow", permissions=[NARROW_PERM],
        )
        narrow_group.roles.add(narrow_tenant_role)
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
# 6. Unified MembershipForm managed-reach onboarding
# --------------------------------------------------------------------------------------------- #
class UnifiedMembershipOnboardingEscalationTests(EscalationSurfaceTestCase):
    """Surface #6: onboarding a technician with an over-privileged MANAGED-reach role
    assignment is blocked by ``MembershipForm.clean()``,
    which delegates to ``validate_assignment_grant`` for the managed-reach grant."""

    def setUp(self):
        super().setUp()
        self.org = Tenant.objects.create(
            name="MSP Onboard", slug="msp-onboard-escalation", is_provider=True,
        )

        # Low-priv onboarder: can create memberships at the org, nothing else — in
        # particular no ``organization.add_roleassignment``, so no managed-reach grant of
        # ANY role should succeed for them, regardless of the role's own permission content.
        self.onboarder_role = Role.objects.create(
            tenant=self.org, name="Onboarder", permissions=['organization.add_membership'],
        )
        self.onboarder = User.objects.create_user(
            username='onboarder', email='onboarder@acme.test', password='pw',
        )
        self.grant(self.onboarder, self.org, self.onboarder_role)

        # High-priv onboarder: add_membership + add_roleassignment + the broad perms, PLUS a
        # managed-reach assignment of their own covering ALL managed tenants — you cannot
        # hand out reach broader than you hold.
        self.senior_role = Role.objects.create(
            tenant=self.org, name="Provider Full Admin",
            permissions=['organization.add_membership', 'organization.add_roleassignment'] + BROAD_PERMS,
        )
        self.senior_onboarder = User.objects.create_user(
            username='senior_onboarder', email='senior_onboarder@acme.test', password='pw',
        )
        self.grant(self.senior_onboarder, self.org, self.senior_role)
        self.grant(
            self.senior_onboarder, self.org, self.senior_role,
            reach=RoleAssignment.REACH_MANAGED, managed_scope=RoleAssignment.SCOPE_ALL,
        )

        # The over-privileged role being granted during onboarding.
        self.overpriv_provider_role = Role.objects.create(
            tenant=self.org, name="Overpriv Provider Role", permissions=list(BROAD_PERMS),
        )

    def _form_data(self, role):
        return {
            'who': MembershipForm.WHO_NEW,
            'new_user_email': 'newtech@acme.test',
            'new_user_first_name': 'New',
            'new_user_last_name': 'Tech',
            'tenant': self.org.pk,
            'roles': [role.pk] if role else [],
            'reach_managed': 'on',
            'managed_scope': RoleAssignment.SCOPE_ALL,
            'is_active': 'on',
        }

    def test_low_privilege_onboarder_cannot_onboard_with_overprivileged_role(self):
        """A user who can create memberships but does not hold the role's permissions (nor
        ``add_roleassignment``) cannot onboard a technician into that role."""
        _flush_perm_cache(self.onboarder)
        form = MembershipForm(
            data=self._form_data(self.overpriv_provider_role),
            user=self.onboarder,
            tenant=self.org,
        )
        self.assertFalse(form.is_valid())
        self.assertTrue(form.non_field_errors())
        self.assertFalse(User.objects.filter(email='newtech@acme.test').exists())

    def test_high_privilege_onboarder_can_onboard_with_role_within_their_permissions(self):
        """An onboarder holding the role's permissions AND sufficient managed reach can
        onboard the technician, creating a managed-reach RoleAssignment."""
        _flush_perm_cache(self.senior_onboarder)
        form = MembershipForm(
            data=self._form_data(self.overpriv_provider_role),
            user=self.senior_onboarder,
            tenant=self.org,
        )
        self.assertTrue(form.is_valid(), form.errors)
        membership = form.save()
        self.assertTrue(
            membership.assignments.filter(
                role=self.overpriv_provider_role, reach=RoleAssignment.REACH_MANAGED,
            ).exists()
        )

    def test_superuser_can_onboard_with_any_role(self):
        form = MembershipForm(
            data=self._form_data(self.overpriv_provider_role),
            user=self.superuser,
            tenant=self.org,
        )
        self.assertTrue(form.is_valid(), form.errors)


# --------------------------------------------------------------------------------------------- #
# 7. Reach-grant escalation — validate_assignment_grant itself
# --------------------------------------------------------------------------------------------- #
class ReachGrantEscalationTests(EscalationSurfaceTestCase):
    """NEW surface: granting MANAGED reach is itself escalation-checked by
    ``validate_assignment_grant`` (core/auth/guards.py), independent of whether the actor holds
    the role's own permission content:

      * an actor who holds the role's permissions but lacks
        ``organization.add_roleassignment`` / ``change_roleassignment`` at the managing tenant
        cannot create ANY managed-reach assignment there ("the gate");
      * an actor whose own managed coverage is EXPLICIT and narrower than what's requested (e.g.
        holds only tenant A, asked to grant [A, B] or SCOPE_ALL) is rejected — "you cannot hand
        out broader reach than you have" ("the coverage subset check").

    Exercised both directly against the guard function (precise unit coverage of the boundary)
    and through ``MembershipForm`` (the same semantics via a real write path)."""

    def setUp(self):
        super().setUp()
        self.org = Tenant.objects.create(name="Reach MSP", slug="reach-msp", is_provider=True)
        self.customer_a = Tenant.objects.create(
            name="Customer A", slug="reach-customer-a", managed_by=self.org,
        )
        self.customer_b = Tenant.objects.create(
            name="Customer B", slug="reach-customer-b", managed_by=self.org,
        )
        # A role whose permissions every actor below holds (NARROW_PERM only), so the base
        # ``validate_permission_grant`` check never fires — isolating the reach-specific checks.
        self.managed_role = Role.objects.create(
            tenant=self.org, name="Managed Viewer", permissions=[NARROW_PERM],
        )

    def _actor(self, name, *, own_perms=(), managed_scope=None, assigned=None):
        """A user holding ``own_perms`` (+ NARROW_PERM) at ``self.org`` via own reach, and
        optionally a managed-reach assignment of their own with the given refinement."""
        role = Role.objects.create(
            tenant=self.org, name=f"{name}-role", permissions=list(own_perms) + [NARROW_PERM],
        )
        user = User.objects.create_user(
            username=name, email=f'{name}@acme.test', password='pw',
        )
        self.grant(user, self.org, role)
        if managed_scope is not None:
            self.grant(
                user, self.org, role, reach=RoleAssignment.REACH_MANAGED,
                managed_scope=managed_scope, assigned_tenants=assigned,
            )
        _flush_perm_cache(user)
        return user

    # ---- the gate: add/change_roleassignment required for ANY managed-reach grant --------- #

    def test_actor_without_roleassignment_perm_cannot_create_any_managed_grant(self):
        """Even an actor whose own coverage is ALL cannot grant managed reach at all without
        holding ``add_roleassignment``/``change_roleassignment`` — the gate comes first."""
        actor = self._actor('no_gate', own_perms=[], managed_scope=RoleAssignment.SCOPE_ALL)
        with self.assertRaises(ValidationError):
            validate_assignment_grant(
                actor, self.managed_role, self.org, reach=RoleAssignment.REACH_MANAGED,
                requested_tenant_ids={self.customer_a.pk},
            )

    def test_actor_with_roleassignment_perm_passes_the_gate(self):
        actor = self._actor(
            'gated', own_perms=['organization.add_roleassignment'],
            managed_scope=RoleAssignment.SCOPE_ALL,
        )
        validate_assignment_grant(  # must not raise
            actor, self.managed_role, self.org, reach=RoleAssignment.REACH_MANAGED,
            requested_tenant_ids={self.customer_a.pk},
        )

    # ---- the coverage-subset check: explicit [A] cannot grant [A, B] or ALL --------------- #

    def test_actor_with_explicit_a_cannot_grant_a_and_b(self):
        actor = self._actor(
            'explicit_a', own_perms=['organization.add_roleassignment'],
            managed_scope=RoleAssignment.SCOPE_EXPLICIT, assigned=[self.customer_a],
        )
        with self.assertRaises(ValidationError):
            validate_assignment_grant(
                actor, self.managed_role, self.org, reach=RoleAssignment.REACH_MANAGED,
                requested_tenant_ids={self.customer_a.pk, self.customer_b.pk},
            )

    def test_actor_with_explicit_a_cannot_grant_scope_all(self):
        actor = self._actor(
            'explicit_a2', own_perms=['organization.add_roleassignment'],
            managed_scope=RoleAssignment.SCOPE_EXPLICIT, assigned=[self.customer_a],
        )
        with self.assertRaises(ValidationError):
            validate_assignment_grant(
                actor, self.managed_role, self.org, reach=RoleAssignment.REACH_MANAGED,
                requested_tenant_ids=None,  # None == SCOPE_ALL requested
            )

    def test_actor_with_explicit_a_can_grant_subset_of_own_coverage(self):
        actor = self._actor(
            'explicit_subset', own_perms=['organization.add_roleassignment'],
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
            assigned=[self.customer_a, self.customer_b],
        )
        validate_assignment_grant(  # must not raise: {A} ⊆ {A, B}
            actor, self.managed_role, self.org, reach=RoleAssignment.REACH_MANAGED,
            requested_tenant_ids={self.customer_a.pk},
        )

    def test_actor_with_scope_all_can_grant_any_subset_or_all(self):
        actor = self._actor(
            'scope_all', own_perms=['organization.add_roleassignment'],
            managed_scope=RoleAssignment.SCOPE_ALL,
        )
        validate_assignment_grant(  # subset
            actor, self.managed_role, self.org, reach=RoleAssignment.REACH_MANAGED,
            requested_tenant_ids={self.customer_a.pk},
        )
        validate_assignment_grant(  # SCOPE_ALL
            actor, self.managed_role, self.org, reach=RoleAssignment.REACH_MANAGED,
            requested_tenant_ids=None,
        )

    def test_superuser_bypasses_reach_grant_guard_entirely(self):
        validate_assignment_grant(  # no membership/assignment of their own — still a no-op
            self.superuser, self.managed_role, self.org, reach=RoleAssignment.REACH_MANAGED,
            requested_tenant_ids=None,
        )

    # ---- integration: the same semantics through a real write path (MembershipForm) ------- #

    def test_membership_form_blocks_explicit_a_actor_granting_scope_all(self):
        """End-to-end: ``MembershipForm.clean()`` calls ``validate_assignment_grant`` per
        selected role, so the same [A] → ALL escalation is rejected through the actual write
        path, not just the bare guard function."""
        actor = self._actor(
            'form_explicit_a', own_perms=['organization.add_roleassignment'],
            managed_scope=RoleAssignment.SCOPE_EXPLICIT, assigned=[self.customer_a],
        )
        victim = User.objects.create_user(
            username='reach_victim', email='reach_victim@acme.test', password='pw',
        )
        form = MembershipForm(
            data={
                'user': victim.pk,
                'tenant': self.org.pk,
                'roles': [self.managed_role.pk],
                'reach_managed': 'on',
                'managed_scope': RoleAssignment.SCOPE_ALL,
                'is_active': True,
            },
            user=actor,
            tenant=self.org,
        )
        self.assertFalse(form.is_valid())
        self.assertFalse(Membership.objects.filter(user=victim, tenant=self.org).exists())

    def test_membership_form_allows_explicit_a_actor_granting_subset(self):
        """Positive counterpart: granting a subset of the actor's own coverage succeeds."""
        actor = self._actor(
            'form_explicit_subset', own_perms=['organization.add_roleassignment'],
            managed_scope=RoleAssignment.SCOPE_EXPLICIT,
            assigned=[self.customer_a, self.customer_b],
        )
        victim = User.objects.create_user(
            username='reach_grantee', email='reach_grantee@acme.test', password='pw',
        )
        form = MembershipForm(
            data={
                'user': victim.pk,
                'tenant': self.org.pk,
                'roles': [self.managed_role.pk],
                'reach_managed': 'on',
                'managed_scope': RoleAssignment.SCOPE_EXPLICIT,
                'assigned_tenants': [self.customer_a.pk],
                'is_active': True,
            },
            user=actor,
            tenant=self.org,
        )
        self.assertTrue(form.is_valid(), form.errors)
        membership = form.save()
        assignment = membership.assignments.get(
            role=self.managed_role, reach=RoleAssignment.REACH_MANAGED,
        )
        self.assertEqual(
            set(assignment.assigned_tenants.values_list('pk', flat=True)), {self.customer_a.pk},
        )
