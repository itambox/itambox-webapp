"""Explicit scoping declarations of the users forms (#584, WP3).

The users forms declare tenant scoping and TomSelect behaviour through
``TenantScopedFormMixin`` instead of relying on the global form patches. These
assertions pin each declaration directly so they stay true when the patches are
removed.
"""

from django.test import TestCase

from core.forms import TenantScopedFormMixin
from core.forms.scoping import is_tenant_scoped_field
from core.managers import set_current_tenant
from core.tests.mixins import TenantTestMixin
from itambox.middleware import set_current_user
from organization.models import Membership, Tenant
from users.forms import (
    GroupManagedRoleGrantForm,
    UserGroupAssignUsersForm,
    UserGroupForm,
    UserPreferencesForm,
)
from users.models import User, UserGroup

FORMS = (UserPreferencesForm, GroupManagedRoleGrantForm, UserGroupForm, UserGroupAssignUsersForm)


class UsersFormDeclarationTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="ufs-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="ufs-b")
        self.user = User.objects.create_user("ufs-user", password="x")
        self.membership_a = Membership.objects.create(user=self.user, tenant=self.tenant)
        self.group = UserGroup.objects.create(name="Ops", tenant=self.tenant)
        self.set_active_tenant(self.tenant)

    def tearDown(self):
        set_current_user(None)
        set_current_tenant(None)
        self.clear_tenant_context()

    def test_forms_use_the_explicit_mixin(self):
        for form_class in FORMS:
            with self.subTest(form=form_class.__name__):
                self.assertTrue(issubclass(form_class, TenantScopedFormMixin))

    def test_deliberate_base_manager_pickers_are_declared_exclusions(self):
        self.assertEqual(
            set(GroupManagedRoleGrantForm.tenant_scoped_choice_exclusions), {"role", "scope_group", "assigned_tenants"}
        )
        self.assertEqual(set(UserGroupForm.tenant_scoped_choice_exclusions), {"tenant", "roles"})

    def test_membership_picker_is_limited_to_the_group_tenant(self):
        # Membership has no ambient-scope manager; the form filters by the
        # group's tenant explicitly, which the mixin must not disturb.
        other = User.objects.create_user("ufs-other", password="x")
        foreign = Membership.objects.create(user=other, tenant=self.tenant_b)
        pks = set(
            UserGroupAssignUsersForm(group=self.group).fields["memberships"].queryset.values_list("pk", flat=True)
        )
        self.assertIn(self.membership_a.pk, pks)
        self.assertNotIn(foreign.pk, pks)

    def test_excluded_pickers_stay_non_scoped(self):
        form = UserGroupForm(user=None, tenant=self.tenant)
        self.assertFalse(is_tenant_scoped_field(form.fields["tenant"]))
        self.assertFalse(is_tenant_scoped_field(form.fields["roles"]))

    def test_user_group_tenant_stays_required(self):
        self.assertTrue(UserGroupForm(user=None, tenant=self.tenant).fields["tenant"].required)

    def test_select_widgets_carry_the_tom_select_attribute(self):
        forms_and_fields = (
            (UserPreferencesForm(user=self.user), ("pagination_per_page", "theme", "language", "default_workspace")),
            (
                GroupManagedRoleGrantForm(owner=self.tenant),
                ("role", "managed_scope", "scope_group", "assigned_tenants"),
            ),
            (UserGroupForm(user=None, tenant=self.tenant), ("roles", "members", "tenant")),
            (UserGroupAssignUsersForm(group=self.group), ("memberships",)),
        )
        for form, names in forms_and_fields:
            for name in names:
                with self.subTest(form=type(form).__name__, field=name):
                    self.assertIn("data-tom-select", form.fields[name].widget.attrs)
