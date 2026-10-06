"""The RoleGrant write phase: validate-before-write, reconciliation, replay, races.

``validate_grant_plan`` returns a ``ValidatedGrantPlan`` and
``sync_membership_grants`` accepts *only* that type, bound to the membership it
was validated against. That makes INV-1 ("decide-then-write") a property of the
type system rather than of the current source layout, and these suites pin every
way the property can be subverted: a raw plan, a plan pointed at a different
membership, an unbound plan from a create, a write outside a transaction, and a
grant that moved between the decision and the write.

The reconciliation suites pin the four apply passes (design §7.3) against the
invariants they exist to preserve — provenance (INV-6), inert tampered ids
(INV-7), independent own/managed reconciliation on one aggregate (INV-9),
expiry-aware reads (INV-10), per-object change-logged deletes (INV-8) — and the
"atomic and idempotent" acceptance criterion (INV-15, INV-16).
"""

import dataclasses

from django.db import transaction
from django.db.models.signals import post_save
from django.test import TestCase, TransactionTestCase

from core.models import ObjectChange
from core.tests.mixins import grant
from organization.models import Membership, RoleGrant, RoleGrantScope
from organization.rbac import effective_permissions

from ._membership_service_adversarial_helpers import (
    ServiceWorldMixin,
    future,
    membership_services,
    past,
    state_fingerprint,
)


class GrantSyncTestCase(ServiceWorldMixin, TestCase):
    prefix = "sync"

    def setUp(self):
        self.svc = membership_services()
        self.setup_service_world(self.prefix)
        self.membership = self.membership_for(self.member, self.provider)

    # ------------------------------------------------------------ plan builders
    def own(self, role, **kwargs):
        return self.svc.OwnGrantSpec(role=role, **kwargs)

    def managed(self, role, **kwargs):
        return self.svc.ManagedGrantSpec(role=role, **kwargs)

    def plan(self, *, own=(), managed=()):
        return self.svc.GrantPlan(own=tuple(own), managed=tuple(managed))

    def validate(self, plan, *, membership=None, actor=None):
        membership = self.membership if membership is None else membership
        return self.svc.validate_grant_plan(
            actor=self.superuser if actor is None else actor,
            principal_tenant=membership.tenant,
            plan=plan,
            membership=membership,
        )

    def apply(self, plan, *, membership=None, actor=None):
        """Validate then write, inside the transaction boundary the service requires."""
        membership = self.membership if membership is None else membership
        validated = self.validate(plan, membership=membership, actor=actor)
        with transaction.atomic():
            return self.svc.sync_membership_grants(membership=membership, validated=validated)

    # ------------------------------------------------------------------ reading
    def grants_for(self, role, *, membership=None):
        membership = self.membership if membership is None else membership
        return RoleGrant._base_manager.filter(membership=membership, role=role)

    def scope_types(self, role_grant):
        return sorted(role_grant.scopes.values_list("scope_type", flat=True))


