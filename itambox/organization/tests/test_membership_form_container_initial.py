"""Tests for the stage-3 unified ``MembershipForm`` — the lossless "Add member" flow.

The form authors the whole grant in one screen
(``organization/forms/membership_form.py``):

  * **Who** (create only) — radio "Existing user" (user select) vs. "New user"
    (email + first/last). ``clean()`` enforces exactly one side; a new user is
    get-or-created by email with ``set_unusable_password()``.
  * **This organization** — an ``own_roles`` multi-select; each selected role maps
    to one ``RoleAssignment(reach='own')``.
  * **Managed tenants** — only on a managing (``is_provider``) tenant: a formset,
    ONE ROW PER managed grant (role + its own coverage refinement). Each row maps
    to one ``RoleAssignment(reach='managed')``.
  * **Edit** reconciles per instance: surviving rows keep their ``granted_by``
    provenance; only deselected/removed rows are deleted; only new rows created.

Covers (a)–(g): tenant context governs create, the real view path, the managed
formset only on provider tenants, save writes the right rows, edit reconciles both
reaches independently, the who-block, and shared-role labelling.
"""
from django import forms
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.tests.mixins import TenantTestMixin, grant
from organization.models import Tenant, Membership, Role, RoleAssignment
from organization.forms.membership_form import MembershipForm

from ._membership_form_helpers import membership_post_data

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
            data=membership_post_data(user=self.member_user.pk, tenant=str(self.tenant.pk)),
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
        response = self.client.post(url, data=membership_post_data(
            user=self.staff_user.pk, tenant=self.tenant.pk,
        ))
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


