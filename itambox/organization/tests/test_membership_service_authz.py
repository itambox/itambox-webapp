"""``validate_grant_plan`` — the read-only half of the RBAC service (issue #86).

Every case here calls the service directly with model instances and an actor:
no form, no widget queryset, no ``cleaned_data``. That is the point of the
extraction — the boundary has to hold for a tampered POST, a directly-built
form, and any future API caller alike.

Per ``docs/development/security-test-expectations.md`` each rejection asserts
BOTH the typed refusal AND that nothing was written.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core.models import ObjectChange
from core.tests.mixins import grant
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant, TenantGroup
from organization.services.errors import (
    CrossTenantObject,
    ElevatedGrantIncomplete,
    EscalationDenied,
    MembershipServiceError,
)
from organization.services.rolegrants import (
    GrantPlan,
    ManagedGrantSpec,
    OwnGrantSpec,
    ValidatedGrantPlan,
    validate_grant_plan,
)
from users.models import GroupMembership, UserGroup

User = get_user_model()

ADMIN_PERMS = ["assets.view_asset", "assets.change_asset", "assets.delete_asset"]


class _PlanTestBase(TestCase):
    """Provider + two customers + a superuser; helpers for the write-nothing
    assertion every boundary case owes."""

    def setUp(self):
        self.superuser = User.objects.create_superuser(username="root")
        self.target = User.objects.create_user(username="target")
        self.provider = Tenant.objects.create(name="Provider", slug="provider", is_provider=True)
        self.customer_a = Tenant.objects.create(name="Customer A", slug="cust-a", managed_by=self.provider)
        self.customer_b = Tenant.objects.create(name="Customer B", slug="cust-b", managed_by=self.provider)
        self.read_role = Role.objects.create(
            tenant=self.provider,
            name="Reader",
            permissions=["assets.view_asset"],
        )
        self.admin_role = Role.objects.create(tenant=self.provider, name="Admin", permissions=ADMIN_PERMS)

    def _counts(self):
        return (
            RoleGrant.objects.count(),
            RoleGrantScope.objects.count(),
            Membership.objects.count(),
            ObjectChange.objects.count(),
        )

    def assert_wrote_nothing(self, before):
        self.assertEqual(self._counts(), before, "a rejected plan must not write a single row")

    def _membership(self, tenant=None):
        return Membership.objects.create(user=self.target, tenant=tenant or self.provider)

    def _validate(self, **kwargs):
        kwargs.setdefault("actor", self.superuser)
        kwargs.setdefault("principal_tenant", self.provider)
        return validate_grant_plan(**kwargs)

    @staticmethod
    def _future():
        return timezone.now() + timedelta(days=30)


class ValidatedPlanShapeTests(_PlanTestBase):
    def test_an_accepted_plan_returns_a_token_bound_to_the_membership(self):
        membership = self._membership()
        plan = GrantPlan(own=(OwnGrantSpec(role=self.read_role),))

        validated = self._validate(plan=plan, membership=membership)

        self.assertIsInstance(validated, ValidatedGrantPlan)
        self.assertEqual(validated.membership_id, membership.pk)
        self.assertIs(validated.plan, plan)
        self.assertIs(validated.principal_tenant, self.provider)
        self.assertFalse(validated.revalidate_inherited_groups)

    def test_a_create_leaves_the_token_unbound_until_the_row_exists(self):
        validated = self._validate(plan=GrantPlan(own=(OwnGrantSpec(role=self.read_role),)))

        self.assertIsNone(validated.membership_id)
        self.assertEqual(validated.existing_own_role_ids, frozenset())
        self.assertEqual(validated.existing_managed_grant_ids, frozenset())

    def test_the_token_records_the_live_state_validation_reasoned_about(self):
        existing = grant(self.target, self.provider, self.read_role, reach="own")
        managed = grant(
            self.target,
            self.provider,
            self.admin_role,
            reach="managed",
            assigned_tenants=[self.customer_a],
        )

        validated = self._validate(
            plan=GrantPlan(own=(OwnGrantSpec(role=self.read_role),)),
            membership=existing.membership,
        )

        self.assertEqual(validated.existing_own_role_ids, frozenset({self.read_role.pk}))
        self.assertEqual(validated.existing_managed_grant_ids, frozenset({managed.pk}))

    def test_expired_grants_are_inert_history_and_never_enter_the_token(self):
        """INV-10 — an expired aggregate is neither seeded nor revoked."""
        expired = grant(self.target, self.provider, self.read_role, reach="own")
        RoleGrant.objects.filter(pk=expired.pk).update(valid_until=timezone.now() - timedelta(days=1))

        validated = self._validate(plan=GrantPlan(), membership=expired.membership)

        self.assertEqual(validated.existing_own_role_ids, frozenset())

    def test_validation_writes_nothing_even_on_the_happy_path(self):
        membership = self._membership()
        before = self._counts()

        self._validate(
            plan=GrantPlan(
                own=(OwnGrantSpec(role=self.read_role),),
                managed=(ManagedGrantSpec(role=self.read_role, tenants=(self.customer_a,)),),
            ),
            membership=membership,
        )

        self.assertEqual(self._counts(), before)


class PlanShapeRejectionTests(_PlanTestBase):
    def test_two_managed_rows_for_one_role_are_reported_on_the_second_row(self):
        """X-8 — the duplicate rule moved off the formset onto the plan."""
        before = self._counts()
        plan = GrantPlan(
            managed=(
                ManagedGrantSpec(role=self.read_role, tenants=(self.customer_a,), row_index=0),
                ManagedGrantSpec(role=self.read_role, tenants=(self.customer_b,), row_index=3),
            )
        )

        with self.assertRaises(MembershipServiceError) as ctx:
            self._validate(plan=plan)

        (err,) = ctx.exception.errors
        self.assertEqual(err.row_index, 3)
        self.assertIn("granted twice", err.message)
        self.assert_wrote_nothing(before)

    def test_managed_row_on_a_non_provider_tenant_is_rejected(self):
        """X-4 / A11 — managed reach needs a provider principal."""
        before = self._counts()
        customer_role = Role.objects.create(
            tenant=self.customer_a,
            name="Local",
            permissions=["assets.view_asset"],
        )

        with self.assertRaises(CrossTenantObject) as ctx:
            self._validate(
                principal_tenant=self.customer_a,
                plan=GrantPlan(managed=(ManagedGrantSpec(role=customer_role, row_index=0),)),
            )

        self.assertIn("provider", " ".join(ctx.exception.messages).lower())
        self.assertEqual(ctx.exception.errors[0].row_index, 0)
        self.assert_wrote_nothing(before)

    def test_explicit_row_without_tenants_is_reported_on_its_field(self):
        with self.assertRaises(MembershipServiceError) as ctx:
            self._validate(plan=GrantPlan(managed=(ManagedGrantSpec(role=self.read_role, row_index=2),)))

        (err,) = ctx.exception.errors
        self.assertEqual((err.field, err.row_index), ("assigned_tenants", 2))

    def test_group_row_without_a_group_is_reported_on_its_field(self):
        with self.assertRaises(MembershipServiceError) as ctx:
            self._validate(
                plan=GrantPlan(
                    managed=(
                        ManagedGrantSpec(
                            role=self.read_role,
                            scope=RoleGrantScope.SCOPE_TENANT_GROUP,
                            row_index=1,
                        ),
                    )
                )
            )

        (err,) = ctx.exception.errors
        self.assertEqual((err.field, err.row_index), ("scope_group", 1))


class CrossTenantRejectionTests(_PlanTestBase):
    def test_own_role_owned_by_an_unrelated_tenant_is_rejected(self):
        """X-1."""
        before = self._counts()
        unrelated = Tenant.objects.create(name="Unrelated", slug="unrelated")
        stray = Role.objects.create(tenant=unrelated, name="Stray", permissions=["assets.view_asset"])

        with self.assertRaises(CrossTenantObject) as ctx:
            self._validate(plan=GrantPlan(own=(OwnGrantSpec(role=stray),)))

        self.assertIn("Stray", " ".join(ctx.exception.messages))
        self.assertIsNone(ctx.exception.errors[0].row_index)
        self.assert_wrote_nothing(before)

    def test_shared_provider_role_on_a_customer_of_another_provider_is_rejected(self):
        """X-2."""
        other_provider = Tenant.objects.create(name="Other MSP", slug="other-msp", is_provider=True)
        foreign_customer = Tenant.objects.create(name="Foreign", slug="foreign", managed_by=other_provider)
        shared = Role.objects.create(
            tenant=self.provider,
            name="Shared",
            permissions=["assets.view_asset"],
            shared_with_managed=True,
        )

        with self.assertRaises(CrossTenantObject):
            self._validate(
                principal_tenant=foreign_customer,
                plan=GrantPlan(own=(OwnGrantSpec(role=shared),)),
            )

    def test_explicit_target_managed_by_a_different_provider_names_the_offender(self):
        """X-3 / A10."""
        before = self._counts()
        other_provider = Tenant.objects.create(name="Other MSP", slug="other-msp", is_provider=True)
        foreign = Tenant.objects.create(name="Foreign Customer", slug="foreign", managed_by=other_provider)

        with self.assertRaises(CrossTenantObject) as ctx:
            self._validate(
                plan=GrantPlan(
                    managed=(
                        ManagedGrantSpec(
                            role=self.read_role,
                            tenants=(self.customer_a, foreign),
                            row_index=0,
                        ),
                    )
                )
            )

        message = " ".join(ctx.exception.messages)
        self.assertIn("Foreign Customer", message)
        self.assertNotIn("Customer A", message)
        self.assertEqual(ctx.exception.errors[0].row_index, 0)
        self.assert_wrote_nothing(before)

    def test_managed_row_with_a_role_shared_down_onto_a_nested_provider_is_a_message_not_a_crash(self):
        """A11 — mirrors ``RoleGrantScope.clean``'s managed-shape rule.

        This is the one place the plan is deliberately stricter than the old
        form: the shape passed assignability and then raised an uncaught
        ``ValidationError`` from ``RoleGrantScope.clean`` inside the write (an
        HTTP 500). It is now located on the row.

        The depth-1 management rule lives only in ``Tenant.clean`` — there is no
        DB constraint — so a drifted row is reachable in a real installation and
        is built here the same way: with a ``queryset.update()`` that bypasses
        the pre-save validator.
        """
        nested = Tenant.objects.create(name="Nested", slug="nested", managed_by=self.provider)
        sub = Tenant.objects.create(name="Sub", slug="sub", managed_by=self.provider)
        Tenant._base_manager.filter(pk=nested.pk).update(is_provider=True)
        Tenant._base_manager.filter(pk=sub.pk).update(managed_by=nested)
        nested = Tenant._base_manager.get(pk=nested.pk)
        sub = Tenant._base_manager.get(pk=sub.pk)
        shared = Role.objects.create(
            tenant=self.provider,
            name="SharedAdmin",
            permissions=["assets.view_asset"],
            shared_with_managed=True,
        )
        before = self._counts()

        with self.assertRaises(CrossTenantObject) as ctx:
            self._validate(
                principal_tenant=nested,
                plan=GrantPlan(managed=(ManagedGrantSpec(role=shared, tenants=(sub,), row_index=0),)),
            )

        self.assertEqual(ctx.exception.errors[0].row_index, 0)
        self.assert_wrote_nothing(before)

    def test_tenant_group_outside_the_provider_subtree_covers_nothing_and_is_rejected(self):
        """X-7 — an expansion that reaches none of the provider's customers."""
        before = self._counts()
        empty_group = TenantGroup.objects.create(name="Elsewhere", slug="elsewhere")

        with self.assertRaises(CrossTenantObject) as ctx:
            self._validate(
                plan=GrantPlan(
                    managed=(
                        ManagedGrantSpec(
                            role=self.read_role,
                            scope=RoleGrantScope.SCOPE_TENANT_GROUP,
                            scope_group=empty_group,
                            row_index=0,
                        ),
                    )
                )
            )

        self.assertEqual(ctx.exception.errors[0].field, "scope_group")
        self.assert_wrote_nothing(before)

    def test_tenant_group_covering_a_managed_customer_is_accepted(self):
        group = TenantGroup.objects.create(name="North", slug="north")
        self.customer_a.group = group
        self.customer_a.save(update_fields=["group"])

        validated = self._validate(
            plan=GrantPlan(
                managed=(
                    ManagedGrantSpec(
                        role=self.read_role,
                        scope=RoleGrantScope.SCOPE_TENANT_GROUP,
                        scope_group=group,
                        row_index=0,
                    ),
                )
            )
        )

        self.assertIsInstance(validated, ValidatedGrantPlan)