class ValidatedPlanTypeGateTests(GrantSyncTestCase):
    """INV-1 encoded as a type, and bound to one principal.

    Without the ``membership_id`` binding the "only a validated plan may be
    applied" gate would still allow membership A's decision to be applied to
    membership B — the type would encode nothing useful (design §4.2).
    """

    def test_a_raw_grant_plan_is_refused(self):
        """Y-5 — an unvalidated plan is a programmer error, not a rejection."""
        with self.assert_writes_nothing("an unvalidated plan"):
            with self.assertRaises(TypeError):
                with transaction.atomic():
                    self.svc.sync_membership_grants(
                        membership=self.membership,
                        validated=self.plan(own=(self.own(self.read_role),)),
                    )

    def test_a_plan_validated_for_another_membership_is_refused(self):
        other = self.membership_for(self.make_user("sync-other-member"), self.provider)
        validated = self.validate(self.plan(own=(self.own(self.read_role),)), membership=other)
        self.assertEqual(validated.membership_id, other.pk)
        with self.assert_writes_nothing("a plan applied to the wrong membership"):
            with self.assertRaises(ValueError):
                with transaction.atomic():
                    self.svc.sync_membership_grants(membership=self.membership, validated=validated)

    def test_an_unbound_create_plan_is_never_applied_to_an_existing_membership(self):
        """A create validates before the row exists, so the token comes back with
        ``membership_id=None``. That value must never be accepted: only the insert
        may rebind it."""
        validated = self.svc.validate_grant_plan(
            actor=self.superuser,
            principal_tenant=self.provider,
            plan=self.plan(own=(self.own(self.read_role),)),
            membership=None,
        )
        self.assertIsNone(validated.membership_id)
        with self.assert_writes_nothing("an unbound plan aimed at an existing membership"):
            with self.assertRaises(ValueError):
                with transaction.atomic():
                    self.svc.sync_membership_grants(membership=self.membership, validated=validated)

    def test_a_plan_validated_for_another_tenant_is_refused(self):
        """The membership binding alone does not prove the *tenant* matched.

        ``validate_grant_plan``'s assignability and escalation decisions are all
        taken relative to ``principal_tenant``; applying such a token to a
        membership that lives somewhere else would mean the whole decision was
        made in the wrong tenant. Defence in depth behind
        ``plan_membership_write``'s own binding, and a ``ValueError`` because no
        legitimate caller can produce it.
        """
        validated = self.validate(self.plan(own=(self.own(self.read_role),)))
        retargeted = dataclasses.replace(validated, principal_tenant=self.customer_a)

        with self.assert_writes_nothing("a plan validated against a different tenant"):
            with self.assertRaises(ValueError):
                with transaction.atomic():
                    self.svc.sync_membership_grants(membership=self.membership, validated=retargeted)

    def test_validation_records_the_state_it_reasoned_about(self):
        existing = grant(
            self.member,
            self.provider,
            self.read_role,
            reach=RoleGrant.REACH_MANAGED,
            assigned_tenants=[self.customer_a],
        )
        grant(self.member, self.provider, self.other_read_role)
        validated = self.validate(
            self.plan(managed=(self.managed(self.read_role, grant_id=existing.pk, tenants=(self.customer_a,)),))
        )
        self.assertEqual(validated.membership_id, self.membership.pk)
        self.assertEqual(set(validated.existing_own_role_ids), {self.other_read_role.pk})
        self.assertEqual(set(validated.existing_managed_grant_ids), {existing.pk})


class GrantSyncAtomicGateTests(ServiceWorldMixin, TransactionTestCase):
    """Y-4 — the write phase refuses to run unprotected.

    This has to be a ``TransactionTestCase``: under the default ``TestCase`` the
    test body is itself wrapped in an atomic block, so ``in_atomic_block`` is
    always true and the gate could never be observed to fire. The gate is a
    ``RuntimeError`` rather than an ``assert`` because assertions are stripped
    under ``-O`` and this is a write-safety check, not a debugging aid.
    """

    def setUp(self):
        self.svc = membership_services()
        self.setup_service_world("sync-atomic")
        self.membership = self.membership_for(self.member, self.provider)

    def build_validated(self):
        return self.svc.validate_grant_plan(
            actor=self.superuser,
            principal_tenant=self.provider,
            plan=self.svc.GrantPlan(own=(self.svc.OwnGrantSpec(role=self.read_role),)),
            membership=self.membership,
        )

    def test_sync_outside_a_transaction_is_refused(self):
        validated = self.build_validated()
        before = state_fingerprint()
        with self.assertRaises(RuntimeError):
            self.svc.sync_membership_grants(membership=self.membership, validated=validated)
        self.assertEqual(state_fingerprint(), before, "the refused write must leave every row alone")
        self.assertFalse(RoleGrant._base_manager.filter(membership=self.membership).exists())

    def test_the_same_call_succeeds_inside_a_transaction(self):
        """Positive control — the gate rejects the missing boundary, not the call."""
        validated = self.build_validated()
        with transaction.atomic():
            result = self.svc.sync_membership_grants(membership=self.membership, validated=validated)
        self.assertTrue(result.wrote_anything)
        self.assertEqual(RoleGrant._base_manager.filter(membership=self.membership).count(), 1)


