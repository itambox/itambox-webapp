"""Tests for the stage-3 unified ``MembershipForm`` — the "Add member" grant flow.

The form authors the whole grant in one screen
(``organization/forms/membership_form.py``):

  * **Who** (create only) — radio "Existing user" (user select) vs. "New user"
    (email + first/last). ``clean()`` enforces exactly one side; a new user is
    get-or-created by email with ``set_unusable_password()``.
  * **What** — roles; the picker offers the membership tenant's own roles plus
    definitions shared down by its managing organization, labelled
    "(from <provider>)".
  * **Where** — only on a managing (``is_provider``) tenant: "This organization"
    and/or "Managed tenants" checkboxes (+ coverage refinement). ``save()``
    writes one own-reach and/or one managed-reach ``RoleAssignment`` row per
    selected role, stamped ``granted_by=<the acting user>``.
  * **Edit** reconciles BOTH reaches: rows at a deselected reach are deleted,
    surviving (role, reach) rows keep their ``granted_by`` provenance.

Covers:
  (a) the tenant context kwarg governs the create flow, including the case
      where a stale/mismatched ``initial`` dict entry is also present.
  (b) the real view path: ``MembershipCreateView`` with ``?tenant=<pk>`` —
      mirrors ``get_initial()``/``get_form_kwargs()`` end to end.
  (c) the Where block only renders (and only accepts managed reach) when the
      membership's tenant ``is_provider``; a defense-in-depth check in
      ``clean()`` re-validates against whatever tenant was *actually posted*.
  (d) ``save()`` creates ``RoleAssignment`` rows — own, managed, or BOTH per
      selected role — with ``granted_by`` set to the acting user.
  (e) editing reconciles rows at BOTH reaches (add + remove + reach-uncheck),
      preserving ``granted_by`` on rows left untouched.
  (f) the who-block: inline user creation (unusable password), get-or-create by
      email, exactly-one-side enforcement, duplicate-membership rejection, and
      absence of the block on edit.
  (g) shared-in roles are labelled "(from <provider>)" in the picker.
"""
from django import forms
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.tests.mixins import TenantTestMixin, grant
from organization.models import Tenant, Membership, Role, RoleAssignment
from organization.forms.membership_form import MembershipForm

User = get_user_model()

WHERE_FIELDS = ('reach_own', 'reach_managed', 'managed_scope', 'scope_group',
                'assigned_tenants')


