"""WS1 regression suite — the lossless membership grant editor.

The persisted model (``RoleAssignment``: one row per membership × role × reach,
each managed row with its own coverage refinement) is richer than the old
single-role-set + one-refinement form could represent. These tests pin the
non-negotiable outcomes of ``RBAC_STAGE3_POST_REVIEW_FIX_PLAN.md`` §1:

  * opening and re-submitting an untouched membership produces ZERO grant changes
    (same PKs, reaches, scopes, ``granted_by``, ``granted_at``);
  * every ``(role, reach)`` row and every managed refinement is independently
    representable and round-trips unchanged;
  * changing one managed row leaves its siblings untouched;
  * own and managed rows are added/removed independently;
  * duplicate managed rows for one role are rejected;
  * every new/changed row passes the escalation guard, and one bad row fails the
    whole transaction.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from core.tests.mixins import TenantTestMixin, grant
from organization.models import Membership, Tenant, Role, RoleAssignment, TenantGroup
from organization.forms.membership_form import MembershipForm

from ._membership_form_helpers import membership_post_data

User = get_user_model()


def _fingerprint(assignment):
    """The audit-relevant identity of a grant row (provenance + timestamps)."""
    return (
        assignment.pk, assignment.role_id, assignment.reach,
        assignment.managed_scope, assignment.scope_group_id,
        assignment.granted_by_id, assignment.granted_at,
        tuple(sorted(assignment.assigned_tenants.values_list('pk', flat=True))),
    )


def _fingerprints(membership):
    return {a.pk: _fingerprint(a) for a in membership.assignments.all()}


class AsymmetricRoundTripTests(TenantTestMixin, TestCase):
    """Outcomes 1 & 2: an asymmetric membership round-trips with zero changes."""

    def setUp(self):
        self.clear_tenant_context()
        self.msp = Tenant.objects.create(name="MSP RT", slug="msp-rt", is_provider=True)
        self.cust_a = Tenant.objects.create(name="Cust A", slug="cust-a-rt", managed_by=self.msp)
        self.cust_b = Tenant.objects.create(name="Cust B", slug="cust-b-rt", managed_by=self.msp)
        self.granter = User.objects.create_superuser(
            username="rt_granter", email="rt_granter@x.com", password="pw",
        )
        self.editor = User.objects.create_superuser(
            username="rt_editor", email="rt_editor@x.com", password="pw",
        )
        self.member = User.objects.create_user(
            username="rt_member", email="rt_member@x.com", password="pw",
        )
        self.role_a = Role.objects.create(tenant=self.msp, name="Role A", permissions=[])
        self.role_b = Role.objects.create(tenant=self.msp, name="Role B", permissions=[])
        # Asymmetric: Role A own-only; Role B managed-only (explicit → cust_a).
        self.own = grant(
            self.member, self.msp, self.role_a,
            reach=RoleAssignment.REACH_OWN, granted_by=self.granter,
        )
        self.managed = grant(
            self.member, self.msp, self.role_b,
            reach=RoleAssignment.REACH_MANAGED, managed_scope=RoleAssignment.SCOPE_EXPLICIT,
            granted_by=self.granter, assigned_tenants=[self.cust_a],
        )
        self.membership = self.own.membership

    def tearDown(self):
        self.clear_tenant_context()

    def _roundtrip_post(self):
        return membership_post_data(
            user=self.member.pk, tenant=self.msp.pk,
            own_roles=[self.role_a.pk],
            managed=[{
                'id': self.managed.pk, 'role': self.role_b.pk,
                'managed_scope': RoleAssignment.SCOPE_EXPLICIT,
                'assigned_tenants': [self.cust_a.pk],
            }],
        )

    def test_unbound_edit_seeds_both_reaches_independently(self):
        form = MembershipForm(instance=self.membership, user=self.editor)
        self.assertEqual(set(form.fields['own_roles'].initial), {self.role_a.pk})
        rows = [r for r in form.managed_formset.initial if r.get('role')]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['role'], self.role_b.pk)
        self.assertEqual(rows[0]['managed_scope'], RoleAssignment.SCOPE_EXPLICIT)
        self.assertEqual(rows[0]['assigned_tenants'], [self.cust_a.pk])

    def test_unchanged_submit_is_a_total_no_op(self):
        before = _fingerprints(self.membership)
        form = MembershipForm(
            data=self._roundtrip_post(), instance=self.membership, user=self.editor,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        after = _fingerprints(self.membership)
        # Same rows, same PKs, same provenance, same timestamps, same explicit scope.
        self.assertEqual(before, after)

    def test_same_role_at_both_reaches_round_trips_as_two_rows(self):
        # Add an own-reach grant for Role B too (same role, two reaches → two rows).
        own_b = grant(
            self.member, self.msp, self.role_b,
            reach=RoleAssignment.REACH_OWN, granted_by=self.granter,
        )
        before = _fingerprints(self.membership)
        form = MembershipForm(
            data=membership_post_data(
                user=self.member.pk, tenant=self.msp.pk,
                own_roles=[self.role_a.pk, self.role_b.pk],
                managed=[{
                    'id': self.managed.pk, 'role': self.role_b.pk,
                    'managed_scope': RoleAssignment.SCOPE_EXPLICIT,
                    'assigned_tenants': [self.cust_a.pk],
                }],
            ),
            instance=self.membership, user=self.editor,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        self.assertEqual(_fingerprints(self.membership), before)
        self.assertEqual(
            self.membership.assignments.filter(role=self.role_b).count(), 2,
        )
        own_b.refresh_from_db()  # still there, untouched


class HeterogeneousManagedRoundTripTests(TenantTestMixin, TestCase):
    """Outcomes 3 & 4: two managed rows with DIFFERENT refinements round-trip, and
    changing one never touches the other."""

    def setUp(self):
        self.clear_tenant_context()
        self.msp = Tenant.objects.create(name="MSP Het", slug="msp-het", is_provider=True)
        self.group = TenantGroup.objects.create(name="Region West", slug="region-west-het")
        self.cust_a = Tenant.objects.create(name="Cust A", slug="cust-a-het", managed_by=self.msp)
        self.cust_grp = Tenant.objects.create(
            name="Cust Grp", slug="cust-grp-het", managed_by=self.msp, group=self.group,
        )
        self.granter = User.objects.create_superuser(
            username="het_granter", email="het_granter@x.com", password="pw",
        )
        self.member = User.objects.create_user(
            username="het_member", email="het_member@x.com", password="pw",
        )
        self.role_x = Role.objects.create(tenant=self.msp, name="Role X", permissions=[])
        self.role_y = Role.objects.create(tenant=self.msp, name="Role Y", permissions=[])
        # Role X → explicit (cust_a); Role Y → tenant_group (Region West).
        self.mx = grant(
            self.member, self.msp, self.role_x, reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT, granted_by=self.granter,
            assigned_tenants=[self.cust_a],
        )
        self.my = grant(
            self.member, self.msp, self.role_y, reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_TENANT_GROUP, scope_group=self.group,
            granted_by=self.granter,
        )
        self.membership = self.mx.membership

    def tearDown(self):
        self.clear_tenant_context()

    def _both_rows(self, x_overrides=None, y_overrides=None):
        x = {
            'id': self.mx.pk, 'role': self.role_x.pk,
            'managed_scope': RoleAssignment.SCOPE_EXPLICIT,
            'assigned_tenants': [self.cust_a.pk],
        }
        y = {
            'id': self.my.pk, 'role': self.role_y.pk,
            'managed_scope': RoleAssignment.SCOPE_TENANT_GROUP,
            'scope_group': self.group.pk,
        }
        x.update(x_overrides or {})
        y.update(y_overrides or {})
        return [x, y]

    def test_heterogeneous_managed_rows_round_trip_unchanged(self):
        before = _fingerprints(self.membership)
        form = MembershipForm(
            data=membership_post_data(
                user=self.member.pk, tenant=self.msp.pk, managed=self._both_rows(),
            ),
            instance=self.membership, user=User.objects.create_superuser(
                username="het_su", email="het_su@x.com", password="pw",
            ),
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        self.assertEqual(_fingerprints(self.membership), before)

    def test_changing_one_managed_row_leaves_the_sibling_untouched(self):
        y_before = _fingerprint(self.my)
        # Change only Role X: explicit(cust_a) → ALL.
        form = MembershipForm(
            data=membership_post_data(
                user=self.member.pk, tenant=self.msp.pk,
                managed=self._both_rows(
                    x_overrides={'managed_scope': RoleAssignment.SCOPE_ALL, 'assigned_tenants': None},
                ),
            ),
            instance=self.membership, user=User.objects.create_superuser(
                username="het_su2", email="het_su2@x.com", password="pw",
            ),
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        self.mx.refresh_from_db()
        self.my.refresh_from_db()
        # X updated in place (same row, new scope, no explicit tenants).
        self.assertEqual(self.mx.managed_scope, RoleAssignment.SCOPE_ALL)
        self.assertEqual(self.mx.assigned_tenants.count(), 0)
        # Y completely untouched — same fingerprint (incl. granted_at).
        self.assertEqual(_fingerprint(self.my), y_before)


class ManagedFormsetRulesTests(TenantTestMixin, TestCase):
    """Duplicate rejection, independent add/remove, and the one-bad-row rule."""

    def setUp(self):
        self.clear_tenant_context()
        self.msp = Tenant.objects.create(name="MSP Rules", slug="msp-rules", is_provider=True)
        self.cust_a = Tenant.objects.create(name="RA", slug="ra-rules", managed_by=self.msp)
        self.cust_b = Tenant.objects.create(name="RB", slug="rb-rules", managed_by=self.msp)
        self.su = User.objects.create_superuser(
            username="rules_su", email="rules_su@x.com", password="pw",
        )
        self.member = User.objects.create_user(
            username="rules_member", email="rules_member@x.com", password="pw",
        )
        self.role_a = Role.objects.create(tenant=self.msp, name="RRole A", permissions=[])
        self.role_b = Role.objects.create(tenant=self.msp, name="RRole B", permissions=[])

    def tearDown(self):
        self.clear_tenant_context()

    def test_duplicate_managed_rows_for_one_role_are_rejected(self):
        form = MembershipForm(
            data=membership_post_data(
                user=self.member.pk, tenant=self.msp.pk,
                managed=[
                    {'role': self.role_a.pk, 'managed_scope': RoleAssignment.SCOPE_ALL},
                    {'role': self.role_a.pk, 'managed_scope': RoleAssignment.SCOPE_EXPLICIT,
                     'assigned_tenants': [self.cust_a.pk]},
                ],
            ),
            tenant=self.msp, user=self.su,
        )
        self.assertFalse(form.is_valid())
        self.assertTrue(form.managed_formset.non_form_errors())

    def test_add_own_and_managed_rows_independently(self):
        membership = grant(
            self.member, self.msp, self.role_a, reach=RoleAssignment.REACH_OWN,
            granted_by=self.su,
        ).membership
        form = MembershipForm(
            data=membership_post_data(
                user=self.member.pk, tenant=self.msp.pk,
                own_roles=[self.role_a.pk],
                managed=[{'role': self.role_b.pk, 'managed_scope': RoleAssignment.SCOPE_ALL}],
            ),
            instance=membership, user=self.su,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        self.assertTrue(membership.assignments.filter(
            role=self.role_a, reach=RoleAssignment.REACH_OWN).exists())
        self.assertTrue(membership.assignments.filter(
            role=self.role_b, reach=RoleAssignment.REACH_MANAGED).exists())

    def test_one_invalid_managed_row_fails_the_whole_transaction(self):
        # Role B row is fine; Role A row is explicit-scope with NO tenants → invalid.
        membership_before = grant(
            self.member, self.msp, self.role_a, reach=RoleAssignment.REACH_OWN,
            granted_by=self.su,
        ).membership
        managed_before = membership_before.assignments.filter(
            reach=RoleAssignment.REACH_MANAGED).count()
        form = MembershipForm(
            data=membership_post_data(
                user=self.member.pk, tenant=self.msp.pk,
                managed=[
                    {'role': self.role_b.pk, 'managed_scope': RoleAssignment.SCOPE_ALL},
                    {'role': self.role_a.pk, 'managed_scope': RoleAssignment.SCOPE_EXPLICIT},
                ],
            ),
            instance=membership_before, user=self.su,
        )
        self.assertFalse(form.is_valid())
        # Nothing written — not even the valid sibling row.
        self.assertEqual(
            membership_before.assignments.filter(reach=RoleAssignment.REACH_MANAGED).count(),
            managed_before,
        )

    def test_role_less_membership_is_supported(self):
        form = MembershipForm(
            data=membership_post_data(user=self.member.pk, tenant=self.msp.pk),
            tenant=self.msp, user=self.su,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        membership = form.save()
        self.assertEqual(membership.assignments.count(), 0)


class ManagedRoleChangeTests(TenantTestMixin, TestCase):
    """RC-blocker regression: editing an existing managed row's ROLE must persist.

    The reconciler used to update only the scope fields of a surviving row, so a
    role change validated, "saved", and silently left the old role granted. A
    role change is a revoke plus a fresh grant: the old row dies, a new row
    carries the new role under the acting user's provenance, and the refinement
    plus sibling rows survive verbatim.
    """

    def setUp(self):
        self.clear_tenant_context()
        self.msp = Tenant.objects.create(name="MSP RC", slug="msp-rc", is_provider=True)
        self.group = TenantGroup.objects.create(name="RC Region", slug="rc-region")
        self.cust_a = Tenant.objects.create(name="RC A", slug="rc-a", managed_by=self.msp)
        self.cust_g = Tenant.objects.create(
            name="RC G", slug="rc-g", managed_by=self.msp, group=self.group,
        )
        self.granter = User.objects.create_superuser(
            username="rc_granter", email="rc_granter@x.com", password="pw",
        )
        self.editor = User.objects.create_superuser(
            username="rc_editor", email="rc_editor@x.com", password="pw",
        )
        self.member = User.objects.create_user(
            username="rc_member", email="rc_member@x.com", password="pw",
        )
        self.role_x = Role.objects.create(tenant=self.msp, name="RC Role X", permissions=[])
        self.role_y = Role.objects.create(tenant=self.msp, name="RC Role Y", permissions=[])
        self.role_z = Role.objects.create(tenant=self.msp, name="RC Role Z", permissions=[])
        self.own = grant(
            self.member, self.msp, self.role_z,
            reach=RoleAssignment.REACH_OWN, granted_by=self.granter,
        )
        self.mx = grant(
            self.member, self.msp, self.role_x, reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_EXPLICIT, granted_by=self.granter,
            assigned_tenants=[self.cust_a],
        )
        self.membership = self.mx.membership

    def tearDown(self):
        self.clear_tenant_context()

    def _managed_rows(self, *rows):
        return membership_post_data(
            user=self.member.pk, tenant=self.msp.pk,
            own_roles=[self.role_z.pk], managed=list(rows),
        )

    def test_role_change_is_persisted_as_revoke_plus_grant(self):
        own_before = _fingerprint(self.own)
        form = MembershipForm(
            data=self._managed_rows({
                'id': self.mx.pk, 'role': self.role_y.pk,
                'managed_scope': RoleAssignment.SCOPE_EXPLICIT,
                'assigned_tenants': [self.cust_a.pk],
            }),
            instance=self.membership, user=self.editor,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        managed = self.membership.assignments.filter(reach=RoleAssignment.REACH_MANAGED)
        # The requested role IS what the database now grants (the RC probe).
        self.assertEqual(
            list(managed.values_list('role_id', flat=True)), [self.role_y.pk],
        )
        row = managed.get()
        # A fresh row under the acting user's provenance, not a mutated old one.
        self.assertNotEqual(row.pk, self.mx.pk)
        self.assertEqual(row.granted_by_id, self.editor.pk)
        # The refinement travelled onto the new grant.
        self.assertEqual(row.managed_scope, RoleAssignment.SCOPE_EXPLICIT)
        self.assertEqual(
            set(row.assigned_tenants.values_list('pk', flat=True)), {self.cust_a.pk},
        )
        # The own-reach sibling is untouched.
        self.own.refresh_from_db()
        self.assertEqual(_fingerprint(self.own), own_before)

    def test_role_change_preserves_group_scope_and_sibling_row(self):
        my = grant(
            self.member, self.msp, self.role_y, reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_TENANT_GROUP, scope_group=self.group,
            granted_by=self.granter,
        )
        mx_before = _fingerprint(self.mx)
        form = MembershipForm(
            data=self._managed_rows(
                {
                    'id': self.mx.pk, 'role': self.role_x.pk,
                    'managed_scope': RoleAssignment.SCOPE_EXPLICIT,
                    'assigned_tenants': [self.cust_a.pk],
                },
                {
                    'id': my.pk, 'role': self.role_z.pk,
                    'managed_scope': RoleAssignment.SCOPE_TENANT_GROUP,
                    'scope_group': self.group.pk,
                },
            ),
            instance=self.membership, user=self.editor,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        managed = self.membership.assignments.filter(reach=RoleAssignment.REACH_MANAGED)
        self.assertEqual(
            set(managed.values_list('role_id', flat=True)),
            {self.role_x.pk, self.role_z.pk},
        )
        new_row = managed.get(role=self.role_z)
        self.assertEqual(new_row.managed_scope, RoleAssignment.SCOPE_TENANT_GROUP)
        self.assertEqual(new_row.scope_group_id, self.group.pk)
        self.assertEqual(new_row.granted_by_id, self.editor.pk)
        # The untouched sibling keeps its identity (pk, provenance, timestamps).
        self.mx.refresh_from_db()
        self.assertEqual(_fingerprint(self.mx), mx_before)

    def test_role_swap_between_two_managed_rows(self):
        my = grant(
            self.member, self.msp, self.role_y, reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_ALL, granted_by=self.granter,
        )
        form = MembershipForm(
            data=self._managed_rows(
                {
                    'id': self.mx.pk, 'role': self.role_y.pk,
                    'managed_scope': RoleAssignment.SCOPE_EXPLICIT,
                    'assigned_tenants': [self.cust_a.pk],
                },
                {
                    'id': my.pk, 'role': self.role_x.pk,
                    'managed_scope': RoleAssignment.SCOPE_ALL,
                },
            ),
            instance=self.membership, user=self.editor,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()
        managed = self.membership.assignments.filter(reach=RoleAssignment.REACH_MANAGED)
        self.assertEqual(managed.count(), 2)
        self.assertEqual(managed.get(role=self.role_y).managed_scope, RoleAssignment.SCOPE_EXPLICIT)
        self.assertEqual(managed.get(role=self.role_x).managed_scope, RoleAssignment.SCOPE_ALL)

    def test_role_change_is_audited_as_revoke_plus_grant(self):
        # The revoke+regrant design exists FOR the audit trail: with a request
        # context bound, the old row's deletion and the new row's creation must
        # both land in ObjectChange (a bulk-delete regression would skip them).
        import uuid
        from django.contrib.contenttypes.models import ContentType
        from core.models import ObjectChange
        from itambox.middleware import _current_user, _request_id

        _current_user.set(self.editor)
        _request_id.set(uuid.uuid4())
        try:
            old_pk = self.mx.pk
            form = MembershipForm(
                data=self._managed_rows({
                    'id': self.mx.pk, 'role': self.role_y.pk,
                    'managed_scope': RoleAssignment.SCOPE_EXPLICIT,
                    'assigned_tenants': [self.cust_a.pk],
                }),
                instance=self.membership, user=self.editor,
            )
            self.assertTrue(form.is_valid(), form.errors.as_json())
            form.save()
            new_pk = self.membership.assignments.get(
                reach=RoleAssignment.REACH_MANAGED).pk
            ct = ContentType.objects.get_for_model(RoleAssignment)
            self.assertTrue(ObjectChange._base_manager.filter(
                changed_object_type=ct, changed_object_id=old_pk, action='delete',
            ).exists(), "revocation of the old role must be change-logged")
            self.assertTrue(ObjectChange._base_manager.filter(
                changed_object_type=ct, changed_object_id=new_pk, action='create',
            ).exists(), "the fresh grant must be change-logged")
        finally:
            _current_user.set(None)
            _request_id.set(None)

    def test_role_change_duplicating_a_sibling_role_is_rejected(self):
        my = grant(
            self.member, self.msp, self.role_y, reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_ALL, granted_by=self.granter,
        )
        form = MembershipForm(
            data=self._managed_rows(
                {
                    'id': self.mx.pk, 'role': self.role_y.pk,
                    'managed_scope': RoleAssignment.SCOPE_EXPLICIT,
                    'assigned_tenants': [self.cust_a.pk],
                },
                {
                    'id': my.pk, 'role': self.role_y.pk,
                    'managed_scope': RoleAssignment.SCOPE_ALL,
                },
            ),
            instance=self.membership, user=self.editor,
        )
        self.assertFalse(form.is_valid())
        self.assertTrue(form.managed_formset.non_form_errors())
        # Nothing changed in the database.
        self.mx.refresh_from_db()
        self.assertEqual(self.mx.role_id, self.role_x.pk)


class SaveCommitContractTests(TenantTestMixin, TestCase):
    """RC-blocker regression: ``save(commit=False)`` must not persist anything.

    The inline who-block used to create (and persist) the new user before
    returning the unsaved membership. That combination is now rejected loudly;
    the existing-user path keeps its documented no-write contract.
    """

    def setUp(self):
        self.clear_tenant_context()
        self.msp = Tenant.objects.create(name="MSP CF", slug="msp-cf", is_provider=True)
        self.su = User.objects.create_superuser(
            username="cf_su", email="cf_su@x.com", password="pw",
        )
        self.member = User.objects.create_user(
            username="cf_member", email="cf_member@x.com", password="pw",
        )
        self.role = Role.objects.create(tenant=self.msp, name="CF Role", permissions=[])

    def tearDown(self):
        self.clear_tenant_context()

    def test_commit_false_with_inline_new_user_raises_and_writes_nothing(self):
        users_before = User.objects.count()
        form = MembershipForm(
            data=membership_post_data(
                tenant=self.msp.pk, who=MembershipForm.WHO_NEW,
                new_user_email='cf-fresh@example.com',
                new_user_first_name='Fresh', new_user_last_name='User',
            ),
            tenant=self.msp, user=self.su,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        with self.assertRaises(ValueError):
            form.save(commit=False)
        self.assertEqual(User.objects.count(), users_before)
        self.assertFalse(User.objects.filter(email__iexact='cf-fresh@example.com').exists())
        self.assertFalse(Membership.objects.filter(tenant=self.msp).exists())

    def test_commit_false_with_existing_user_defers_all_writes(self):
        form = MembershipForm(
            data=membership_post_data(
                user=self.member.pk, tenant=self.msp.pk,
                who=MembershipForm.WHO_EXISTING, own_roles=[self.role.pk],
            ),
            tenant=self.msp, user=self.su,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        instance = form.save(commit=False)
        self.assertIsNone(instance.pk)
        self.assertFalse(
            Membership.objects.filter(user=self.member, tenant=self.msp).exists()
        )
        self.assertEqual(
            RoleAssignment.objects.filter(membership__user=self.member).count(), 0,
        )

    def test_commit_false_with_resolved_existing_email_defers_all_writes(self):
        # who=new but the email matches an existing account: clean() resolves
        # the user (get-or-create semantics), so NO user row would be written —
        # commit=False must return the unsaved membership, not raise.
        form = MembershipForm(
            data=membership_post_data(
                tenant=self.msp.pk, who=MembershipForm.WHO_NEW,
                new_user_email=self.member.email,
                new_user_first_name='CF', new_user_last_name='Member',
            ),
            tenant=self.msp, user=self.su,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        instance = form.save(commit=False)
        self.assertIsNone(instance.pk)
        self.assertEqual(instance.user_id, self.member.pk)
        self.assertFalse(Membership.objects.filter(tenant=self.msp).exists())

    def test_commit_false_two_step_completes_grants_via_save_m2m(self):
        # The canonical Django two-step (instance.save() + form.save_m2m()) must
        # end with the SAME grant rows a commit=True save writes.
        form = MembershipForm(
            data=membership_post_data(
                user=self.member.pk, tenant=self.msp.pk,
                who=MembershipForm.WHO_EXISTING, own_roles=[self.role.pk],
            ),
            tenant=self.msp, user=self.su,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        instance = form.save(commit=False)
        self.assertEqual(
            RoleAssignment.objects.filter(membership__user=self.member).count(), 0,
        )
        instance.save()
        form.save_m2m()
        membership = Membership.objects.get(user=self.member, tenant=self.msp)
        assignment = membership.assignments.get()
        self.assertEqual(assignment.role_id, self.role.pk)
        self.assertEqual(assignment.reach, RoleAssignment.REACH_OWN)
        self.assertEqual(assignment.granted_by_id, self.su.pk)

    def test_commit_true_with_inline_new_user_still_works(self):
        form = MembershipForm(
            data=membership_post_data(
                tenant=self.msp.pk, who=MembershipForm.WHO_NEW,
                new_user_email='cf-real@example.com',
                new_user_first_name='Real', new_user_last_name='User',
            ),
            tenant=self.msp, user=self.su,
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        membership = form.save()
        self.assertIsNotNone(membership.pk)
        self.assertTrue(form.new_user_created)
        self.assertEqual(membership.user.email, 'cf-real@example.com')