class GrantReconciliationTests(GrantSyncTestCase):
    """The four apply passes (design §7.3), pinned by the invariants they preserve."""

    def test_own_and_managed_reach_reconcile_independently_on_one_aggregate(self):
        """INV-9 — dropping the managed row keeps the own scope on the same grant."""
        aggregate = grant(self.member, self.provider, self.read_role)
        RoleGrantScope.objects.create(
            role_grant=aggregate,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )
        self.apply(self.plan(own=(self.own(self.read_role),)))
        aggregate.refresh_from_db()
        self.assertEqual(self.scope_types(aggregate), [RoleGrantScope.SCOPE_OWN])

    def test_dropping_the_own_role_keeps_the_managed_children(self):
        """INV-9, the other direction."""
        aggregate = grant(self.member, self.provider, self.read_role)
        RoleGrantScope.objects.create(
            role_grant=aggregate,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )
        self.apply(
            self.plan(managed=(self.managed(self.read_role, grant_id=aggregate.pk, tenants=(self.customer_a,)),))
        )
        aggregate.refresh_from_db()
        self.assertEqual(self.scope_types(aggregate), [RoleGrantScope.SCOPE_TENANT])

    def test_a_new_own_role_gets_its_own_aggregate_beside_a_managed_one(self):
        """INV-9 — own reach never rides on a managed-only aggregate."""
        managed_only = grant(
            self.member,
            self.provider,
            self.read_role,
            reach=RoleGrant.REACH_MANAGED,
            assigned_tenants=[self.customer_a],
        )
        self.apply(
            self.plan(
                own=(self.own(self.read_role),),
                managed=(self.managed(self.read_role, grant_id=managed_only.pk, tenants=(self.customer_a,)),),
            )
        )
        self.assertEqual(self.grants_for(self.read_role).count(), 2)
        own_grant = self.grants_for(self.read_role).exclude(pk=managed_only.pk).get()
        self.assertEqual(self.scope_types(own_grant), [RoleGrantScope.SCOPE_OWN])
        managed_only.refresh_from_db()
        self.assertEqual(self.scope_types(managed_only), [RoleGrantScope.SCOPE_TENANT])

    def test_a_surviving_row_keeps_its_provenance(self):
        """INV-6 — ``granted_by``/``granted_at`` document who granted THIS role."""
        existing = grant(
            self.member,
            self.provider,
            self.read_role,
            reach=RoleGrant.REACH_MANAGED,
            assigned_tenants=[self.customer_a],
            granted_by=self.member,
        )
        granted_at = existing.granted_at
        self.apply(self.plan(managed=(self.managed(self.read_role, grant_id=existing.pk, tenants=(self.customer_a,)),)))
        existing.refresh_from_db()
        self.assertEqual(existing.granted_by, self.member)
        self.assertEqual(existing.granted_at, granted_at)

    def test_changing_a_rows_role_is_a_revoke_plus_a_fresh_grant(self):
        """INV-6 — never an in-place mutation, so provenance stays truthful."""
        existing = grant(
            self.member,
            self.provider,
            self.read_role,
            reach=RoleGrant.REACH_MANAGED,
            assigned_tenants=[self.customer_a],
            granted_by=self.member,
        )
        self.apply(
            self.plan(managed=(self.managed(self.other_read_role, grant_id=existing.pk, tenants=(self.customer_a,)),))
        )
        self.assertFalse(RoleGrant._base_manager.filter(pk=existing.pk).exists())
        replacement = self.grants_for(self.other_read_role).get()
        self.assertEqual(replacement.granted_by, self.superuser)
        self.assertEqual(self.scope_types(replacement), [RoleGrantScope.SCOPE_TENANT])

    def test_a_tampered_grant_id_can_never_touch_another_membership(self):
        """INV-7 — an unknown id is inert; the row is treated as new."""
        victim_membership = self.membership_for(self.make_user("sync-victim"), self.provider)
        foreign = grant(
            victim_membership.user,
            self.provider,
            self.read_role,
            reach=RoleGrant.REACH_MANAGED,
            assigned_tenants=[self.customer_z],
        )
        foreign_before = self._grant_row(foreign.pk)

        self.apply(self.plan(managed=(self.managed(self.read_role, grant_id=foreign.pk, tenants=(self.customer_a,)),)))

        self.assertEqual(self._grant_row(foreign.pk), foreign_before)
        foreign.refresh_from_db()
        self.assertEqual(list(foreign.scopes.values_list("tenant_id", flat=True)), [self.customer_z.pk])
        mine = self.grants_for(self.read_role).get()
        self.assertNotEqual(mine.pk, foreign.pk)
        self.assertEqual(list(mine.scopes.values_list("tenant_id", flat=True)), [self.customer_a.pk])

    def test_an_expired_grant_is_inert_audit_history(self):
        """INV-10 / X-6 — expired rows are neither seeded, reused, nor revoked."""
        expired_managed = RoleGrant.objects.create(
            membership=self.membership,
            role=self.read_role,
            valid_until=past(),
        )
        RoleGrantScope.objects.create(
            role_grant=expired_managed,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )
        expired_own = RoleGrant.objects.create(
            membership=self.membership,
            role=self.other_read_role,
            valid_until=past(),
        )
        RoleGrantScope.objects.create(role_grant=expired_own, scope_type=RoleGrantScope.SCOPE_OWN)
        expired_rows = (self._grant_row(expired_managed.pk), self._grant_row(expired_own.pk))

        self.apply(
            self.plan(managed=(self.managed(self.read_role, grant_id=expired_managed.pk, tenants=(self.customer_a,)),))
        )

        self.assertEqual(
            (self._grant_row(expired_managed.pk), self._grant_row(expired_own.pk)),
            expired_rows,
            "expired grants are inert history — the write phase must not reuse or revoke them",
        )
        live = self.grants_for(self.read_role).exclude(pk=expired_managed.pk).get()
        self.assertEqual(self.scope_types(live), [RoleGrantScope.SCOPE_TENANT])

    def test_managed_metadata_is_stored_verbatim_for_a_view_only_role(self):
        """INV-5, managed half — a view-only managed grant may be time-boxed."""
        expiry = future()
        self.apply(
            self.plan(
                managed=(
                    self.managed(
                        self.read_role,
                        tenants=(self.customer_a,),
                        reason="temporary handover",
                        valid_until=expiry,
                    ),
                )
            )
        )
        stored = self.grants_for(self.read_role).get()
        self.assertEqual(stored.reason, "temporary handover")
        self.assertEqual(stored.valid_until, expiry)

    def test_own_metadata_is_gated_on_privilege(self):
        """INV-5, own half — a non-privileged own grant stores no reason/expiry
        even when the submitter supplied them (membership_form.py:868-874)."""
        self.apply(self.plan(own=(self.own(self.read_role, reason="ignored", valid_until=future()),)))
        stored = self.grants_for(self.read_role).get()
        self.assertEqual(stored.reason, "")
        self.assertIsNone(stored.valid_until)

    def test_revocation_is_change_logged_per_object(self):
        """INV-8 — a queryset delete would revoke without an audit trail."""
        aggregate = grant(self.member, self.provider, self.read_role)
        scope_pk = aggregate.scopes.get().pk

        self.apply(self.plan())

        self.assertFalse(RoleGrant._base_manager.filter(pk=aggregate.pk).exists())
        self.assertTrue(
            ObjectChange._base_manager.filter(
                changed_object_id=scope_pk,
                action="delete",
                changed_object_type__model="rolegrantscope",
            ).exists(),
            "each revoked scope must leave a tombstone",
        )
        self.assertTrue(
            ObjectChange._base_manager.filter(
                changed_object_id=aggregate.pk,
                action="delete",
                changed_object_type__model="rolegrant",
            ).exists(),
            "each revoked aggregate must leave a tombstone",
        )

    def test_the_result_describes_what_was_written(self):
        result = self.apply(
            self.plan(
                own=(self.own(self.read_role),),
                managed=(self.managed(self.other_read_role, tenants=(self.customer_a,)),),
            )
        )
        self.assertTrue(result.wrote_anything)
        created = result.of("created")
        self.assertEqual(
            sorted(change.role_id for change in created),
            sorted([self.read_role.pk, self.other_read_role.pk]),
        )
        self.assertEqual({change.reach for change in created}, {RoleGrant.REACH_OWN, RoleGrant.REACH_MANAGED})

    @staticmethod
    def _grant_row(pk):
        fields = ("pk", "role_id", "granted_by_id", "granted_at", "reason", "valid_until")
        return RoleGrant._base_manager.filter(pk=pk).values(*fields).first()


