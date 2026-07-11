"""Tests for the Stage-2 ``MembershipForm`` successor to the deleted
tenant-XOR-provider container-initial logic.

Post-collapse world: ``organization.Provider`` is gone. A membership has exactly
one container — ``Membership.tenant`` — so the old "which hidden field did the
stale ``self.initial`` entry leak into" bug class is structurally impossible:
there is only one container field left, not two competing ones. What replaced it
in ``organization/forms/membership_form.py`` (``MembershipForm``):

  * the membership's tenant comes from the ``tenant=`` context kwarg on create
    (``MembershipCreateView`` resolves it from ``?tenant=`` or the active
    tenant); the tenant field is locked on edit;
  * a "reach" block (own vs. managed + refinement fields) is offered only when
    the membership's tenant is a managing (``is_provider``) tenant — elsewhere
    it collapses to "own" and hides;
  * ``save()`` writes one ``RoleAssignment`` row per selected role, reach, and
    refinement, stamped with ``granted_by=`` the acting (requesting) user;
  * editing an existing membership reconciles only the ``RoleAssignment`` rows
    at the *selected* reach (adds newly-selected roles, deletes deselected
    ones) and never touches rows at the other reach.

Covers:
  (a) the tenant context kwarg governs the create flow, including the case
      where a stale/mismatched ``initial`` dict entry is also present.
  (b) the real view path: ``MembershipCreateView`` with ``?tenant=<pk>`` —
      mirrors ``get_initial()``/``get_form_kwargs()`` end to end.
  (c) the reach block only renders (and only accepts "managed") when the
      membership's tenant ``is_provider``; a defense-in-depth check in
      ``clean()`` re-validates against whatever tenant was *actually posted*,
      since the ``tenant`` field's queryset is intentionally unrestricted (a
      create-context ``tenant=`` kwarg only sets the initial + hides the
      widget — it does not narrow the queryset).
  (d) ``save()`` creates ``RoleAssignment`` rows (own and managed reach) with
      ``granted_by`` set to the acting user.
  (e) editing reconciles own-reach assignment rows (add + remove), preserves
      ``granted_by`` on rows left unchanged, and never touches managed-reach
      rows on the same membership.
"""
from django import forms
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.tests.mixins import TenantTestMixin, grant
from organization.models import Tenant, Membership, Role, RoleAssignment
from organization.forms.membership_form import MembershipForm

User = get_user_model()


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

    def test_tenant_get_param_flow_is_submittable(self):
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