class ElevatedMetadataTests(_PlanTestBase):
    """A13 / INV-5 — the own and managed halves differ and the difference is
    load-bearing, not an inconsistency to tidy away."""

    def test_new_privileged_own_grant_requires_a_reason_and_a_future_expiry(self):
        before = self._counts()

        with self.assertRaises(ElevatedGrantIncomplete) as ctx:
            self._validate(plan=GrantPlan(own=(OwnGrantSpec(role=self.admin_role),)))

        fields = {e.field for e in ctx.exception.errors}
        self.assertEqual(fields, {"reason", "valid_until"})
        self.assertTrue(all(e.row_index is None for e in ctx.exception.errors))
        self.assert_wrote_nothing(before)

    def test_past_expiry_on_a_new_privileged_own_grant_is_rejected(self):
        with self.assertRaises(ElevatedGrantIncomplete) as ctx:
            self._validate(
                plan=GrantPlan(
                    own=(
                        OwnGrantSpec(
                            role=self.admin_role,
                            reason="on call",
                            valid_until=timezone.now() - timedelta(days=1),
                        ),
                    )
                )
            )

        self.assertIn("future", " ".join(ctx.exception.messages))

    def test_surviving_privileged_own_grant_is_not_re_validated(self):
        """INV-5 own half: only grants this plan would CREATE are checked."""
        existing = grant(self.target, self.provider, self.admin_role, reach="own")

        validated = self._validate(
            plan=GrantPlan(own=(OwnGrantSpec(role=self.admin_role),)),
            membership=existing.membership,
        )

        self.assertEqual(validated.existing_own_role_ids, frozenset({self.admin_role.pk}))

    def test_every_privileged_managed_row_is_validated_new_or_surviving(self):
        """INV-5 managed half: narrowing this to creates re-opens the
        ``RoleGrant.clean`` crash on a surviving row's past expiry."""
        surviving = grant(
            self.target,
            self.provider,
            self.admin_role,
            reach="managed",
            assigned_tenants=[self.customer_a],
        )

        with self.assertRaises(ElevatedGrantIncomplete) as ctx:
            self._validate(
                plan=GrantPlan(
                    managed=(
                        ManagedGrantSpec(
                            role=self.admin_role,
                            grant_id=surviving.pk,
                            tenants=(self.customer_a,),
                            reason="still on call",
                            valid_until=timezone.now() - timedelta(days=1),
                            row_index=0,
                        ),
                    )
                ),
                membership=surviving.membership,
            )

        self.assertEqual(ctx.exception.errors[0].row_index, 0)

    def test_non_privileged_managed_row_may_carry_an_operator_chosen_expiry(self):
        """INV-5: applying the own-reach privilege gate here would silently turn
        a time-boxed managed reach into a permanent one."""
        validated = self._validate(
            plan=GrantPlan(
                managed=(
                    ManagedGrantSpec(
                        role=self.read_role,
                        tenants=(self.customer_a,),
                        reason="temporary audit",
                        valid_until=self._future(),
                        row_index=0,
                    ),
                )
            )
        )

        self.assertEqual(validated.plan.managed[0].valid_until.date(), self._future().date())


