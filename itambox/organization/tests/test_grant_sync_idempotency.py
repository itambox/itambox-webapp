"""``sync_membership_grants`` — the write half of the RBAC service (issue #86).

The reconciliation passes moved out of ``MembershipForm`` verbatim; these tests
pin the invariants that make them safe to share: provenance on survivors
(INV-6), inert tampered ids (INV-7), independent own/managed reconciliation on
one aggregate (INV-9), expired rows as inert history (INV-10), a change-logged
per-object delete (INV-8), and a replay that writes nothing (INV-16).

They also pin the three gates that make "validate before write" structural
rather than a comment: only a ``ValidatedGrantPlan`` is accepted, the write
phase refuses to run outside a transaction, and grant state that moved after
validation fails closed.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from core.models import ObjectChange
from core.tasks.context import TaskContext
from core.tests.mixins import grant
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant, TenantGroup
from organization.services.errors import ConcurrentGrantChange
from organization.services.rolegrants import (
    GrantPlan,
    ManagedGrantSpec,
    OwnGrantSpec,
    sync_membership_grants,
    validate_grant_plan,
)

User = get_user_model()


class _SyncTestBase:
    """Provider + two customers; a superuser actor so the write phase is the
    only thing under test."""

    def build_world(self):
        self.actor = User.objects.create_superuser(username="root")
        self.other_actor = User.objects.create_superuser(username="root-2")
        self.target = User.objects.create_user(username="target")
        self.provider = Tenant.objects.create(name="Provider", slug="provider", is_provider=True)
        self.customer_a = Tenant.objects.create(name="Customer A", slug="cust-a", managed_by=self.provider)
        self.customer_b = Tenant.objects.create(name="Customer B", slug="cust-b", managed_by=self.provider)
        self.read_role = Role.objects.create(
            tenant=self.provider,
            name="Reader",
            permissions=["assets.view_asset"],
        )
        self.write_role = Role.objects.create(
            tenant=self.provider,
            name="Writer",
            permissions=["assets.view_asset"],
        )
        self.membership = Membership.objects.create(user=self.target, tenant=self.provider)

    def apply(self, plan, *, membership=None, actor=None):
        membership = membership or self.membership
        validated = validate_grant_plan(
            actor=self.actor if actor is None else actor,
            principal_tenant=membership.tenant,
            plan=plan,
            membership=membership,
        )
        return sync_membership_grants(membership=membership, validated=validated)

    @staticmethod
    def scope_keys(grant_obj):
        return sorted((scope.scope_type, scope.tenant_id, scope.tenant_group_id) for scope in grant_obj.scopes.all())


class OwnReachSyncTests(_SyncTestBase, TestCase):
    def setUp(self):
        self.build_world()

    def test_selecting_a_role_creates_one_aggregate_with_one_own_child(self):
        result = self.apply(GrantPlan(own=(OwnGrantSpec(role=self.read_role),)))

        created = RoleGrant.objects.get(membership=self.membership)
        self.assertEqual(created.role_id, self.read_role.pk)
        self.assertEqual(created.granted_by_id, self.actor.pk)
        self.assertEqual(self.scope_keys(created), [(RoleGrantScope.SCOPE_OWN, None, None)])
        self.assertEqual([c.action for c in result.changes], ["created"])
        self.assertTrue(result.wrote_anything)

    def test_deselecting_a_role_revokes_it_per_object_so_it_is_change_logged(self):
        """INV-8 — a queryset delete would leave no ``ObjectChange`` tombstone.

        The service deliberately does NOT set the change-log request context;
        that stays the caller's job, so a non-HTTP caller wraps the write in
        ``TaskContext`` exactly as this test does (§11).
        """
        existing = grant(self.target, self.provider, self.read_role, reach="own")
        before = ObjectChange.objects.count()

        with TaskContext(tenant_id=self.provider.pk, user_id=self.actor.pk):
            result = self.apply(GrantPlan(), membership=existing.membership)

        self.assertFalse(RoleGrant.objects.filter(pk=existing.pk).exists())
        tombstones = ObjectChange.objects.exclude(pk__in=[])
        self.assertGreater(tombstones.count(), before)
        self.assertEqual(
            set(tombstones.values_list("user_id", flat=True)),
            {self.actor.pk},
            "every recorded change must be attributed to the task principal",
        )
        self.assertEqual([c.action for c in result.changes], ["revoked"])

    def test_without_request_context_the_write_still_succeeds_but_is_unlogged(self):
        """The documented consequence of ``ChangeLoggingMixin``'s early return —
        stated in the service docstrings so a task author cannot be surprised."""
        existing = grant(self.target, self.provider, self.read_role, reach="own")

        self.apply(GrantPlan(), membership=existing.membership)

        self.assertFalse(RoleGrant.objects.filter(pk=existing.pk).exists())
        self.assertEqual(ObjectChange.objects.count(), 0)

    def test_non_privileged_own_grant_never_stores_submitted_metadata(self):
        """INV-5 own half — the gate is applied at write time."""
        self.apply(
            GrantPlan(
                own=(
                    OwnGrantSpec(
                        role=self.read_role,
                        reason="not needed",
                        valid_until=timezone.now() + timedelta(days=5),
                    ),
                )
            )
        )

        created = RoleGrant.objects.get(membership=self.membership)
        self.assertEqual(created.reason, "")
        self.assertIsNone(created.valid_until)

    def test_privileged_own_grant_keeps_its_reason_and_expiry(self):
        admin_role = Role.objects.create(
            tenant=self.provider,
            name="Admin",
            permissions=["assets.view_asset", "assets.change_asset"],
        )
        expiry = timezone.now() + timedelta(days=5)

        self.apply(GrantPlan(own=(OwnGrantSpec(role=admin_role, reason="on call", valid_until=expiry),)))

        created = RoleGrant.objects.get(membership=self.membership, role=admin_role)
        self.assertEqual(created.reason, "on call")
        self.assertEqual(created.valid_until, expiry)

    def test_expired_own_grant_is_left_untouched_as_audit_history(self):
        """INV-10 — neither revoked nor re-used; a fresh aggregate is created."""
        expired = grant(self.target, self.provider, self.read_role, reach="own")
        RoleGrant.objects.filter(pk=expired.pk).update(valid_until=timezone.now() - timedelta(days=1))

        self.apply(GrantPlan(own=(OwnGrantSpec(role=self.read_role),)), membership=expired.membership)

        self.assertTrue(RoleGrant.objects.filter(pk=expired.pk).exists())
        self.assertEqual(RoleGrant.objects.filter(membership=expired.membership).count(), 2)


class ManagedReachSyncTests(_SyncTestBase, TestCase):
    def setUp(self):
        self.build_world()

    def _managed(self, **overrides):
        overrides.setdefault("role", self.read_role)
        overrides.setdefault("tenants", (self.customer_a,))
        overrides.setdefault("row_index", 0)
        return GrantPlan(managed=(ManagedGrantSpec(**overrides),))

    def test_explicit_row_writes_one_tenant_child_per_target(self):
        self.apply(
            GrantPlan(
                managed=(
                    ManagedGrantSpec(
                        role=self.read_role,
                        tenants=(self.customer_a, self.customer_b),
                        row_index=0,
                    ),
                )
            )
        )

        created = RoleGrant.objects.get(membership=self.membership)
        self.assertEqual(
            self.scope_keys(created),
            sorted(
                [
                    (RoleGrantScope.SCOPE_TENANT, self.customer_a.pk, None),
                    (RoleGrantScope.SCOPE_TENANT, self.customer_b.pk, None),
                ]
            ),
        )

    def test_surviving_row_keeps_its_provenance_when_only_coverage_changes(self):
        """INV-6 — ``granted_by``/``granted_at`` document who granted THIS role."""
        existing = grant(
            self.target,
            self.provider,
            self.read_role,
            reach="managed",
            assigned_tenants=[self.customer_a],
            granted_by=self.other_actor,
        )
        granted_at = existing.granted_at

        self.apply(
            self._managed(grant_id=existing.pk, tenants=(self.customer_b,)),
            membership=existing.membership,
        )

        existing.refresh_from_db()
        self.assertEqual(existing.granted_by_id, self.other_actor.pk)
        self.assertEqual(existing.granted_at, granted_at)
        self.assertEqual(self.scope_keys(existing), [(RoleGrantScope.SCOPE_TENANT, self.customer_b.pk, None)])

    def test_changing_the_role_on_a_row_is_a_revoke_plus_a_fresh_grant(self):
        """INV-6 — never an in-place mutation, so the audit trail stays honest."""
        existing = grant(
            self.target,
            self.provider,
            self.read_role,
            reach="managed",
            assigned_tenants=[self.customer_a],
            granted_by=self.other_actor,
        )

        result = self.apply(
            self._managed(grant_id=existing.pk, role=self.write_role),
            membership=existing.membership,
        )

        self.assertFalse(RoleGrant.objects.filter(pk=existing.pk).exists())
        fresh = RoleGrant.objects.get(membership=existing.membership)
        self.assertEqual(fresh.role_id, self.write_role.pk)
        self.assertEqual(fresh.granted_by_id, self.actor.pk)
        self.assertEqual({c.action for c in result.changes}, {"revoked", "created"})

    def test_a_grant_id_from_another_membership_can_never_be_touched(self):
        """INV-7 / X-5 — a tampered id is inert, not a cross-principal handle."""
        stranger = User.objects.create_user(username="stranger")
        foreign = grant(
            stranger,
            self.provider,
            self.read_role,
            reach="managed",
            assigned_tenants=[self.customer_a],
            granted_by=self.other_actor,
        )
        foreign_before = (foreign.role_id, foreign.granted_by_id, self.scope_keys(foreign))

        self.apply(self._managed(grant_id=foreign.pk, tenants=(self.customer_b,)))

        foreign.refresh_from_db()
        self.assertEqual((foreign.role_id, foreign.granted_by_id, self.scope_keys(foreign)), foreign_before)
        mine = RoleGrant.objects.get(membership=self.membership)
        self.assertNotEqual(mine.pk, foreign.pk)
        self.assertEqual(self.scope_keys(mine), [(RoleGrantScope.SCOPE_TENANT, self.customer_b.pk, None)])

    def test_omitting_a_row_revokes_only_its_managed_children(self):
        """INV-9 — an own scope on the same aggregate survives."""
        combined = grant(self.target, self.provider, self.read_role, reach="own")
        RoleGrantScope.objects.create(
            role_grant=combined,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )

        self.apply(
            GrantPlan(own=(OwnGrantSpec(role=self.read_role),)),
            membership=combined.membership,
        )

        combined.refresh_from_db()
        self.assertEqual(self.scope_keys(combined), [(RoleGrantScope.SCOPE_OWN, None, None)])

    def test_deselecting_the_own_role_leaves_the_managed_children_alone(self):
        """INV-9, the other direction."""
        combined = grant(self.target, self.provider, self.read_role, reach="own")
        RoleGrantScope.objects.create(
            role_grant=combined,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )

        self.apply(
            self._managed(grant_id=combined.pk, tenants=(self.customer_a,)),
            membership=combined.membership,
        )

        combined.refresh_from_db()
        self.assertEqual(self.scope_keys(combined), [(RoleGrantScope.SCOPE_TENANT, self.customer_a.pk, None)])

    def test_own_and_managed_reach_for_one_role_are_separate_aggregates(self):
        """INV-9 — a newly selected own role always gets its OWN aggregate."""
        self.apply(
            GrantPlan(
                own=(OwnGrantSpec(role=self.read_role),),
                managed=(ManagedGrantSpec(role=self.read_role, tenants=(self.customer_a,), row_index=0),),
            )
        )

        grants = list(RoleGrant.objects.filter(membership=self.membership).prefetch_related("scopes"))
        self.assertEqual(len(grants), 2)
        self.assertEqual(
            sorted(scope.scope_type for g in grants for scope in g.scopes.all()),
            ["own", "tenant"],
        )

    def test_group_coverage_writes_a_single_tenant_group_child(self):
        group = TenantGroup.objects.create(name="North", slug="north")
        self.customer_a.group = group
        self.customer_a.save(update_fields=["group"])

        self.apply(
            self._managed(
                scope=RoleGrantScope.SCOPE_TENANT_GROUP,
                scope_group=group,
                tenants=(),
            )
        )

        created = RoleGrant.objects.get(membership=self.membership)
        self.assertEqual(self.scope_keys(created), [(RoleGrantScope.SCOPE_TENANT_GROUP, None, group.pk)])

    def test_all_managed_coverage_writes_a_single_singleton_child(self):
        self.apply(self._managed(scope=RoleGrantScope.SCOPE_ALL_MANAGED, tenants=()))

        created = RoleGrant.objects.get(membership=self.membership)
        self.assertEqual(self.scope_keys(created), [(RoleGrantScope.SCOPE_ALL_MANAGED, None, None)])

    def test_metadata_only_change_updates_in_place(self):
        expiry = timezone.now() + timedelta(days=9)
        existing = grant(
            self.target,
            self.provider,
            self.read_role,
            reach="managed",
            assigned_tenants=[self.customer_a],
        )

        result = self.apply(
            self._managed(grant_id=existing.pk, reason="audit window", valid_until=expiry),
            membership=existing.membership,
        )

        existing.refresh_from_db()
        self.assertEqual(existing.reason, "audit window")
        self.assertEqual(existing.valid_until, expiry)
        self.assertEqual([c.action for c in result.changes], ["updated"])


class IdempotencyTests(_SyncTestBase, TestCase):
    """INV-16 / Y-1 — re-applying an intent against the state it produced writes
    zero rows: no ``ObjectChange``, no moved ``granted_at``."""

    def setUp(self):
        self.build_world()
        self.plan = GrantPlan(
            own=(OwnGrantSpec(role=self.read_role),),
            managed=(
                ManagedGrantSpec(
                    role=self.write_role,
                    tenants=(self.customer_a, self.customer_b),
                    row_index=0,
                ),
            ),
        )

    def test_replaying_the_same_intent_writes_nothing(self):
        self.apply(self.plan)
        managed = RoleGrant.objects.get(membership=self.membership, role=self.write_role)
        own = RoleGrant.objects.get(membership=self.membership, role=self.read_role)
        stamps = (managed.granted_at, own.granted_at)
        change_count = ObjectChange.objects.count()

        replay = GrantPlan(
            own=self.plan.own,
            managed=(
                ManagedGrantSpec(
                    role=self.write_role, grant_id=managed.pk, tenants=(self.customer_a, self.customer_b), row_index=0
                ),
            ),
        )
        result = self.apply(replay)

        self.assertFalse(result.wrote_anything)
        self.assertEqual({c.action for c in result.changes}, {"unchanged"})
        self.assertEqual(ObjectChange.objects.count(), change_count)
        managed.refresh_from_db()
        own.refresh_from_db()
        self.assertEqual((managed.granted_at, own.granted_at), stamps)

    def test_an_empty_plan_against_an_empty_membership_reports_no_changes(self):
        result = self.apply(GrantPlan())

        self.assertEqual(result.changes, ())
        self.assertFalse(result.wrote_anything)


class WritePhaseGateTests(_SyncTestBase, TestCase):
    """The preconditions are enforced, not documented."""

    def setUp(self):
        self.build_world()

    def _validated(self, membership=None):
        membership = membership or self.membership
        return validate_grant_plan(
            actor=self.actor,
            principal_tenant=self.provider,
            plan=GrantPlan(own=(OwnGrantSpec(role=self.read_role),)),
            membership=membership,
        )

    def test_a_raw_grant_plan_is_refused_by_type(self):
        """Y-5 — INV-1 expressed in the type system."""
        with self.assertRaises(TypeError):
            sync_membership_grants(
                membership=self.membership,
                validated=GrantPlan(own=(OwnGrantSpec(role=self.read_role),)),
            )

        self.assertEqual(RoleGrant.objects.count(), 0)

    def test_a_token_bound_to_another_membership_is_refused(self):
        """The type gate alone would still allow applying A's decision to B."""
        other = Membership.objects.create(
            user=User.objects.create_user(username="somebody"),
            tenant=self.provider,
        )
        validated = self._validated(membership=other)

        with self.assertRaises(ValueError):
            sync_membership_grants(membership=self.membership, validated=validated)

        self.assertEqual(RoleGrant.objects.count(), 0)

    def test_an_unbound_create_token_is_never_accepted(self):
        """``membership_id=None`` must be rebound to the inserted row first."""
        validated = validate_grant_plan(
            actor=self.actor,
            principal_tenant=self.provider,
            plan=GrantPlan(own=(OwnGrantSpec(role=self.read_role),)),
        )
        self.assertIsNone(validated.membership_id)

        with self.assertRaises(ValueError):
            sync_membership_grants(membership=self.membership, validated=validated)

        self.assertEqual(RoleGrant.objects.count(), 0)

    def test_grant_state_moving_after_validation_fails_closed(self):
        """Y-6 — an unmigrated writer that does not take the membership lock can
        legitimately win this race; the caller must re-render, not 500."""
        validated = self._validated()
        # A legacy writer (MembershipBulkEditView._add_own_scope, SSO provisioning)
        # lands a grant between the decision and the write.
        interloper = RoleGrant.objects.create(membership=self.membership, role=self.write_role)
        RoleGrantScope.objects.create(role_grant=interloper, scope_type=RoleGrantScope.SCOPE_OWN)

        with self.assertRaises(ConcurrentGrantChange) as ctx:
            sync_membership_grants(membership=self.membership, validated=validated)

        self.assertIn("resubmit", " ".join(ctx.exception.messages).lower())
        self.assertIsNone(ctx.exception.errors[0].field)
        self.assertIsNone(ctx.exception.errors[0].row_index)
        # Nothing from the attempted reconciliation was written.
        self.assertFalse(RoleGrant.objects.filter(membership=self.membership, role=self.read_role).exists())