class GrantSyncIdempotencyTests(GrantSyncTestCase):
    """INV-16 / Y-1 — re-applying an intent against the state it produced writes nothing."""

    def setUp(self):
        super().setUp()
        self.expiry = future()

    def build_plan(self):
        return self.plan(
            own=(self.own(self.read_role),),
            managed=(
                self.managed(
                    self.other_read_role,
                    tenants=(self.customer_a, self.customer_z),
                    reason="handover",
                    valid_until=self.expiry,
                ),
            ),
        )

    def test_replaying_an_identical_intent_writes_zero_rows(self):
        first = self.apply(self.build_plan())
        self.assertTrue(first.wrote_anything)
        own_grant = self.grants_for(self.read_role).get()
        managed_grant = self.grants_for(self.other_read_role).get()
        before = state_fingerprint()
        change_count = ObjectChange._base_manager.count()

        # The surviving rows are now the plan's ``grant_id`` inputs, exactly as a
        # re-rendered form would resubmit them.
        second = self.apply(
            self.plan(
                own=(self.own(self.read_role),),
                managed=(
                    self.managed(
                        self.other_read_role,
                        grant_id=managed_grant.pk,
                        tenants=(self.customer_a, self.customer_z),
                        reason="handover",
                        valid_until=self.expiry,
                    ),
                ),
            )
        )

        self.assertFalse(second.wrote_anything)
        self.assertEqual({change.action for change in second.changes}, {"unchanged"})
        self.assertEqual(state_fingerprint(), before, "an idempotent replay must not move a single row")
        self.assertEqual(ObjectChange._base_manager.count(), change_count)
        granted_at = own_grant.granted_at
        own_grant.refresh_from_db()
        managed_grant.refresh_from_db()
        self.assertEqual(own_grant.granted_at, granted_at)
        self.assertEqual(managed_grant.granted_by, self.superuser)