class EscalationTests(_PlanTestBase):
    """A4-A8 — ``core.auth.guards`` decisions, reached through the service."""

    def setUp(self):
        super().setUp()
        self.actor = User.objects.create_user(username="msp-operator")
        self.grantee_membership = Membership.objects.create(user=self.target, tenant=self.provider)

    def _actor_holds_at_provider(self, permissions, *, name="ActorOwnRole"):
        """An own-reach grant: what the actor may do INSIDE the provider."""
        role = Role.objects.create(tenant=self.provider, name=name, permissions=permissions)
        return grant(self.actor, self.provider, role, reach="own")

    def _actor_reaches(self, permissions, *, name, managed_scope=None, assigned_tenants=None):
        """A managed-reach grant: what the actor may do in the provider's customers."""
        role = Role.objects.create(tenant=self.provider, name=name, permissions=permissions)
        return grant(
            self.actor,
            self.provider,
            role,
            reach="managed",
            managed_scope=managed_scope,
            assigned_tenants=assigned_tenants,
        )

    def test_actor_cannot_grant_a_permission_it_does_not_hold(self):
        """E-1 / A4."""
        self._actor_holds_at_provider(["assets.view_asset"])
        before = self._counts()

        with self.assertRaises(EscalationDenied) as ctx:
            self._validate(
                actor=self.actor,
                plan=GrantPlan(own=(OwnGrantSpec(role=self.admin_role, reason="why", valid_until=self._future()),)),
                membership=self.grantee_membership,
            )

        self.assertIn("cannot grant permissions you do not hold", " ".join(ctx.exception.messages))
        self.assertIsNone(ctx.exception.errors[0].row_index)
        self.assert_wrote_nothing(before)

    def test_own_tenant_authority_plus_read_only_coverage_cannot_manufacture_admin(self):
        """E-2 — pins guards.py's per-target permission proof: reach into a
        customer is not authority to delegate everything a role carries there."""
        self._actor_holds_at_provider([*ADMIN_PERMS, "organization.add_rolegrant"], name="OwnAdmin")
        self._actor_reaches(["assets.view_asset"], name="CustomerReader", assigned_tenants=[self.customer_a])

        with self.assertRaises(EscalationDenied):
            self._validate(
                actor=self.actor,
                plan=GrantPlan(
                    managed=(
                        ManagedGrantSpec(
                            role=self.admin_role,
                            tenants=(self.customer_a,),
                            reason="why",
                            valid_until=self._future(),
                            row_index=0,
                        ),
                    )
                ),
                membership=self.grantee_membership,
            )

    def test_actor_without_rolegrant_rights_cannot_submit_any_managed_row(self):
        """E-3 / A5."""
        self._actor_holds_at_provider(["assets.view_asset"])

        with self.assertRaises(EscalationDenied) as ctx:
            self._validate(
                actor=self.actor,
                plan=GrantPlan(
                    managed=(ManagedGrantSpec(role=self.read_role, tenants=(self.customer_a,), row_index=0),)
                ),
                membership=self.grantee_membership,
            )

        self.assertEqual(ctx.exception.errors[0].row_index, 0)

    def test_narrow_actor_cannot_request_a_dynamic_scope(self):
        """E-4 / A7-A8 — single-customer reach is not all-managed authority."""
        self._actor_holds_at_provider(["assets.view_asset", "organization.add_rolegrant"])
        self._actor_reaches(["assets.view_asset"], name="NarrowManager", assigned_tenants=[self.customer_a])

        with self.assertRaises(EscalationDenied):
            self._validate(
                actor=self.actor,
                plan=GrantPlan(
                    managed=(
                        ManagedGrantSpec(
                            role=self.read_role,
                            scope=RoleGrantScope.SCOPE_ALL_MANAGED,
                            row_index=0,
                        ),
                    )
                ),
                membership=self.grantee_membership,
            )

    def test_actor_with_matching_all_managed_authority_may_request_all_managed(self):
        """E-5."""
        self._actor_holds_at_provider(["assets.view_asset", "organization.add_rolegrant"])
        self._actor_reaches(
            ["assets.view_asset"],
            name="AllManagedManager",
            managed_scope=RoleGrantScope.SCOPE_ALL_MANAGED,
        )

        validated = self._validate(
            actor=self.actor,
            plan=GrantPlan(
                managed=(
                    ManagedGrantSpec(
                        role=self.read_role,
                        scope=RoleGrantScope.SCOPE_ALL_MANAGED,
                        row_index=0,
                    ),
                )
            ),
            membership=self.grantee_membership,
        )

        self.assertIsInstance(validated, ValidatedGrantPlan)

    def test_self_escalation_cannot_bootstrap_row_by_row(self):
        """E-6 / INV-1 — the whole plan is decided before anything is written, so
        a role granted by row 1 can never authorise row 2."""
        self._actor_holds_at_provider(["assets.view_asset"])
        own_membership = Membership.objects.get(user=self.actor, tenant=self.provider)
        bootstrap_role = Role.objects.create(
            tenant=self.provider,
            name="Bootstrap",
            permissions=["assets.view_asset", "organization.add_rolegrant"],
        )
        before = self._counts()

        with self.assertRaises(EscalationDenied):
            self._validate(
                actor=self.actor,
                plan=GrantPlan(
                    own=(OwnGrantSpec(role=bootstrap_role, reason="why", valid_until=self._future()),),
                    managed=(ManagedGrantSpec(role=self.read_role, tenants=(self.customer_a,), row_index=0),),
                ),
                membership=own_membership,
            )

        self.assert_wrote_nothing(before)

    def test_superuser_and_trusted_null_actor_skip_escalation_but_not_integrity(self):
        """E-7 / INV-2 — A4-A8 are skipped; A9-A13 still apply."""
        for actor in (self.superuser, None):
            with self.subTest(actor=actor):
                self._validate(
                    actor=actor,
                    plan=GrantPlan(own=(OwnGrantSpec(role=self.admin_role, reason="why", valid_until=self._future()),)),
                    membership=self.grantee_membership,
                )
                with self.assertRaises(ElevatedGrantIncomplete):
                    self._validate(
                        actor=actor,
                        plan=GrantPlan(own=(OwnGrantSpec(role=self.admin_role),)),
                        membership=self.grantee_membership,
                    )