class MembershipFormReachBlockTests(TenantTestMixin, TestCase):
    """(c) The managed-reach choice + refinement fields render only when the
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

    def test_provider_tenant_offers_managed_reach_and_refinement_fields(self):
        form = MembershipForm(tenant=self.msp_tenant, user=self.superuser)
        choice_values = [c[0] for c in form.fields['reach'].choices]
        self.assertIn(RoleAssignment.REACH_MANAGED, choice_values)
        self.assertNotIsInstance(form.fields['reach'].widget, forms.HiddenInput)
        for fname in ('managed_scope', 'scope_group', 'assigned_tenants'):
            self.assertIn(fname, form.fields)

    def test_non_provider_tenant_hides_managed_reach_and_drops_refinement_fields(self):
        for tenant in (self.customer_tenant, self.standalone_tenant):
            form = MembershipForm(tenant=tenant, user=self.superuser)
            choice_values = [c[0] for c in form.fields['reach'].choices]
            self.assertEqual(choice_values, [RoleAssignment.REACH_OWN])
            self.assertIsInstance(form.fields['reach'].widget, forms.HiddenInput)
            for fname in ('managed_scope', 'scope_group', 'assigned_tenants'):
                self.assertNotIn(fname, form.fields)

    def test_context_free_create_offers_managed_reach_pending_the_tenant_pick(self):
        # Unknown tenant (no context, unbound form): the block stays available
        # so it can render; clean() re-validates once a real tenant is submitted.
        form = MembershipForm(user=self.superuser)
        choice_values = [c[0] for c in form.fields['reach'].choices]
        self.assertIn(RoleAssignment.REACH_MANAGED, choice_values)

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
                # the reach field's *choices* were computed off the context tenant.
                'tenant': self.standalone_tenant.pk,
                'roles': [],
                'reach': RoleAssignment.REACH_MANAGED,
                'managed_scope': RoleAssignment.SCOPE_ALL,
                'is_active': 'on',
            },
            tenant=self.msp_tenant,
            user=self.superuser,
        )
        self.assertFalse(bound.is_valid())
        self.assertIn('reach', bound.errors)

    def test_own_reach_is_always_valid_for_a_non_provider_tenant(self):
        member_user = User.objects.create_user(
            username="member_mfrb_own", email="member_mfrb_own@x.com", password="pw",
        )
        role = Role.objects.create(tenant=self.customer_tenant, name="Local Role", permissions=[])
        bound = MembershipForm(
            data={
                'user': member_user.pk,
                'tenant': self.customer_tenant.pk,
                'roles': [role.pk],
                'reach': RoleAssignment.REACH_OWN,
                'is_active': 'on',
            },
            tenant=self.customer_tenant,
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())


class MembershipFormSaveCreatesAssignmentsTests(TenantTestMixin, TestCase):
    """(d) ``save()`` writes one ``RoleAssignment`` row per selected role, stamped
    with the acting (granting) user."""

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
                'reach': RoleAssignment.REACH_OWN,
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
                'reach': RoleAssignment.REACH_MANAGED,
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

    def test_no_roles_selected_creates_membership_without_assignments(self):
        bound = MembershipForm(
            data={'user': self.member_user.pk, 'tenant': self.tenant.pk, 'is_active': 'on'},
            tenant=self.tenant,
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        membership = bound.save()
        self.assertEqual(membership.assignments.count(), 0)


class MembershipFormEditReconcilesOwnReachAssignmentsTests(TenantTestMixin, TestCase):
    """(e) Editing a membership adds/removes ``reach='own'`` assignment rows to
    match the submitted role selection, preserves ``granted_by`` on rows left
    unchanged, and never touches the membership's managed-reach rows."""

    def setUp(self):
        self.clear_tenant_context()
        self.msp_tenant = Tenant.objects.create(name="MSP", slug="msp-mfer", is_provider=True)
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

    def test_unbound_edit_seeds_roles_and_reach_from_existing_own_assignments(self):
        form = MembershipForm(instance=self.membership, user=self.superuser)
        self.assertEqual(
            set(form.fields['roles'].initial), {self.role_a.pk, self.role_b.pk},
        )
        self.assertEqual(form.fields['reach'].initial, RoleAssignment.REACH_OWN)

    def test_edit_adds_and_removes_own_reach_rows(self):
        other_editor = User.objects.create_user(
            username="editor_mfer", email="editor_mfer@x.com", password="pw",
        )
        bound = MembershipForm(
            data={
                'user': self.member_user.pk,
                'tenant': self.tenant.pk,
                'roles': [self.role_a.pk, self.role_c.pk],
                'reach': RoleAssignment.REACH_OWN,
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
                'reach': RoleAssignment.REACH_OWN,
                'is_active': 'on',
            },
            instance=self.membership,
            user=other_editor,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        bound.save()
        self.assignment_a.refresh_from_db()
        self.assertEqual(self.assignment_a.granted_by_id, self.superuser.pk)

    def test_edit_never_touches_managed_reach_rows(self):
        managed_role = Role.objects.create(
            tenant=self.msp_tenant, name="MSP Role", permissions=[], shared_with_managed=True,
        )
        managed_assignment = grant(
            self.member_user, self.msp_tenant, managed_role,
            reach=RoleAssignment.REACH_MANAGED, managed_scope=RoleAssignment.SCOPE_ALL,
            granted_by=self.superuser,
        )
        msp_membership = managed_assignment.membership

        bound = MembershipForm(
            data={
                'user': self.member_user.pk,
                'tenant': self.msp_tenant.pk,
                'roles': [],
                'reach': RoleAssignment.REACH_OWN,
                'is_active': 'on',
            },
            instance=msp_membership,
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        bound.save()
        managed_assignment.refresh_from_db()  # must survive: reach='own' sync never touches it
        self.assertEqual(managed_assignment.reach, RoleAssignment.REACH_MANAGED)
        self.assertEqual(managed_assignment.managed_scope, RoleAssignment.SCOPE_ALL)