class GrantSyncConcurrencyTests(GrantSyncTestCase):
    """Y-6 — an unmigrated writer can still win the race, and must not be overwritten.

    ``select_for_update()`` on the ``Membership`` row serialises callers of the
    service, but the grant writers listed in design §7.5 (``RoleAssignUsersView``,
    ``MembershipBulkEditView``, ``UserGroupForm``, SSO provisioning) do not take
    it. When one of them lands between the decision and the write, the tamper
    check must fail closed with a typed, form-renderable error rather than
    reconciling against state nobody validated.
    """

    def test_a_grant_inserted_after_validation_fails_the_write_closed(self):
        validated = self.validate(self.plan(own=(self.own(self.read_role),)))

        # A legacy writer that never took the membership lock.
        legacy = grant(self.member, self.provider, self.other_read_role)
        legacy_scope_ids = sorted(legacy.scopes.values_list("pk", flat=True))
        before = state_fingerprint()

        with self.assertRaises(self.svc.ConcurrentGrantChange):
            with transaction.atomic():
                self.svc.sync_membership_grants(membership=self.membership, validated=validated)

        self.assertEqual(state_fingerprint(), before, "the refused reconciliation must write nothing")
        legacy.refresh_from_db()
        self.assertEqual(sorted(legacy.scopes.values_list("pk", flat=True)), legacy_scope_ids)
        self.assertFalse(self.grants_for(self.read_role).exists())

    def test_a_managed_aggregate_removed_after_validation_fails_the_write_closed(self):
        existing = grant(
            self.member,
            self.provider,
            self.read_role,
            reach=RoleGrant.REACH_MANAGED,
            assigned_tenants=[self.customer_a],
        )
        validated = self.validate(
            self.plan(managed=(self.managed(self.read_role, grant_id=existing.pk, tenants=(self.customer_z,)),))
        )
        RoleGrant._base_manager.filter(pk=existing.pk).delete()
        before = state_fingerprint()

        with self.assertRaises(self.svc.ConcurrentGrantChange):
            with transaction.atomic():
                self.svc.sync_membership_grants(membership=self.membership, validated=validated)

        self.assertEqual(state_fingerprint(), before)

    def test_the_race_is_a_service_error_the_form_can_render(self):
        """A ``ValueError`` here would be an HTTP 500; a caller that legitimately
        lost a race must get a re-render instead. ``ValueError`` stays reserved
        for programmer errors (design §4.2)."""
        validated = self.validate(self.plan(own=(self.own(self.read_role),)))
        grant(self.member, self.provider, self.other_read_role)
        with self.assertRaises(self.svc.MembershipServiceError) as ctx:
            with transaction.atomic():
                self.svc.sync_membership_grants(membership=self.membership, validated=validated)
        self.assertNotIsInstance(ctx.exception, ValueError)
        self.assertTrue(ctx.exception.messages)
        self.assertEqual([err.row_index for err in ctx.exception.errors], [None])