class ReactivationInheritanceTests(_PlanTestBase):
    """A12 / INV-14 — switching a membership back on re-grants every retained
    live group, so it must pass the same inheritance guard."""

    def setUp(self):
        super().setUp()
        # An actor with narrow (single-customer) reach: enough to manage
        # memberships, not enough to restore an all-managed group projection.
        self.actor = User.objects.create_user(username="provider-admin")
        actor_role = Role.objects.create(
            tenant=self.provider,
            name="ProviderAdmin",
            permissions=[
                "assets.view_asset",
                "assets.delete_asset",
                "organization.add_rolegrant",
                "organization.change_membership",
            ],
        )
        grant(self.actor, self.provider, actor_role, reach="own")
        grant(
            self.actor,
            self.provider,
            actor_role,
            reach="managed",
            assigned_tenants=[self.customer_a],
        )
        self.membership = Membership.objects.create(user=self.target, tenant=self.provider, is_active=False)
        self.group = UserGroup.objects.create(tenant=self.provider, name="Field Techs")
        GroupMembership.objects.create(membership=self.membership, user_group=self.group)
        projected_role = Role.objects.create(
            tenant=self.provider,
            name="Projected",
            permissions=["assets.delete_asset"],
        )
        group_grant = RoleGrant.objects.create(user_group=self.group, role=projected_role)
        RoleGrantScope.objects.create(role_grant=group_grant, scope_type=RoleGrantScope.SCOPE_ALL_MANAGED)

    def test_reactivation_revalidates_every_retained_live_group(self):
        """E-8 — the projection reaches beyond the actor, so the plan is refused."""
        before = self._counts()

        with self.assertRaises(EscalationDenied) as ctx:
            self._validate(
                actor=self.actor,
                plan=GrantPlan(),
                membership=self.membership,
                revalidate_inherited_groups=True,
            )

        self.assertIsNone(ctx.exception.errors[0].row_index)
        self.assertIsNone(ctx.exception.errors[0].field)
        self.assert_wrote_nothing(before)

    def test_the_same_plan_is_accepted_when_the_guard_is_not_triggered(self):
        """The membership is only re-validated on a ``False -> True`` transition;
        an ordinary edit of an active membership is untouched."""
        validated = self._validate(
            actor=self.actor,
            plan=GrantPlan(),
            membership=self.membership,
            revalidate_inherited_groups=False,
        )

        self.assertFalse(validated.revalidate_inherited_groups)

    def test_inactive_or_deleted_groups_stay_inert(self):
        """E-9 — nothing is restored, so nothing needs re-authorising."""
        self.group.is_active = False
        self.group.save(update_fields=["is_active"])

        validated = self._validate(
            actor=self.actor,
            plan=GrantPlan(),
            membership=self.membership,
            revalidate_inherited_groups=True,
        )

        self.assertTrue(validated.revalidate_inherited_groups)

    def test_superuser_reactivation_skips_the_inheritance_guard(self):
        self._validate(
            actor=self.superuser,
            plan=GrantPlan(),
            membership=self.membership,
            revalidate_inherited_groups=True,
        )


