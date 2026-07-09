"""Regression tests for FIX #4 (§3-D): the raw-JSON ``direct_permissions`` textarea
is removed from ``MembershipForm``.

Covers:
  (a) MembershipForm exposes no ``direct_permissions`` field (last no-JSON violation gone).
  (b) Saving a membership with roles still works, and the escalation guard still fires on
      role permissions — a low-priv actor cannot attach an over-privileged role.
  (c) An existing membership that already has ``direct_permissions`` in the DB is not wiped
      by editing the membership through the form (the model column is untouched).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from core.managers import set_current_tenant, set_current_membership
from core.tests.mixins import TenantTestMixin
from organization.models import Tenant, Membership, Role
from organization.forms.membership_form import MembershipForm

User = get_user_model()


def _make_role(tenant, name, perms=None):
    return Role.objects.create(tenant=tenant, name=name, permissions=perms or [])


class MembershipFormNoJSONFieldTests(TenantTestMixin, TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name="Corp", slug="corp-nojson")
        self.superuser = User.objects.create_superuser(
            username="su_nojson", email="su_nojson@x.com", password="pw",
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    # (a) ---------------------------------------------------------------
    def test_form_has_no_direct_permissions_field(self):
        form = MembershipForm()
        self.assertNotIn('direct_permissions', form.fields)
        self.assertNotIn('direct_permissions', MembershipForm.Meta.fields)

    def test_layout_never_references_direct_permissions(self):
        """The crispy layout (and its bound-field walk) must not mention the dropped field."""
        form = MembershipForm(tenant=self.tenant)
        # Rendering exercises the layout; a stale field name would raise / silently render.
        rendered = str(form)
        self.assertNotIn('direct_permissions', rendered)


class MembershipFormRoleSaveTests(TenantTestMixin, TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name="Save Corp", slug="save-corp")
        self.superuser = User.objects.create_superuser(
            username="su_save", email="su_save@x.com", password="pw",
        )
        self.member_user = User.objects.create_user(
            username="member_save", email="member_save@x.com", password="pw",
        )
        self.role = _make_role(self.tenant, "Viewer", ["assets.view_asset"])

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    # (b) ---------------------------------------------------------------
    def test_saving_membership_with_roles_still_works(self):
        form = MembershipForm(
            data={
                'user': self.member_user.pk,
                'tenant': self.tenant.pk,
                'roles': [self.role.pk],
                'is_active': 'on',
            },
            user=self.superuser,
            tenant=self.tenant,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        membership = form.save()
        membership.refresh_from_db()
        self.assertEqual(membership.tenant_id, self.tenant.pk)
        self.assertIn(self.role, membership.roles.all())

    def test_escalation_guard_fires_on_role_permissions(self):
        """A low-priv actor cannot attach a role granting perms they do not themselves hold."""
        # Low-priv actor: only holds view_asset in the tenant.
        low_role = _make_role(self.tenant, "LowPriv", ["assets.view_asset"])
        low_actor = User.objects.create_user(
            username="lowpriv", email="lowpriv@x.com", password="pw",
        )
        Membership.objects.create(
            user=low_actor, tenant=self.tenant, is_active=True,
        ).roles.add(low_role)

        # Over-privileged role the low actor must not be able to grant.
        powerful_role = _make_role(
            self.tenant, "Powerful", ["assets.view_asset", "assets.delete_asset"],
        )

        form = MembershipForm(
            data={
                'user': self.member_user.pk,
                'tenant': self.tenant.pk,
                'roles': [powerful_role.pk],
                'is_active': 'on',
            },
            user=low_actor,
            tenant=self.tenant,
        )
        self.assertFalse(form.is_valid())
        # The escalation error is raised in clean() (a non-field error).
        combined = form.errors.as_text().lower()
        self.assertIn('escalation', combined)
        # And nothing was persisted.
        self.assertFalse(
            Membership.objects.filter(user=self.member_user, tenant=self.tenant).exists()
        )

    def test_escalation_guard_passes_for_authorized_actor(self):
        """An actor who already holds the role's perms may grant them."""
        priv_role = _make_role(
            self.tenant, "Manager", ["assets.view_asset", "assets.delete_asset"],
        )
        actor = User.objects.create_user(
            username="mgr_actor", email="mgr_actor@x.com", password="pw",
        )
        # Actor holds the exact perms the granted role confers.
        actor_role = _make_role(
            self.tenant, "MgrOwn", ["assets.view_asset", "assets.delete_asset"],
        )
        Membership.objects.create(
            user=actor, tenant=self.tenant, is_active=True,
        ).roles.add(actor_role)

        form = MembershipForm(
            data={
                'user': self.member_user.pk,
                'tenant': self.tenant.pk,
                'roles': [priv_role.pk],
                'is_active': 'on',
            },
            user=actor,
            tenant=self.tenant,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())


class MembershipFormPreservesDirectPermsTests(TenantTestMixin, TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name="Keep Corp", slug="keep-corp")
        self.superuser = User.objects.create_superuser(
            username="su_keep", email="su_keep@x.com", password="pw",
        )
        self.member_user = User.objects.create_user(
            username="member_keep", email="member_keep@x.com", password="pw",
        )
        self.role_a = _make_role(self.tenant, "RoleA", ["assets.view_asset"])
        self.role_b = _make_role(self.tenant, "RoleB", ["assets.view_asset"])
        # Pre-existing membership carrying direct_permissions already in the DB.
        self.membership = Membership.objects.create(
            user=self.member_user,
            tenant=self.tenant,
            is_active=True,
            direct_permissions=["assets.change_asset"],
        )
        self.membership.roles.add(self.role_a)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    # (c) ---------------------------------------------------------------
    def test_editing_membership_does_not_wipe_existing_direct_permissions(self):
        form = MembershipForm(
            data={
                'user': self.member_user.pk,
                'tenant': self.tenant.pk,
                'roles': [self.role_b.pk],
                'is_active': 'on',
            },
            instance=self.membership,
            user=self.superuser,
            tenant=self.tenant,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        self.membership.refresh_from_db()
        # Role change took effect...
        self.assertIn(self.role_b, self.membership.roles.all())
        # ...but the pre-existing direct_permissions column is untouched.
        self.assertEqual(self.membership.direct_permissions, ["assets.change_asset"])