class GrantSyncRollbackTests(GrantSyncTestCase):
    """INV-15 / R-1 / R-3 — all-or-nothing, and authorization unchanged afterwards."""

    def failing_scope_write(self, *, tenant):
        """Fail the creation of one specific scope child, mid-write.

        A ``post_save`` receiver rather than a patched internal, so the test
        depends on nothing but the models: whatever the write phase is called
        internally, creating this scope raises and the surrounding transaction
        must unwind everything already written.
        """

        def explode(sender, instance, created, **kwargs):
            if created and instance.tenant_id == tenant.pk:
                raise RuntimeError("simulated failure while writing managed scope children")

        post_save.connect(explode, sender=RoleGrantScope, weak=False)
        self.addCleanup(post_save.disconnect, explode, sender=RoleGrantScope)

    def test_a_failure_in_the_managed_pass_unwinds_the_own_pass(self):
        """R-1 — own-reach rows are written first, so they are the proof."""
        self.failing_scope_write(tenant=self.customer_z)
        validated = self.validate(
            self.plan(
                own=(self.own(self.read_role),),
                managed=(self.managed(self.other_read_role, tenants=(self.customer_z,)),),
            )
        )
        with self.assert_writes_nothing("a write that failed part-way through"):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    self.svc.sync_membership_grants(membership=self.membership, validated=validated)
        self.assertFalse(self.grants_for(self.read_role).exists())

    def test_a_rolled_back_write_leaves_effective_authorization_unchanged(self):
        """R-3.

        The authorization *cache* is not rollback-safe and never was
        (``core/auth/cache.py`` publishes a new generation immediately), so the
        guarantee this design relies on is the weaker, true one: effective
        permissions recomputed from the database are unchanged. A bumped
        generation only forces that recompute.
        """
        grant(self.member, self.provider, self.read_role)
        before = set(effective_permissions(self.reload_member(), self.provider))
        self.assertEqual(before, {"assets.view_asset"})

        self.failing_scope_write(tenant=self.customer_z)
        # The plan both REVOKES the live own grant and ADDS an elevated one, so the
        # rollback has to restore a deletion as well as undo an insert.
        validated = self.validate(
            self.plan(
                own=(self.own(self.editor_role, reason="ops", valid_until=future()),),
                managed=(self.managed(self.other_read_role, tenants=(self.customer_z,)),),
            )
        )
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.svc.sync_membership_grants(membership=self.membership, validated=validated)

        self.assertEqual(set(effective_permissions(self.reload_member(), self.provider)), before)
        self.assertNotIn("assets.change_asset", effective_permissions(self.reload_member(), self.provider))

    def reload_member(self):
        """A fresh instance — ``applicable_grants`` memoises on the user object."""
        return self.member.__class__._base_manager.get(pk=self.member.pk)


class MembershipWriteAtomicityTests(GrantSyncTestCase):
    """INV-15 at the orchestration level: the membership row unwinds too."""

    def test_a_failed_grant_write_leaves_no_membership_behind(self):
        newcomer = self.make_user("sync-newcomer")

        def explode(sender, instance, created, **kwargs):
            if created and instance.scope_type == RoleGrantScope.SCOPE_OWN:
                raise RuntimeError("simulated failure while writing the own scope")

        post_save.connect(explode, sender=RoleGrantScope, weak=False)
        self.addCleanup(post_save.disconnect, explode, sender=RoleGrantScope)

        with self.assert_writes_nothing("a create whose grant write failed"):
            with self.assertRaises(RuntimeError):
                self.svc.execute_membership_write(
                    actor=self.superuser,
                    intent=self.svc.MembershipIntent(
                        tenant=self.provider,
                        user=newcomer,
                        own_roles=(self.own(self.read_role),),
                    ),
                )
        self.assertFalse(Membership.objects.filter(user=newcomer, tenant=self.provider).exists())

    def test_an_empty_intent_over_expired_history_writes_nothing(self):
        """INV-10 + INV-16 — a grant that lapsed purely by the clock is history.

        Nothing selects it, so a naive reconciler would "revoke" it and emit a
        spurious ``ObjectChange``; an expiry-aware one leaves it alone and writes
        no rows at all.
        """
        lapsed = RoleGrant.objects.create(
            membership=self.membership,
            role=self.read_role,
            valid_until=past(),
        )
        RoleGrantScope.objects.create(role_grant=lapsed, scope_type=RoleGrantScope.SCOPE_OWN)
        before = state_fingerprint()

        result = self.apply(self.plan())

        self.assertFalse(result.wrote_anything)
        self.assertEqual(state_fingerprint(), before)