class AggregationTests(_PlanTestBase):
    """Failures are reported together so an admin sees them all at once, and a
    mixed-cause rejection stays catchable as the service base class."""

    def test_multiple_failures_of_one_kind_share_that_kind(self):
        unrelated = Tenant.objects.create(name="Unrelated", slug="unrelated")
        stray_a = Role.objects.create(tenant=unrelated, name="StrayA", permissions=[])
        stray_b = Role.objects.create(tenant=unrelated, name="StrayB", permissions=[])

        with self.assertRaises(CrossTenantObject) as ctx:
            self._validate(plan=GrantPlan(own=(OwnGrantSpec(role=stray_a), OwnGrantSpec(role=stray_b))))

        self.assertEqual(len(ctx.exception.errors), 2)

    def test_mixed_causes_fall_back_to_the_base_class_but_keep_their_codes(self):
        actor = User.objects.create_user(username="mixed-actor")
        grant(actor, self.provider, self.read_role, reach="own")
        membership = Membership.objects.create(user=self.target, tenant=self.provider)

        with self.assertRaises(MembershipServiceError) as ctx:
            self._validate(
                actor=actor,
                plan=GrantPlan(
                    own=(OwnGrantSpec(role=self.admin_role),),
                    managed=(ManagedGrantSpec(role=self.read_role, tenants=(self.customer_a,), row_index=0),),
                ),
                membership=membership,
            )

        codes = {e.code for e in ctx.exception.errors}
        self.assertIn("elevated_grant_incomplete", codes)
        self.assertIn("escalation_denied", codes)
        self.assertIs(type(ctx.exception), MembershipServiceError)

    def test_identical_messages_in_the_same_place_are_reported_once(self):
        actor = User.objects.create_user(username="dedup-actor")
        grant(actor, self.provider, self.read_role, reach="own")
        membership = Membership.objects.create(user=self.target, tenant=self.provider)
        twin = Role.objects.create(tenant=self.provider, name="Twin", permissions=ADMIN_PERMS)

        with self.assertRaises(EscalationDenied) as ctx:
            self._validate(
                actor=actor,
                plan=GrantPlan(
                    own=(
                        OwnGrantSpec(role=self.admin_role, reason="why", valid_until=self._future()),
                        OwnGrantSpec(role=twin, reason="why", valid_until=self._future()),
                    )
                ),
                membership=membership,
            )

        self.assertEqual(len(ctx.exception.messages), len(set(ctx.exception.messages)))