class WritePhaseTransactionGateTests(_SyncTestBase, TransactionTestCase):
    """Y-4 — the ``in_atomic_block`` gate can only be observed outside the
    implicit transaction a plain ``TestCase`` wraps every test body in."""

    def setUp(self):
        self.build_world()

    def test_the_write_phase_refuses_to_run_unprotected(self):
        with transaction.atomic():
            validated = validate_grant_plan(
                actor=self.actor,
                principal_tenant=self.provider,
                plan=GrantPlan(own=(OwnGrantSpec(role=self.read_role),)),
                membership=self.membership,
            )

        self.assertFalse(transaction.get_connection().in_atomic_block)
        with self.assertRaises(RuntimeError):
            sync_membership_grants(membership=self.membership, validated=validated)

        self.assertEqual(RoleGrant.objects.count(), 0)

    def test_the_same_call_succeeds_inside_a_transaction(self):
        with transaction.atomic():
            validated = validate_grant_plan(
                actor=self.actor,
                principal_tenant=self.provider,
                plan=GrantPlan(own=(OwnGrantSpec(role=self.read_role),)),
                membership=self.membership,
            )
            sync_membership_grants(membership=self.membership, validated=validated)

        self.assertEqual(RoleGrant.objects.filter(membership=self.membership).count(), 1)