class MembershipFormTenantContextTests(TenantTestMixin, TestCase):
    """(a) The ``tenant=`` context kwarg governs the create flow."""

    def setUp(self):
        self.clear_tenant_context()
        self.tenant = Tenant.objects.create(name="Corp", slug="corp-mfci-a")
        self.other_tenant = Tenant.objects.create(name="Other Corp", slug="other-mfci-a")
        self.superuser = User.objects.create_superuser(
            username="su_mfci_a", email="su_mfci_a@x.com", password="pw",
        )
        self.member_user = User.objects.create_user(
            username="member_mfci_a", email="member_mfci_a@x.com", password="pw",
        )

    def tearDown(self):
        self.clear_tenant_context()

    def test_tenant_context_wins_over_a_mismatched_initial_entry(self):
        form = MembershipForm(
            tenant=self.tenant,
            initial={'tenant': str(self.other_tenant.pk)},
            user=self.superuser,
        )
        self.assertEqual(form.fields['tenant'].initial, self.tenant.pk)
        self.assertIsInstance(form.fields['tenant'].widget, forms.HiddenInput)

    def test_tenant_context_create_is_submittable_and_saves_against_context_tenant(self):
        bound = MembershipForm(
            data={
                'user': self.member_user.pk,
                'tenant': str(self.tenant.pk),
                'is_active': 'on',
            },
            tenant=self.tenant,
            initial={'tenant': str(self.other_tenant.pk)},
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        membership = bound.save()
        self.assertEqual(membership.tenant_id, self.tenant.pk)

    def test_context_free_create_tenant_field_stays_a_visible_enabled_select(self):
        form = MembershipForm(user=self.superuser)
        self.assertNotIsInstance(form.fields['tenant'].widget, forms.HiddenInput)
        self.assertIsNone(form.fields['tenant'].initial)
        self.assertFalse(form.fields['tenant'].disabled)

    def test_edit_locks_the_tenant_field_to_the_instance_tenant(self):
        membership = Membership.objects.create(
            user=self.member_user, tenant=self.tenant, is_active=True,
        )
        form = MembershipForm(instance=membership, user=self.superuser)
        self.assertEqual(form.fields['tenant'].initial, self.tenant.pk)
        self.assertTrue(form.fields['tenant'].disabled)


class MembershipCreateViewTenantContextTests(TenantTestMixin, TestCase):
    """(b) End-to-end through the real view: ``?tenant=<pk>`` mirrors
    ``get_initial()``/``get_form_kwargs()`` exactly."""

    def setUp(self):
        self.clear_tenant_context()
        self.tenant = Tenant.objects.create(name="View Corp", slug="view-corp-mfci")
        self.superuser = User.objects.create_superuser(
            username="su_view_mfci", email="su_view_mfci@x.com", password="pw",
        )
        self.staff_user = User.objects.create_user(
            username="staff_view_mfci", email="staff_view_mfci@x.com", password="pw",
        )
        self.client.force_login(self.superuser)
        session = self.client.session
        session['active_tenant_id'] = self.tenant.pk
        session.save()

    def tearDown(self):
        self.clear_tenant_context()

    def test_tenant_get_param_renders_hidden_field_with_context_initial(self):
        url = reverse('organization:membership_create') + f'?tenant={self.tenant.pk}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertIsInstance(form.fields['tenant'].widget, forms.HiddenInput)
        self.assertEqual(form.fields['tenant'].initial, self.tenant.pk)

    def test_tenant_get_param_flow_is_submittable_and_lands_on_membership_detail(self):
        url = reverse('organization:membership_create') + f'?tenant={self.tenant.pk}'
        response = self.client.post(url, data={
            'user': self.staff_user.pk,
            'tenant': self.tenant.pk,
            'is_active': 'on',
        })
        self.assertEqual(
            response.status_code, 302,
            getattr(response, 'context', None) and response.context['form'].errors.as_json(),
        )
        membership = Membership.objects.get(user=self.staff_user)
        self.assertEqual(membership.tenant_id, self.tenant.pk)
        # The unified flow's success target is the membership detail (spec item 1).
        self.assertEqual(
            response.url,
            reverse('organization:membership_detail', kwargs={'pk': membership.pk}),
        )


class MembershipFormWhereBlockTests(TenantTestMixin, TestCase):
    """(c) The Where block (reach checkboxes + refinement) renders only when the
    membership's tenant is a managing (``is_provider``) tenant, and ``clean()``
    re-validates that against whatever tenant was actually posted."""

    def setUp(self):
        self.clear_tenant_context()
        self.msp_tenant = Tenant.objects.create(name="MSP", slug="msp-mfrb", is_provider=True)
        self.customer_tenant = Tenant.objects.create(
            name="Customer", slug="customer-mfrb", managed_by=self.msp_tenant,
        )
        self.standalone_tenant = Tenant.objects.create(name="Standalone", slug="standalone-mfrb")
        self.superuser = User.objects.create_superuser(
            username="su_mfrb", email="su_mfrb@x.com", password="pw",
        )

    def tearDown(self):
        self.clear_tenant_context()

    def test_provider_tenant_offers_reach_checkboxes_and_refinement_fields(self):
        form = MembershipForm(tenant=self.msp_tenant, user=self.superuser)
        for fname in WHERE_FIELDS:
            self.assertIn(fname, form.fields)
        # "This organization" defaults on, "Managed tenants" off.
        self.assertTrue(form.fields['reach_own'].initial)
        self.assertFalse(form.fields['reach_managed'].initial)

    def test_non_provider_tenant_drops_the_where_block(self):
        for tenant in (self.customer_tenant, self.standalone_tenant):
            form = MembershipForm(tenant=tenant, user=self.superuser)
            for fname in WHERE_FIELDS:
                self.assertNotIn(fname, form.fields)

    def test_context_free_create_offers_the_where_block_pending_the_tenant_pick(self):
        # Unknown tenant (no context, unbound form): the block stays available
        # so it can render; clean() re-validates once a real tenant is submitted.
        form = MembershipForm(user=self.superuser)
        for fname in WHERE_FIELDS:
            self.assertIn(fname, form.fields)

    def test_clean_rejects_managed_reach_for_a_non_provider_posted_tenant(self):
        member_user = User.objects.create_user(
            username="member_mfrb_tamper", email="member_mfrb_tamper@x.com", password="pw",
        )
        bound = MembershipForm(
            data={
                'user': member_user.pk,
                # Posted tenant differs from the (provider) context tenant below —
                # the ``tenant`` field's queryset is never narrowed to the context
                # tenant, only its initial/widget are set, so a tampered hidden
                # input can post any tenant. clean() must catch this even though
                # the Where fields were offered based on the context tenant.
                'tenant': self.standalone_tenant.pk,
                'roles': [],
                'reach_managed': 'on',
                'managed_scope': RoleAssignment.SCOPE_ALL,
                'is_active': 'on',
            },
            tenant=self.msp_tenant,
            user=self.superuser,
        )
        self.assertFalse(bound.is_valid())
        self.assertIn('reach_managed', bound.errors)

    def test_own_reach_is_implied_for_a_non_provider_tenant(self):
        member_user = User.objects.create_user(
            username="member_mfrb_own", email="member_mfrb_own@x.com", password="pw",
        )
        role = Role.objects.create(tenant=self.customer_tenant, name="Local Role", permissions=[])
        bound = MembershipForm(
            data={
                'user': member_user.pk,
                'tenant': self.customer_tenant.pk,
                'roles': [role.pk],
                'is_active': 'on',
            },
            tenant=self.customer_tenant,
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        membership = bound.save()
        self.assertTrue(
            membership.assignments.filter(
                role=role, reach=RoleAssignment.REACH_OWN,
            ).exists()
        )

    def test_roles_without_any_reach_checkbox_are_rejected_on_a_provider_tenant(self):
        member_user = User.objects.create_user(
            username="member_mfrb_noreach", email="member_mfrb_noreach@x.com", password="pw",
        )
        role = Role.objects.create(tenant=self.msp_tenant, name="MSP Role NR", permissions=[])
        bound = MembershipForm(
            data={
                'user': member_user.pk,
                'tenant': self.msp_tenant.pk,
                'roles': [role.pk],
                # neither reach_own nor reach_managed checked
                'is_active': 'on',
            },
            tenant=self.msp_tenant,
            user=self.superuser,
        )
        self.assertFalse(bound.is_valid())
        self.assertIn('reach_own', bound.errors)


class MembershipFormSaveCreatesAssignmentsTests(TenantTestMixin, TestCase):
    """(d) ``save()`` writes one ``RoleAssignment`` row per selected role and
    selected reach, stamped with the acting (granting) user."""

    def setUp(self):
        self.clear_tenant_context()
        self.tenant = Tenant.objects.create(name="Corp", slug="corp-mfsa")
        self.msp_tenant = Tenant.objects.create(name="MSP", slug="msp-mfsa", is_provider=True)
        self.customer_tenant = Tenant.objects.create(
            name="Customer", slug="customer-mfsa", managed_by=self.msp_tenant,
        )
        self.superuser = User.objects.create_superuser(
            username="su_mfsa", email="su_mfsa@x.com", password="pw",
        )
        self.member_user = User.objects.create_user(
            username="member_mfsa", email="member_mfsa@x.com", password="pw",
        )
        self.role = Role.objects.create(
            tenant=self.tenant, name="Editor", permissions=["organization.view_membership"],
        )
        self.msp_role = Role.objects.create(
            tenant=self.msp_tenant, name="MSP Technician", permissions=[], shared_with_managed=True,
        )

    def tearDown(self):
        self.clear_tenant_context()

    def test_own_reach_save_creates_assignment_stamped_with_actor(self):
        bound = MembershipForm(
            data={
                'user': self.member_user.pk,
                'tenant': self.tenant.pk,
                'roles': [self.role.pk],
                'is_active': 'on',
            },
            tenant=self.tenant,
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        membership = bound.save()
        assignments = list(membership.assignments.all())
        self.assertEqual(len(assignments), 1)
        assignment = assignments[0]
        self.assertEqual(assignment.role_id, self.role.pk)
        self.assertEqual(assignment.reach, RoleAssignment.REACH_OWN)
        self.assertEqual(assignment.granted_by_id, self.superuser.pk)

    def test_managed_reach_save_creates_assignment_with_scope_and_actor(self):
        bound = MembershipForm(
            data={
                'user': self.member_user.pk,
                'tenant': self.msp_tenant.pk,
                'roles': [self.msp_role.pk],
                'reach_managed': 'on',
                'managed_scope': RoleAssignment.SCOPE_ALL,
                'is_active': 'on',
            },
            tenant=self.msp_tenant,
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        membership = bound.save()
        assignment = membership.assignments.get(role=self.msp_role)
        self.assertEqual(assignment.reach, RoleAssignment.REACH_MANAGED)
        self.assertEqual(assignment.managed_scope, RoleAssignment.SCOPE_ALL)
        self.assertEqual(assignment.granted_by_id, self.superuser.pk)
        self.assertTrue(assignment.covers_tenant(self.customer_tenant))

    def test_both_reaches_checked_write_two_rows_per_role(self):
        bound = MembershipForm(
            data={
                'user': self.member_user.pk,
                'tenant': self.msp_tenant.pk,
                'roles': [self.msp_role.pk],
                'reach_own': 'on',
                'reach_managed': 'on',
                'managed_scope': RoleAssignment.SCOPE_ALL,
                'is_active': 'on',
            },
            tenant=self.msp_tenant,
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        membership = bound.save()
        reaches = set(
            membership.assignments.filter(role=self.msp_role)
            .values_list('reach', flat=True)
        )
        self.assertEqual(reaches, {RoleAssignment.REACH_OWN, RoleAssignment.REACH_MANAGED})
        for assignment in membership.assignments.all():
            self.assertEqual(assignment.granted_by_id, self.superuser.pk)

    def test_no_roles_selected_creates_membership_without_assignments(self):
        bound = MembershipForm(
            data={'user': self.member_user.pk, 'tenant': self.tenant.pk, 'is_active': 'on'},
            tenant=self.tenant,
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        membership = bound.save()
        self.assertEqual(membership.assignments.count(), 0)


class MembershipFormEditReconcilesBothReachesTests(TenantTestMixin, TestCase):
    """(e) Editing a membership reconciles ``RoleAssignment`` rows at BOTH
    reaches: deselected roles lose their rows, an unchecked reach loses ALL its
    rows, and untouched rows keep their ``granted_by`` provenance."""

    def setUp(self):
        self.clear_tenant_context()
        self.msp_tenant = Tenant.objects.create(name="MSP", slug="msp-mfer", is_provider=True)
        self.customer_tenant = Tenant.objects.create(
            name="Customer", slug="customer-mfer", managed_by=self.msp_tenant,
        )
        self.tenant = Tenant.objects.create(name="Corp", slug="corp-mfer")
        self.member_user = User.objects.create_user(
            username="member_mfer", email="member_mfer@x.com", password="pw",
        )
        self.superuser = User.objects.create_superuser(
            username="su_mfer", email="su_mfer@x.com", password="pw",
        )
        self.role_a = Role.objects.create(tenant=self.tenant, name="Role A", permissions=[])
        self.role_b = Role.objects.create(tenant=self.tenant, name="Role B", permissions=[])
        self.role_c = Role.objects.create(tenant=self.tenant, name="Role C", permissions=[])

        self.assignment_a = grant(self.member_user, self.tenant, self.role_a, granted_by=self.superuser)
        grant(self.member_user, self.tenant, self.role_b, granted_by=self.superuser)
        self.membership = self.assignment_a.membership

    def tearDown(self):
        self.clear_tenant_context()

    def _msp_membership_with_both_reaches(self):
        """An MSP membership carrying one role at BOTH reaches (managed = ALL)."""
        msp_role = Role.objects.create(
            tenant=self.msp_tenant, name="MSP Role", permissions=[], shared_with_managed=True,
        )
        managed_assignment = grant(
            self.member_user, self.msp_tenant, msp_role,
            reach=RoleAssignment.REACH_MANAGED, managed_scope=RoleAssignment.SCOPE_ALL,
            granted_by=self.superuser,
        )
        own_assignment = grant(
            self.member_user, self.msp_tenant, msp_role,
            reach=RoleAssignment.REACH_OWN, granted_by=self.superuser,
        )
        return managed_assignment.membership, msp_role, own_assignment, managed_assignment

    def test_unbound_edit_seeds_roles_from_both_reaches_and_reach_checkboxes(self):
        membership, msp_role, _own, _managed = self._msp_membership_with_both_reaches()
        form = MembershipForm(instance=membership, user=self.superuser)
        self.assertEqual(set(form.fields['roles'].initial), {msp_role.pk})
        self.assertTrue(form.fields['reach_own'].initial)
        self.assertTrue(form.fields['reach_managed'].initial)
        self.assertEqual(form.fields['managed_scope'].initial, RoleAssignment.SCOPE_ALL)

    def test_unbound_edit_seeds_roles_and_own_reach_from_own_only_assignments(self):
        form = MembershipForm(instance=self.membership, user=self.superuser)
        self.assertEqual(
            set(form.fields['roles'].initial), {self.role_a.pk, self.role_b.pk},
        )
        # Non-provider tenant: no Where block at all.
        self.assertNotIn('reach_own', form.fields)

    def test_edit_adds_and_removes_own_reach_rows(self):
        other_editor = User.objects.create_user(
            username="editor_mfer", email="editor_mfer@x.com", password="pw",
        )
        bound = MembershipForm(
            data={
                'user': self.member_user.pk,
                'tenant': self.tenant.pk,
                'roles': [self.role_a.pk, self.role_c.pk],
                'is_active': 'on',
            },
            instance=self.membership,
            user=other_editor,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        bound.save()
        own_role_ids = set(
            self.membership.assignments.filter(reach=RoleAssignment.REACH_OWN)
            .values_list('role_id', flat=True)
        )
        self.assertEqual(own_role_ids, {self.role_a.pk, self.role_c.pk})

    def test_edit_preserves_granted_by_on_an_unchanged_assignment(self):
        other_editor = User.objects.create_user(
            username="other_editor_mfer", email="other_editor_mfer@x.com", password="pw",
        )
        bound = MembershipForm(
            data={
                'user': self.member_user.pk,
                'tenant': self.tenant.pk,
                'roles': [self.role_a.pk],
                'is_active': 'on',
            },
            instance=self.membership,
            user=other_editor,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        bound.save()
        self.assignment_a.refresh_from_db()
        self.assertEqual(self.assignment_a.granted_by_id, self.superuser.pk)

    def test_edit_keeps_managed_rows_while_the_managed_checkbox_stays_checked(self):
        membership, msp_role, own_assignment, managed_assignment = (
            self._msp_membership_with_both_reaches()
        )
        bound = MembershipForm(
            data={
                'user': self.member_user.pk,
                'tenant': self.msp_tenant.pk,
                'roles': [msp_role.pk],
                'reach_own': 'on',
                'reach_managed': 'on',
                'managed_scope': RoleAssignment.SCOPE_ALL,
                'is_active': 'on',
            },
            instance=membership,
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        bound.save()
        managed_assignment.refresh_from_db()
        own_assignment.refresh_from_db()
        # Untouched rows survive with their provenance intact.
        self.assertEqual(managed_assignment.granted_by_id, self.superuser.pk)
        self.assertEqual(managed_assignment.managed_scope, RoleAssignment.SCOPE_ALL)

    def test_edit_unchecking_managed_reach_deletes_all_managed_rows(self):
        membership, msp_role, own_assignment, managed_assignment = (
            self._msp_membership_with_both_reaches()
        )
        bound = MembershipForm(
            data={
                'user': self.member_user.pk,
                'tenant': self.msp_tenant.pk,
                'roles': [msp_role.pk],
                'reach_own': 'on',
                # reach_managed deliberately unchecked → managed rows go away
                'is_active': 'on',
            },
            instance=membership,
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        bound.save()
        self.assertFalse(
            membership.assignments.filter(reach=RoleAssignment.REACH_MANAGED).exists()
        )
        own_assignment.refresh_from_db()  # own row untouched
        self.assertEqual(own_assignment.granted_by_id, self.superuser.pk)


class MembershipFormWhoBlockTests(TenantTestMixin, TestCase):
    """(f) The who-radio: inline user creation, get-or-create by email, and
    exactly-one-side enforcement."""

    def setUp(self):
        self.clear_tenant_context()
        self.tenant = Tenant.objects.create(name="Corp", slug="corp-mfwb")
        self.superuser = User.objects.create_superuser(
            username="su_mfwb", email="su_mfwb@x.com", password="pw",
        )

    def tearDown(self):
        self.clear_tenant_context()

    def _new_user_data(self, **overrides):
        data = {
            'who': MembershipForm.WHO_NEW,
            'tenant': self.tenant.pk,
            'new_user_email': 'fresh@x.com',
            'new_user_first_name': 'Fresh',
            'new_user_last_name': 'Hire',
            'is_active': 'on',
        }
        data.update(overrides)
        return data

    def test_new_user_is_created_with_unusable_password(self):
        bound = MembershipForm(
            data=self._new_user_data(), tenant=self.tenant, user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        membership = bound.save()
        user = membership.user
        self.assertEqual(user.email, 'fresh@x.com')
        self.assertEqual(user.username, 'fresh@x.com')
        self.assertEqual(user.first_name, 'Fresh')
        self.assertFalse(user.has_usable_password())
        self.assertTrue(bound.new_user_created)

    def test_new_user_email_is_matched_case_insensitively_and_account_reused(self):
        existing = User.objects.create_user(
            username="already", email="Already@X.com", password="pw",
        )
        bound = MembershipForm(
            data=self._new_user_data(new_user_email='already@x.com'),
            tenant=self.tenant, user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        membership = bound.save()
        self.assertEqual(membership.user_id, existing.pk)
        self.assertFalse(bound.new_user_created)
        # The reused account keeps its password and its name.
        existing.refresh_from_db()
        self.assertTrue(existing.has_usable_password())

    def test_new_user_side_requires_email_and_names(self):
        bound = MembershipForm(
            data=self._new_user_data(
                new_user_email='', new_user_first_name='', new_user_last_name='',
            ),
            tenant=self.tenant, user=self.superuser,
        )
        self.assertFalse(bound.is_valid())
        for fname in ('new_user_email', 'new_user_first_name', 'new_user_last_name'):
            self.assertIn(fname, bound.errors)

    def test_existing_side_requires_a_user_pick(self):
        bound = MembershipForm(
            data={
                'who': MembershipForm.WHO_EXISTING,
                'tenant': self.tenant.pk,
                'is_active': 'on',
            },
            tenant=self.tenant, user=self.superuser,
        )
        self.assertFalse(bound.is_valid())
        self.assertIn('user', bound.errors)

    def test_new_side_ignores_a_leftover_user_pick(self):
        # The JS toggle only hides the other side — its inputs still POST. The
        # server must use ONLY the selected side.
        leftover = User.objects.create_user(
            username="leftover_mfwb", email="leftover_mfwb@x.com", password="pw",
        )
        bound = MembershipForm(
            data=self._new_user_data(user=leftover.pk),
            tenant=self.tenant, user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        membership = bound.save()
        self.assertNotEqual(membership.user_id, leftover.pk)
        self.assertEqual(membership.user.email, 'fresh@x.com')

    def test_duplicate_membership_via_email_match_is_rejected(self):
        existing = User.objects.create_user(
            username="dupe_mfwb", email="dupe_mfwb@x.com", password="pw",
        )
        Membership.objects.create(user=existing, tenant=self.tenant, is_active=True)
        bound = MembershipForm(
            data=self._new_user_data(new_user_email='dupe_mfwb@x.com'),
            tenant=self.tenant, user=self.superuser,
        )
        self.assertFalse(bound.is_valid())
        self.assertIn('new_user_email', bound.errors)
        self.assertEqual(
            Membership.objects.filter(user=existing, tenant=self.tenant).count(), 1,
        )

    def test_who_block_is_absent_on_edit(self):
        member = User.objects.create_user(
            username="edit_mfwb", email="edit_mfwb@x.com", password="pw",
        )
        membership = Membership.objects.create(user=member, tenant=self.tenant, is_active=True)
        form = MembershipForm(instance=membership, user=self.superuser)
        for fname in ('who', 'new_user_email', 'new_user_first_name', 'new_user_last_name'):
            self.assertNotIn(fname, form.fields)


class MembershipFormSharedRoleLabelTests(TenantTestMixin, TestCase):
    """(g) Shared-in definitions are labelled "(from <provider>)" in the picker;
    the tenant's own roles keep their bare name."""

    def setUp(self):
        self.clear_tenant_context()
        self.msp = Tenant.objects.create(name="Prime MSP", slug="msp-mfrl", is_provider=True)
        self.customer = Tenant.objects.create(
            name="Customer", slug="customer-mfrl", managed_by=self.msp,
        )
        self.superuser = User.objects.create_superuser(
            username="su_mfrl", email="su_mfrl@x.com", password="pw",
        )
        self.own_role = Role.objects.create(
            tenant=self.customer, name="Local Editor", permissions=[],
        )
        self.shared_role = Role.objects.create(
            tenant=self.msp, name="Technician", permissions=[], shared_with_managed=True,
        )
        self.unshared_msp_role = Role.objects.create(
            tenant=self.msp, name="MSP Internal", permissions=[],
        )

    def tearDown(self):
        self.clear_tenant_context()

    def test_picker_offers_own_plus_shared_in_roles_with_provider_labels(self):
        form = MembershipForm(tenant=self.customer, user=self.superuser)
        labels = {pk: str(label) for pk, label in form.fields['roles'].choices}
        self.assertIn(self.own_role.pk, labels)
        self.assertIn(self.shared_role.pk, labels)
        self.assertNotIn(self.unshared_msp_role.pk, labels)
        self.assertEqual(labels[self.own_role.pk], "Local Editor")
        self.assertIn("Technician", labels[self.shared_role.pk])
        self.assertIn("Prime MSP", labels[self.shared_role.pk])