class MembershipFormManagedBlockTests(TenantTestMixin, TestCase):
    """(c) The Managed-tenants formset renders only when the membership's tenant is
    a managing (``is_provider``) tenant; ``own_roles`` always renders."""

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

    def test_provider_tenant_offers_the_managed_formset_and_own_roles(self):
        form = MembershipForm(tenant=self.msp_tenant, user=self.superuser)
        self.assertIn('own_roles', form.fields)
        self.assertIsNotNone(form.managed_formset)

    def test_non_provider_tenant_drops_the_managed_formset(self):
        for tenant in (self.customer_tenant, self.standalone_tenant):
            form = MembershipForm(tenant=tenant, user=self.superuser)
            self.assertIn('own_roles', form.fields)
            self.assertIsNone(form.managed_formset)

    def test_context_free_create_offers_the_managed_formset_pending_the_tenant_pick(self):
        # Unknown tenant (no context, unbound form): the formset stays available so
        # it can render; clean()/the row forms re-validate once a real tenant posts.
        form = MembershipForm(user=self.superuser)
        self.assertIsNotNone(form.managed_formset)

    def test_tampered_non_provider_tenant_is_rejected_at_the_tenant_field(self):
        # A context-bound create restricts the tenant field's queryset to the
        # context tenant, so a tampered hidden input posting another tenant fails
        # field validation — it can never reach (or write) managed grants.
        member_user = User.objects.create_user(
            username="member_mfrb_tamper", email="member_mfrb_tamper@x.com", password="pw",
        )
        bound = MembershipForm(
            data=membership_post_data(
                user=member_user.pk,
                tenant=self.standalone_tenant.pk,  # tampered: not the context tenant
                managed=[{'role': '', 'managed_scope': RoleAssignment.SCOPE_ALL}],
            ),
            tenant=self.msp_tenant,
            user=self.superuser,
        )
        self.assertFalse(bound.is_valid())
        self.assertIn('tenant', bound.errors)
        self.assertFalse(Membership.objects.filter(user=member_user).exists())

    def test_own_reach_is_implied_for_a_non_provider_tenant(self):
        member_user = User.objects.create_user(
            username="member_mfrb_own", email="member_mfrb_own@x.com", password="pw",
        )
        role = Role.objects.create(tenant=self.customer_tenant, name="Local Role", permissions=[])
        bound = MembershipForm(
            data=membership_post_data(
                user=member_user.pk, tenant=self.customer_tenant.pk, own_roles=[role.pk],
            ),
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


class MembershipFormSaveCreatesAssignmentsTests(TenantTestMixin, TestCase):
    """(d) ``save()`` writes one ``RoleAssignment`` row per own role and per managed
    formset row, stamped with the acting (granting) user."""

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
            data=membership_post_data(
                user=self.member_user.pk, tenant=self.tenant.pk, own_roles=[self.role.pk],
            ),
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
            data=membership_post_data(
                user=self.member_user.pk, tenant=self.msp_tenant.pk,
                managed=[{'role': self.msp_role.pk, 'managed_scope': RoleAssignment.SCOPE_ALL}],
            ),
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

    def test_both_reaches_write_two_rows_for_one_role(self):
        bound = MembershipForm(
            data=membership_post_data(
                user=self.member_user.pk, tenant=self.msp_tenant.pk,
                own_roles=[self.msp_role.pk],
                managed=[{'role': self.msp_role.pk, 'managed_scope': RoleAssignment.SCOPE_ALL}],
            ),
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
            data=membership_post_data(user=self.member_user.pk, tenant=self.tenant.pk),
            tenant=self.tenant,
            user=self.superuser,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        membership = bound.save()
        self.assertEqual(membership.assignments.count(), 0)


class MembershipFormEditReconcilesBothReachesTests(TenantTestMixin, TestCase):
    """(e) Editing reconciles ``RoleAssignment`` rows at BOTH reaches independently:
    deselected own roles lose their rows, removed managed rows are deleted, and
    untouched rows keep their ``granted_by`` provenance."""

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

    def test_unbound_edit_seeds_own_roles_and_managed_formset_from_both_reaches(self):
        membership, msp_role, _own, _managed = self._msp_membership_with_both_reaches()
        form = MembershipForm(instance=membership, user=self.superuser)
        self.assertEqual(set(form.fields['own_roles'].initial), {msp_role.pk})
        seeded = [row for row in form.managed_formset.initial if row.get('role') == msp_role.pk]
        self.assertEqual(len(seeded), 1)
        self.assertEqual(seeded[0]['managed_scope'], RoleAssignment.SCOPE_ALL)

    def test_unbound_edit_seeds_own_roles_from_own_only_assignments(self):
        form = MembershipForm(instance=self.membership, user=self.superuser)
        self.assertEqual(
            set(form.fields['own_roles'].initial), {self.role_a.pk, self.role_b.pk},
        )
        # Non-provider tenant: no managed formset at all.
        self.assertIsNone(form.managed_formset)

    def test_edit_adds_and_removes_own_reach_rows(self):
        other_editor = User.objects.create_user(
            username="editor_mfer", email="editor_mfer@x.com", password="pw",
        )
        bound = MembershipForm(
            data=membership_post_data(
                user=self.member_user.pk, tenant=self.tenant.pk,
                own_roles=[self.role_a.pk, self.role_c.pk],
            ),
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
            data=membership_post_data(
                user=self.member_user.pk, tenant=self.tenant.pk, own_roles=[self.role_a.pk],
            ),
            instance=self.membership,
            user=other_editor,
        )
        self.assertTrue(bound.is_valid(), bound.errors.as_json())
        bound.save()
        self.assignment_a.refresh_from_db()
        self.assertEqual(self.assignment_a.granted_by_id, self.superuser.pk)

    def test_edit_keeps_a_resubmitted_managed_row_with_its_provenance(self):
        membership, msp_role, own_assignment, managed_assignment = (
            self._msp_membership_with_both_reaches()
        )
        bound = MembershipForm(
            data=membership_post_data(
                user=self.member_user.pk, tenant=self.msp_tenant.pk,
                own_roles=[msp_role.pk],
                managed=[{
                    'id': managed_assignment.pk, 'role': msp_role.pk,
                    'managed_scope': RoleAssignment.SCOPE_ALL,
                }],
            ),
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

    def test_edit_removing_a_managed_row_deletes_only_that_grant(self):
        membership, msp_role, own_assignment, managed_assignment = (
            self._msp_membership_with_both_reaches()
        )
        bound = MembershipForm(
            data=membership_post_data(
                user=self.member_user.pk, tenant=self.msp_tenant.pk,
                own_roles=[msp_role.pk],
                managed=[{
                    'id': managed_assignment.pk, 'role': msp_role.pk,
                    'managed_scope': RoleAssignment.SCOPE_ALL, 'delete': True,
                }],
            ),
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
        defaults = {
            'who': MembershipForm.WHO_NEW,
            'tenant': self.tenant.pk,
            'new_user_email': 'fresh@x.com',
            'new_user_first_name': 'Fresh',
            'new_user_last_name': 'Hire',
        }
        defaults.update(overrides)
        return membership_post_data(**defaults)

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
            data=membership_post_data(who=MembershipForm.WHO_EXISTING, tenant=self.tenant.pk),
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
        labels = {pk: str(label) for pk, label in form.fields['own_roles'].choices}
        self.assertIn(self.own_role.pk, labels)
        self.assertIn(self.shared_role.pk, labels)
        self.assertNotIn(self.unshared_msp_role.pk, labels)
        self.assertEqual(labels[self.own_role.pk], "Local Editor")
        self.assertIn("Technician", labels[self.shared_role.pk])
        self.assertIn("Prime MSP", labels[self.shared_role.pk])
