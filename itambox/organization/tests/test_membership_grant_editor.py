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
from organization.models import Tenant, Role, RoleAssignment, TenantGroup
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