class TamperedIdentifierTests(_PlanTestBase):
    """INV-7 — an id that is not a live managed grant of THIS membership is
    inert; it can never be used to reach another membership's row."""

    def test_grant_id_of_another_membership_is_ignored_by_the_plan(self):
        """X-5, validation half: the plan accepts the row as a NEW aggregate and
        never records the foreign id as existing state."""
        other_user = User.objects.create_user(username="somebody-else")
        foreign = grant(
            other_user,
            self.provider,
            self.read_role,
            reach="managed",
            assigned_tenants=[self.customer_a],
        )
        membership = Membership.objects.create(user=self.target, tenant=self.provider)

        validated = self._validate(
            plan=GrantPlan(
                managed=(
                    ManagedGrantSpec(
                        role=self.read_role,
                        grant_id=foreign.pk,
                        tenants=(self.customer_b,),
                        row_index=0,
                    ),
                )
            ),
            membership=membership,
        )

        self.assertEqual(validated.existing_managed_grant_ids, frozenset())

    def test_expired_grant_id_of_this_membership_is_ignored_by_the_plan(self):
        """X-6 — INV-10 keeps the expired aggregate out of the reconciliation."""
        expired = grant(
            self.target,
            self.provider,
            self.read_role,
            reach="managed",
            assigned_tenants=[self.customer_a],
        )
        RoleGrant.objects.filter(pk=expired.pk).update(valid_until=timezone.now() - timedelta(days=1))

        validated = self._validate(
            plan=GrantPlan(
                managed=(
                    ManagedGrantSpec(
                        role=self.read_role,
                        grant_id=expired.pk,
                        tenants=(self.customer_a,),
                        row_index=0,
                    ),
                )
            ),
            membership=expired.membership,
        )

        self.assertEqual(validated.existing_managed_grant_ids, frozenset())
