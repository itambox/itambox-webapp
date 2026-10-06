"""Service-boundary authorization for membership and RoleGrant writes (issue #86).

Every case here is constructed **without a form**: the services take an actor and
model instances, so a widget queryset, a formset prefix, or a disabled field can
never be the thing that enforced a boundary. That is the acceptance criterion
"services reject unauthorized actors and cross-tenant objects without relying on
form widgets/querysets".

The matrix rows referenced in the class docstrings are §5 of the issue #86 design
(`A1`–`A15`) and the invariants are §2 (`INV-1`…`INV-16`). Per
the private design-docs security-test-expectations.md every rejection asserts
both the exception *and* that no row moved — ``assert_writes_nothing`` compares
full ``Membership`` / ``RoleGrant`` / ``RoleGrantScope`` / ``User`` /
``ObjectChange`` row tuples, so an in-place mutation cannot hide behind an
unchanged count.
"""

from django.test import TestCase

from core.tests.mixins import grant
from organization.models import Membership, Role, RoleGrant, RoleGrantScope
from users.models import GroupMembership, UserGroup

from ._membership_service_adversarial_helpers import ServiceWorldMixin, future, membership_services, past


class ServiceAuthzTestCase(ServiceWorldMixin, TestCase):
    prefix = "authz"

    def setUp(self):
        self.svc = membership_services()
        self.setup_service_world(self.prefix)

    def own(self, role, **kwargs):
        return self.svc.OwnGrantSpec(role=role, **kwargs)

    def managed(self, role, **kwargs):
        return self.svc.ManagedGrantSpec(role=role, **kwargs)

    def plan(self, *, own=(), managed=()):
        return self.svc.GrantPlan(own=tuple(own), managed=tuple(managed))

    def intent(self, **kwargs):
        kwargs.setdefault("tenant", self.provider)
        return self.svc.MembershipIntent(**kwargs)

    def validate(self, *, actor, plan, principal_tenant=None, membership=None, **kwargs):
        return self.svc.validate_grant_plan(
            actor=actor,
            principal_tenant=principal_tenant or self.provider,
            plan=plan,
            membership=membership,
            **kwargs,
        )

    @staticmethod
    def messages(exc):
        return " ".join(exc.messages)


class ActorAuthorizationBoundaryTests(ServiceAuthzTestCase):
    """Matrix A1/A2 — the gate the form never had (design §5, "Deliberate non-change").

    Before #86 a directly-constructed ``MembershipForm`` with an unauthorized actor
    and a fresh email would *write*; the view's ``_authorized_tenant()`` was the
    only gate. These cases pin the gate at the service, where no caller can skip
    it.
    """

    prefix = "authz-actor"

    def setUp(self):
        super().setUp()
        self.viewer = self.actor_with("authz-viewer", ["organization.view_membership"])
        self.adder = self.actor_with("authz-adder", ["organization.add_membership"])
        self.changer = self.actor_with("authz-changer", ["organization.change_membership"])
        self.target = self.membership_for(self.member, self.provider)

    def test_create_without_add_membership_is_rejected_and_writes_nothing(self):
        """A1."""
        newcomer = self.make_user("authz-newcomer")
        with self.assert_writes_nothing("an unauthorized create"):
            with self.assertRaises(self.svc.ActorNotAuthorized):
                self.svc.execute_membership_write(
                    actor=self.viewer,
                    intent=self.intent(user=newcomer),
                )
        self.assertFalse(Membership.objects.filter(user=newcomer).exists())

    def test_update_without_change_membership_is_rejected_and_writes_nothing(self):
        """A2."""
        with self.assert_writes_nothing("an unauthorized update"):
            with self.assertRaises(self.svc.ActorNotAuthorized):
                self.svc.execute_membership_write(
                    actor=self.viewer,
                    intent=self.intent(user=self.member, own_roles=(self.own(self.read_role),)),
                    membership=self.target,
                )

    def test_add_membership_permission_does_not_authorize_an_update(self):
        """B-3 — the create permission is not an update permission."""
        with self.assert_writes_nothing("an update attempted with only add_membership"):
            with self.assertRaises(self.svc.ActorNotAuthorized):
                self.svc.execute_membership_write(
                    actor=self.adder,
                    intent=self.intent(user=self.member, own_roles=(self.own(self.read_role),)),
                    membership=self.target,
                )

    def test_change_membership_permission_does_not_authorize_a_create(self):
        """The mirror of B-3 — an update permission is not a create permission."""
        newcomer = self.make_user("authz-newcomer-2")
        with self.assert_writes_nothing("a create attempted with only change_membership"):
            with self.assertRaises(self.svc.ActorNotAuthorized):
                self.svc.execute_membership_write(actor=self.changer, intent=self.intent(user=newcomer))

    def test_deferred_grant_entry_point_gates_on_change_membership(self):
        """``apply_membership_grants`` is a full write path, not a shortcut.

        ``MembershipForm.save(commit=False)`` defers grant reconciliation onto
        ``save_m2m``; if that entry point skipped A2 it would be a hole straight
        through the matrix (design §4.3).
        """
        with self.assert_writes_nothing("an unauthorized deferred grant apply"):
            with self.assertRaises(self.svc.ActorNotAuthorized):
                self.svc.apply_membership_grants(
                    actor=self.viewer,
                    membership=self.target,
                    plan=self.plan(own=(self.own(self.read_role),)),
                    previous_is_active=True,
                )

    def test_plan_is_read_only_even_for_an_authorized_actor(self):
        """INV-1 — planning decides, it never writes."""
        with self.assert_writes_nothing("a successful plan"):
            self.svc.plan_membership_write(
                actor=self.superuser,
                intent=self.intent(user=self.member, own_roles=(self.own(self.read_role),)),
                membership=self.target,
            )

    def test_null_actor_and_superuser_stay_trusted(self):
        """INV-2 — verbatim preservation of the trusted-null/superuser bypass."""
        for actor in (None, self.superuser):
            with self.subTest(actor=actor):
                self.svc.authorize_membership_write(actor=actor, tenant=self.provider, creating=True)
                self.svc.authorize_membership_write(actor=actor, tenant=self.provider, creating=False)
                self.assertTrue(self.svc.may_manage_memberships(actor=actor, tenant=self.provider, creating=True))

    def test_may_manage_memberships_agrees_with_the_raising_gate(self):
        """A3 — the boolean the form's oracle defence needs must not diverge."""
        cases = [
            (self.viewer, True, False),
            (self.viewer, False, False),
            (self.adder, True, True),
            (self.adder, False, False),
            (self.changer, True, False),
            (self.changer, False, True),
        ]
        for actor, creating, expected in cases:
            with self.subTest(actor=actor.username, creating=creating):
                self.assertEqual(
                    self.svc.may_manage_memberships(actor=actor, tenant=self.provider, creating=creating),
                    expected,
                )
                if expected:
                    self.svc.authorize_membership_write(actor=actor, tenant=self.provider, creating=creating)
                else:
                    with self.assertRaises(self.svc.ActorNotAuthorized):
                        self.svc.authorize_membership_write(actor=actor, tenant=self.provider, creating=creating)

    def test_authorization_is_per_tenant_not_global(self):
        """Holding ``add_membership`` in one tenant authorizes nothing in another."""
        with self.assert_writes_nothing("a create into a tenant the actor cannot manage"):
            with self.assertRaises(self.svc.ActorNotAuthorized):
                self.svc.execute_membership_write(
                    actor=self.adder,
                    intent=self.svc.MembershipIntent(tenant=self.rival, user=self.member),
                )


class UpdateBindsToTheMembershipsTenantTests(ServiceAuthzTestCase):
    """A2, bound to the row being written rather than to the submitted tenant.

    An update names two tenants: the one the caller submitted and the one the
    membership actually lives in. Only the second is the tenant whose members are
    being changed, so it is the one A2 has to be evaluated against and the one
    ``validate_grant_plan`` must reason about — otherwise an actor who manages a
    provider can prove authority *there* and have the grant written into a
    customer they hold nothing in. The submitted tenant is then refused outright:
    a membership's tenant is immutable, so a mismatch is a caller bug, never a
    silent rewrite.
    """

    prefix = "authz-bind"

    def setUp(self):
        super().setUp()
        #: Shared down the management edge, so it is assignable in BOTH tenants —
        #: nothing but the tenant binding can reject the write.
        self.shared_role = self.make_role("Bind shared reader", ["assets.view_asset"], shared_with_managed=True)
        self.customer_membership = self.membership_for(self.member, self.customer_a)

    def cross_tenant_intent(self):
        """Authority claimed at the provider, applied to a customer's membership."""
        return self.svc.MembershipIntent(
            tenant=self.provider,
            user=self.member,
            own_roles=(self.own(self.shared_role),),
        )

    def test_provider_only_actor_cannot_update_a_managed_customers_membership(self):
        """The BLOCKER: A1/A2 must not be satisfiable in the wrong tenant."""
        actor = self.actor_with(
            "authz-bind-provider-only",
            ["organization.change_membership", "assets.view_asset"],
        )
        self.assertFalse(actor.has_perm("organization.change_membership", obj=self.customer_a))

        with self.assert_writes_nothing("an update authorized only at the supplied provider"):
            with self.assertRaises(self.svc.ActorNotAuthorized):
                self.svc.execute_membership_write(
                    actor=actor,
                    intent=self.cross_tenant_intent(),
                    membership=self.customer_membership,
                )
        self.assertFalse(RoleGrant.objects.filter(membership=self.customer_membership).exists())

    def test_planning_the_same_call_discloses_nothing_and_writes_nothing(self):
        """The read-only entry point refuses at the same point, so a form that
        plans in ``clean()`` cannot render the customer's state either."""
        actor = self.actor_with(
            "authz-bind-planner",
            ["organization.change_membership", "assets.view_asset"],
        )
        with self.assert_writes_nothing("planning an update outside the membership's tenant"):
            with self.assertRaises(self.svc.ActorNotAuthorized):
                self.svc.plan_membership_write(
                    actor=actor,
                    intent=self.cross_tenant_intent(),
                    membership=self.customer_membership,
                )

    def test_a_mismatched_intent_tenant_is_refused_even_for_a_trusted_actor(self):
        """Authorization is not the only guard: the tenant is immutable, so a
        superuser gets a ``ValueError`` rather than a write into either tenant."""
        with self.assert_writes_nothing("an update whose intent names a different tenant"):
            with self.assertRaises(ValueError):
                self.svc.execute_membership_write(
                    actor=self.superuser,
                    intent=self.cross_tenant_intent(),
                    membership=self.customer_membership,
                )

    def test_the_matching_tenant_still_writes_exactly_as_before(self):
        """Positive control — the binding is a boundary, not a new refusal."""
        result = self.svc.execute_membership_write(
            actor=self.superuser,
            intent=self.svc.MembershipIntent(
                tenant=self.customer_a,
                user=self.member,
                own_roles=(self.own(self.shared_role),),
            ),
            membership=self.customer_membership,
        )

        self.assertTrue(result.grants.wrote_anything)
        self.assertEqual(
            sorted(
                scope.scope_type
                for item in RoleGrant.objects.filter(membership=self.customer_membership)
                for scope in item.scopes.all()
            ),
            [RoleGrantScope.SCOPE_OWN],
        )

    def test_the_plan_reasons_about_the_memberships_own_tenant(self):
        """The gate and the grant validation must agree on one principal tenant,
        so the token the write phase receives names the row's tenant."""
        plan = self.svc.plan_membership_write(
            actor=self.superuser,
            intent=self.svc.MembershipIntent(tenant=self.customer_a, user=self.member),
            membership=self.customer_membership,
        )

        self.assertEqual(plan.validated_grants.principal_tenant, self.customer_a)


class UnauthorizedActorLearnsNothingTests(ServiceAuthzTestCase):
    """Ordering is part of the contract (design §5, INV-12).

    ``DuplicateMembership`` names the tenant an account already belongs to and
    ``AmbiguousIdentity`` confirms a duplicated email. Both are *revealing by
    construction*, so A1/A2 must fail fast and must never be aggregated with
    them. If an implementation collects all errors and raises them together, an
    unauthorized actor gets a membership oracle for free.
    """

    prefix = "authz-oracle"

    def setUp(self):
        super().setUp()
        self.victim = self.member
        self.victim.username = "authz-oracle-victim"
        self.victim.email = "victim@oracle.test"
        self.victim.save(update_fields=["username", "email"])
        self.membership_for(self.victim, self.provider)
        self.outsider = self.actor_with("authz-outsider", ["organization.view_membership"])

    def new_identity_intent(self, email):
        return self.intent(new_identity=self.svc.NewIdentitySpec(email=email, first_name="F", last_name="L"))

    def test_duplicate_membership_is_not_revealed_to_an_unauthorized_actor(self):
        with self.assert_writes_nothing("an unauthorized duplicate-membership probe"):
            with self.assertRaises(self.svc.ActorNotAuthorized) as ctx:
                self.svc.plan_membership_write(
                    actor=self.outsider,
                    intent=self.new_identity_intent("victim@oracle.test"),
                )
        text = self.messages(ctx.exception).lower()
        self.assertNotIn("already a member", text)
        self.assertNotIn(self.victim.username.lower(), text)

    def test_ambiguous_identity_is_not_revealed_to_an_unauthorized_actor(self):
        self.make_user("authz-dupe-1", email="dupe@oracle.test")
        self.make_user("authz-dupe-2", email="DUPE@oracle.test")
        with self.assert_writes_nothing("an unauthorized ambiguity probe"):
            with self.assertRaises(self.svc.ActorNotAuthorized) as ctx:
                self.svc.plan_membership_write(
                    actor=self.outsider,
                    intent=self.new_identity_intent("dupe@oracle.test"),
                )
        self.assertNotIn("more than one account", self.messages(ctx.exception).lower())

    def test_grant_rejections_are_not_reported_before_the_actor_gate(self):
        """A cross-tenant role in the plan must not tell an unauthorized actor
        which roles exist in the target tenant."""
        candidate = self.make_user("authz-oracle-new")
        with self.assert_writes_nothing("an unauthorized plan carrying a foreign role"):
            with self.assertRaises(self.svc.ActorNotAuthorized) as ctx:
                self.svc.plan_membership_write(
                    actor=self.outsider,
                    intent=self.intent(
                        user=candidate,
                        own_roles=(self.own(self.rival_role),),
                    ),
                )
        self.assertNotIn(self.rival_role.name.lower(), self.messages(ctx.exception).lower())

    def test_the_oracle_exists_only_once_the_actor_is_authorized(self):
        """Positive control: without this, the two tests above would also pass if
        the duplicate check had simply been deleted."""
        authorized = self.actor_with("authz-oracle-admin", ["organization.add_membership"])
        with self.assert_writes_nothing("a rejected duplicate create"):
            with self.assertRaises(self.svc.DuplicateMembership):
                self.svc.plan_membership_write(
                    actor=authorized,
                    intent=self.new_identity_intent("victim@oracle.test"),
                )


class CrossTenantObjectRejectionTests(ServiceAuthzTestCase):
    """Matrix A9–A11 — data-integrity rules that bind superusers too.

    The actor is a superuser throughout, so nothing here can be mistaken for an
    escalation guard firing: these are reach and ownership rules about the
    *objects*, and they are exactly the rules a form queryset used to enforce by
    omission.
    """

    prefix = "authz-xtenant"

    def setUp(self):
        super().setUp()
        self.target = self.membership_for(self.member, self.provider)
        self.shared_role = self.make_role("Shared reader", ["assets.view_asset"], shared_with_managed=True)

    def test_own_role_owned_by_an_unrelated_tenant_is_rejected(self):
        """X-1."""
        with self.assert_writes_nothing("an own grant of a foreign tenant's role"):
            with self.assertRaises(self.svc.CrossTenantObject):
                self.validate(
                    actor=self.superuser,
                    plan=self.plan(own=(self.own(self.rival_role),)),
                    membership=self.target,
                )

    def test_shared_role_is_rejected_on_a_customer_of_another_provider(self):
        """X-2 — sharing reaches down the management edge, not sideways."""
        rival_membership = self.membership_for(self.member, self.rival_customer)
        with self.assert_writes_nothing("a shared role offered outside its managed subtree"):
            with self.assertRaises(self.svc.CrossTenantObject):
                self.validate(
                    actor=self.superuser,
                    principal_tenant=self.rival_customer,
                    plan=self.plan(own=(self.own(self.shared_role),)),
                    membership=rival_membership,
                )

    def test_shared_role_is_accepted_inside_the_providers_own_subtree(self):
        """The positive half of INV-3 — the rule above is a boundary, not a ban."""
        managed_membership = self.membership_for(self.member, self.customer_a)
        self.validate(
            actor=self.superuser,
            principal_tenant=self.customer_a,
            plan=self.plan(own=(self.own(self.shared_role),)),
            membership=managed_membership,
        )
        self.assertTrue(self.svc.role_assignable_in(self.shared_role, self.customer_a))
        self.assertFalse(self.svc.role_assignable_in(self.shared_role, self.rival_customer))
        self.assertFalse(self.svc.role_assignable_in(self.rival_role, self.provider))

    def test_managed_row_targeting_another_providers_customer_is_rejected(self):
        """X-3 — and the message must name the offending tenant so an admin can fix it."""
        with self.assert_writes_nothing("a managed row reaching into a rival's customer"):
            with self.assertRaises(self.svc.CrossTenantObject) as ctx:
                self.validate(
                    actor=self.superuser,
                    plan=self.plan(managed=(self.managed(self.read_role, tenants=(self.rival_customer,)),)),
                    membership=self.target,
                )
        self.assertIn(self.rival_customer.name, self.messages(ctx.exception))

    def test_managed_row_on_a_non_provider_tenant_is_rejected(self):
        """X-4 / A11 — managed reach needs a provider principal."""
        customer_membership = self.membership_for(self.member, self.customer_a)
        customer_role = self.make_role("Customer local", ["assets.view_asset"], tenant=self.customer_a)
        with self.assert_writes_nothing("a managed row on a non-provider tenant"):
            with self.assertRaises(self.svc.CrossTenantObject):
                self.validate(
                    actor=self.superuser,
                    principal_tenant=self.customer_a,
                    plan=self.plan(managed=(self.managed(customer_role, tenants=(self.customer_z,)),)),
                    membership=customer_membership,
                )

    def test_tenant_group_outside_the_providers_subtree_is_rejected(self):
        """X-7 — an empty expansion is a rejection, never a silently empty grant."""
        foreign_group = self.make_tenant_group("authz foreign group", "authz-foreign-group")
        self.rival_customer.group = foreign_group
        self.rival_customer.save(update_fields=["group"])
        with self.assert_writes_nothing("a group scope that expands to nothing"):
            with self.assertRaises(self.svc.CrossTenantObject):
                self.validate(
                    actor=self.superuser,
                    plan=self.plan(
                        managed=(
                            self.managed(
                                self.read_role,
                                scope=RoleGrantScope.SCOPE_TENANT_GROUP,
                                scope_group=foreign_group,
                            ),
                        )
                    ),
                    membership=self.target,
                )

    def test_duplicate_managed_role_is_located_at_the_second_row(self):
        """X-8 — the formset row index, not the spec's position in the plan.

        The indices below are deliberately non-contiguous: ``row_index`` must be
        the index inside ``managed_formset.forms`` (design §4.2), so echoing a
        plan offset would produce ``1`` here instead of ``3``.
        """
        with self.assert_writes_nothing("a plan granting one role twice"):
            with self.assertRaises(self.svc.MembershipServiceError) as ctx:
                self.validate(
                    actor=self.superuser,
                    plan=self.plan(
                        managed=(
                            self.managed(self.read_role, tenants=(self.customer_a,), row_index=0),
                            self.managed(self.read_role, tenants=(self.customer_z,), row_index=3),
                        )
                    ),
                    membership=self.target,
                )
        self.assertEqual([err.row_index for err in ctx.exception.errors], [3])

    def test_explicit_row_without_targets_is_rejected_on_its_own_field(self):
        """Shape rejections carry ``field`` so the form can render them in place."""
        with self.assert_writes_nothing("an explicit managed row with no tenants"):
            with self.assertRaises(self.svc.MembershipServiceError) as ctx:
                self.validate(
                    actor=self.superuser,
                    plan=self.plan(managed=(self.managed(self.read_role, tenants=(), row_index=2),)),
                    membership=self.target,
                )
        located = [(err.field, err.row_index) for err in ctx.exception.errors]
        self.assertTrue(all(row_index == 2 for _field, row_index in located), located)
        self.assertTrue(all(field for field, _row_index in located), located)


class EscalationBoundaryTests(ServiceAuthzTestCase):
    """Matrix A4–A8 — "you cannot grant what you do not hold", at the service.

    These mirror ``test_escalation_surface.py``'s guard-level cases one level up,
    where the plan (not a formset row) is the unit, so the aggregation and
    ordering rules are exercised too.
    """

    prefix = "authz-esc"

    def setUp(self):
        super().setUp()
        self.target = self.membership_for(self.member, self.provider)

    def test_actor_cannot_grant_a_permission_they_do_not_hold(self):
        """E-1."""
        actor = self.actor_with("esc-reader", ["organization.change_membership", "assets.view_asset"])
        with self.assert_writes_nothing("an own grant beyond the actor's permissions"):
            with self.assertRaises(self.svc.EscalationDenied) as ctx:
                self.validate(
                    actor=actor,
                    plan=self.plan(own=(self.own(self.editor_role, reason="ops", valid_until=future()),)),
                    membership=self.target,
                )
        self.assertIn("assets.change_asset", self.messages(ctx.exception))

    def test_own_tenant_admin_cannot_amplify_read_only_customer_coverage(self):
        """E-2 — pins ``guards.py``'s per-target permission proof."""
        actor = self.actor_with(
            "esc-split-admin",
            [
                "organization.change_membership",
                "organization.add_rolegrant",
                "assets.view_asset",
                "assets.change_asset",
            ],
            coverage=[self.customer_a],
            coverage_permissions=["assets.view_asset"],
        )
        with self.assert_writes_nothing("a managed row amplifying read-only coverage"):
            with self.assertRaises(self.svc.EscalationDenied) as ctx:
                self.validate(
                    actor=actor,
                    plan=self.plan(
                        managed=(
                            self.managed(
                                self.editor_role,
                                tenants=(self.customer_a,),
                                reason="ops",
                                valid_until=future(),
                            ),
                        )
                    ),
                    membership=self.target,
                )
        self.assertIn("assets.change_asset", self.messages(ctx.exception))

    def test_managed_reach_requires_rolegrant_administration(self):
        """E-3."""
        actor = self.actor_with(
            "esc-no-gate",
            ["organization.change_membership", "assets.view_asset"],
            coverage=[self.customer_a],
            coverage_permissions=["assets.view_asset"],
        )
        with self.assert_writes_nothing("a managed row without rolegrant administration"):
            with self.assertRaises(self.svc.EscalationDenied):
                self.validate(
                    actor=actor,
                    plan=self.plan(managed=(self.managed(self.read_role, tenants=(self.customer_a,)),)),
                    membership=self.target,
                )

    def test_narrow_actor_cannot_request_dynamic_coverage(self):
        """E-4 — ``tenant_group`` and ``all_managed`` both need all-managed authority."""
        group = self.make_tenant_group("authz esc group", "authz-esc-group")
        self.customer_a.group = group
        self.customer_a.save(update_fields=["group"])
        actor = self.actor_with(
            "esc-narrow",
            ["organization.change_membership", "organization.add_rolegrant", "assets.view_asset"],
            coverage=[self.customer_a],
            coverage_permissions=["assets.view_asset"],
        )
        dynamic_rows = [
            self.managed(self.read_role, scope=RoleGrantScope.SCOPE_TENANT_GROUP, scope_group=group),
            self.managed(self.read_role, scope=RoleGrantScope.SCOPE_ALL_MANAGED),
        ]
        for row in dynamic_rows:
            with self.subTest(scope=row.scope):
                with self.assert_writes_nothing("a dynamic scope beyond the actor's authority"):
                    with self.assertRaises(self.svc.EscalationDenied):
                        self.validate(actor=actor, plan=self.plan(managed=(row,)), membership=self.target)

    def test_matching_all_managed_authority_is_accepted(self):
        """E-5 — the boundary is authority, not scope shape."""
        actor = self.actor_with(
            "esc-all-managed",
            ["organization.change_membership", "organization.add_rolegrant", "assets.view_asset"],
            all_managed=True,
            coverage_permissions=["assets.view_asset"],
        )
        self.validate(
            actor=actor,
            plan=self.plan(managed=(self.managed(self.read_role, scope=RoleGrantScope.SCOPE_ALL_MANAGED),)),
            membership=self.target,
        )

    def test_self_edit_cannot_bootstrap_authority_row_by_row(self):
        """E-6 / INV-1 — decide-then-write, proved on the actor's own membership.

        Row 1 grants a role carrying ``organization.add_rolegrant`` and is
        individually valid (asserted first, so the case cannot pass vacuously).
        Row 2 reaches a tenant outside the actor's coverage. Because
        ``organization.signals.clear_role_grant_cache`` drops the actor's memoised
        ``applicable_grants`` the moment a self-grant is written — and
        ``applicable_grants`` sees uncommitted rows on the same connection — a
        validate-immediately-before-each-write implementation would judge row 2
        against the state row 1 just created. The whole plan must be rejected with
        row 1 unwritten.
        """
        actor = self.actor_with(
            "esc-self",
            [
                "organization.change_membership",
                "organization.add_rolegrant",
                "assets.view_asset",
            ],
            coverage=[self.customer_a],
            coverage_permissions=["assets.view_asset"],
        )
        own_membership = Membership.objects.get(user=actor, tenant=self.provider)
        grant_admin_role = self.make_role("Grant delegator", ["organization.add_rolegrant"])
        row_one = self.own(grant_admin_role, reason="delegation", valid_until=future())
        row_two = self.managed(self.other_read_role, tenants=(self.customer_z,), row_index=1)

        # Row 1 alone is genuinely acceptable — this is what makes the combined
        # rejection meaningful rather than an artefact of an invalid first row.
        self.validate(actor=actor, plan=self.plan(own=(row_one,)), membership=own_membership)

        with self.assert_writes_nothing("a self-edit whose second row escalates"):
            with self.assertRaises(self.svc.EscalationDenied):
                self.validate(
                    actor=actor,
                    plan=self.plan(own=(row_one,), managed=(row_two,)),
                    membership=own_membership,
                )
            with self.assertRaises(self.svc.EscalationDenied):
                self.svc.execute_membership_write(
                    actor=actor,
                    intent=self.intent(user=actor, own_roles=(row_one,), managed_grants=(row_two,)),
                    membership=own_membership,
                )
        self.assertFalse(RoleGrant.objects.filter(membership=own_membership, role=grant_admin_role).exists())

    def test_superuser_and_null_actor_skip_escalation_but_not_data_integrity(self):
        """E-7 — A4–A8 are bypassed; A9 and A13 are not."""
        for actor in (None, self.superuser):
            with self.subTest(actor=actor):
                self.validate(
                    actor=actor,
                    plan=self.plan(
                        managed=(
                            self.managed(
                                self.editor_role,
                                scope=RoleGrantScope.SCOPE_ALL_MANAGED,
                                reason="root",
                                valid_until=future(),
                            ),
                        )
                    ),
                    membership=self.target,
                )
                with self.assert_writes_nothing("a superuser plan carrying a foreign role"):
                    with self.assertRaises(self.svc.CrossTenantObject):
                        self.validate(
                            actor=actor,
                            plan=self.plan(own=(self.own(self.rival_role),)),
                            membership=self.target,
                        )
                with self.assert_writes_nothing("a superuser plan missing elevated metadata"):
                    with self.assertRaises(self.svc.ElevatedGrantIncomplete):
                        self.validate(
                            actor=actor,
                            plan=self.plan(own=(self.own(self.editor_role),)),
                            membership=self.target,
                        )


class ElevatedGrantMetadataTests(ServiceAuthzTestCase):
    """Matrix A13 / INV-5 — the own and managed halves differ, on purpose.

    Own reach validates only grants this plan would CREATE and stores metadata
    only for privileged roles. Managed reach validates EVERY privileged row, new
    or surviving, and stores metadata verbatim. Collapsing either half into the
    other is a regression the design forbids explicitly: narrowing the managed
    half re-opens an uncaught ``RoleGrant.clean`` ``ValidationError`` inside the
    write, and widening it would silently make a time-boxed managed reach
    permanent.
    """

    prefix = "authz-elev"

    def setUp(self):
        super().setUp()
        self.target = self.membership_for(self.member, self.provider)

    def test_new_privileged_own_grant_requires_reason_and_future_expiry(self):
        for spec in (
            self.own(self.editor_role),
            self.own(self.editor_role, reason="", valid_until=future()),
            self.own(self.editor_role, reason="ops", valid_until=None),
            self.own(self.editor_role, reason="ops", valid_until=past()),
        ):
            with self.subTest(reason=spec.reason, valid_until=spec.valid_until):
                with self.assert_writes_nothing("an incomplete elevated own grant"):
                    with self.assertRaises(self.svc.ElevatedGrantIncomplete):
                        self.validate(actor=self.superuser, plan=self.plan(own=(spec,)), membership=self.target)

    def test_surviving_privileged_own_grant_is_not_revalidated(self):
        """The own half is diffed against the live rows (membership_form.py:658)."""
        grant(self.member, self.provider, self.editor_role)
        self.validate(
            actor=self.superuser,
            plan=self.plan(own=(self.own(self.editor_role),)),
            membership=self.target,
        )

    def test_surviving_privileged_managed_row_is_revalidated_every_submit(self):
        """The managed half has no such exemption — this is the crash INV-5 names."""
        existing = grant(
            self.member,
            self.provider,
            self.editor_role,
            reach=RoleGrant.REACH_MANAGED,
            assigned_tenants=[self.customer_a],
        )
        with self.assert_writes_nothing("a surviving elevated managed row stripped of its metadata"):
            with self.assertRaises(self.svc.ElevatedGrantIncomplete):
                self.validate(
                    actor=self.superuser,
                    plan=self.plan(
                        managed=(
                            self.managed(
                                self.editor_role,
                                grant_id=existing.pk,
                                tenants=(self.customer_a,),
                                reason="",
                                valid_until=None,
                            ),
                        )
                    ),
                    membership=self.target,
                )

    def test_view_only_managed_row_may_still_carry_an_operator_expiry(self):
        """A view-only managed grant legitimately carries an operator-chosen expiry;
        applying the own half's privilege gate here would quietly make it permanent."""
        self.validate(
            actor=self.superuser,
            plan=self.plan(
                managed=(
                    self.managed(
                        self.read_role,
                        tenants=(self.customer_a,),
                        reason="temporary handover",
                        valid_until=future(),
                    ),
                )
            ),
            membership=self.target,
        )

    def test_elevated_rejections_stay_on_the_main_form(self):
        """Own-reach rejections carry no ``row_index`` — they belong in
        ``form.non_field_errors()`` / the main form's fields, exactly as today."""
        with self.assertRaises(self.svc.ElevatedGrantIncomplete) as ctx:
            self.validate(
                actor=self.superuser,
                plan=self.plan(own=(self.own(self.editor_role),)),
                membership=self.target,
            )
        self.assertEqual([err.row_index for err in ctx.exception.errors], [None] * len(ctx.exception.errors))


class ReactivationInheritedGroupTests(ServiceAuthzTestCase):
    """Matrix A12 / INV-14 — reactivation re-validates every retained live group.

    Switching a membership back on is equivalent to re-adding the principal to
    every group it still belongs to, so it must pass the same inheritance guard.
    The transition is DERIVED (from the locked row, or from ``previous_is_active``
    failing closed) and never submitted, so no caller can opt out.
    """

    prefix = "authz-react"

    def setUp(self):
        super().setUp()
        self.actor = self.actor_with(
            "react-admin",
            ["organization.change_membership", "organization.add_rolegrant", "assets.delete_asset"],
        )
        self.target_user = self.member
        self.membership = self.membership_for(self.target_user, self.provider, is_active=False)
        self.group = UserGroup.objects.create(tenant=self.provider, name="Projected asset administrators")
        GroupMembership.objects.create(user_group=self.group, membership=self.membership)
        projected_role = self.make_role("Projected asset deleter", ["assets.delete_asset"])
        group_grant = RoleGrant.objects.create(user_group=self.group, role=projected_role)
        RoleGrantScope.objects.create(
            role_grant=group_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )

    def reactivation_intent(self):
        return self.intent(user=self.target_user, is_active=True)

    def test_reactivation_is_blocked_when_a_retained_group_projects_beyond_the_actor(self):
        """E-8 — and the membership must still be inactive afterwards."""
        with self.assert_writes_nothing("a blocked reactivation"):
            with self.assertRaises(self.svc.EscalationDenied) as ctx:
                self.svc.execute_membership_write(
                    actor=self.actor,
                    intent=self.reactivation_intent(),
                    membership=self.membership,
                )
        self.assertIn("outside your own reach", self.messages(ctx.exception).lower())
        self.membership.refresh_from_db()
        self.assertFalse(self.membership.is_active)

    def test_no_caller_can_opt_out_of_the_reactivation_guard(self):
        """INV-14 — the transition is derived from the stored row, never submitted.

        ``execute_membership_write`` takes no ``revalidate_inherited_groups``
        argument and ``GrantPlan`` carries no such field, so "just don't ask for
        it" is not a way past the guard. The rejection above already proves the
        derivation happens with nothing passed in.
        """
        self.assertNotIn("revalidate_inherited_groups", getattr(self.svc.GrantPlan, "__dataclass_fields__", {}))

    def test_reactivation_succeeds_once_the_actor_holds_the_projected_reach(self):
        grant(
            self.actor,
            self.provider,
            self.make_role("React coverage", ["assets.delete_asset"]),
            reach=RoleGrant.REACH_MANAGED,
            assigned_tenants=[self.customer_a],
        )
        result = self.svc.execute_membership_write(
            actor=self.actor,
            intent=self.reactivation_intent(),
            membership=self.membership,
        )
        self.assertTrue(result.membership.is_active)
        self.membership.refresh_from_db()
        self.assertTrue(self.membership.is_active)

    def test_inactive_or_deleted_groups_are_inert(self):
        """E-9 — an inert group restores no permissions, so it blocks nothing."""
        self.group.is_active = False
        self.group.save(update_fields=["is_active"])
        self.svc.execute_membership_write(
            actor=self.actor,
            intent=self.reactivation_intent(),
            membership=self.membership,
        )
        self.membership.refresh_from_db()
        self.assertTrue(self.membership.is_active)

    def test_soft_deleted_group_is_inert(self):
        self.group.delete()
        self.svc.execute_membership_write(
            actor=self.actor,
            intent=self.reactivation_intent(),
            membership=self.membership,
        )
        self.membership.refresh_from_db()
        self.assertTrue(self.membership.is_active)

    def test_deferred_grant_path_fails_closed_when_the_transition_is_unknown(self):
        """INV-14's ``apply_membership_grants`` half.

        The caller has already written the row, so the locked row can no longer
        show ``False -> True``. ``previous_is_active=None`` means "unknown" and
        must re-validate every retained live group rather than skip the guard.
        """
        Membership.objects.filter(pk=self.membership.pk).update(is_active=True)
        self.membership.refresh_from_db()
        with self.assert_writes_nothing("a deferred apply with an unknown prior state"):
            with self.assertRaises(self.svc.EscalationDenied):
                self.svc.apply_membership_grants(
                    actor=self.actor,
                    membership=self.membership,
                    plan=self.plan(),
                    previous_is_active=None,
                )

    def test_deferred_grant_path_skips_the_guard_only_on_a_positive_assertion(self):
        """Silence never disables the guard; ``previous_is_active=True`` does."""
        Membership.objects.filter(pk=self.membership.pk).update(is_active=True)
        self.membership.refresh_from_db()
        self.svc.apply_membership_grants(
            actor=self.actor,
            membership=self.membership,
            plan=self.plan(),
            previous_is_active=True,
        )


class RoleAssignabilityTests(ServiceAuthzTestCase):
    """INV-3 as a truth table, on the symbol the form now imports rather than owns."""

    prefix = "authz-assign"

    def test_role_assignable_in_truth_table(self):
        owned = self.read_role
        shared = self.make_role("Assignable shared", ["assets.view_asset"], shared_with_managed=True)
        unshared = self.make_role("Assignable unshared", ["assets.view_asset"])
        non_provider_role = self.make_role("Assignable local", ["assets.view_asset"], tenant=self.customer_z)
        cases = [
            (owned, self.provider, True),
            (shared, self.customer_a, True),
            (shared, self.rival_customer, False),
            (unshared, self.customer_a, False),
            (non_provider_role, self.provider, False),
            (self.rival_role, self.customer_a, False),
        ]
        for role, tenant, expected in cases:
            with self.subTest(role=role.name, tenant=tenant.slug):
                self.assertIs(self.svc.role_assignable_in(role, tenant), expected)

    def test_assignable_roles_qs_offers_own_plus_shared_down_only(self):
        shared = self.make_role("Queryset shared", ["assets.view_asset"], shared_with_managed=True)
        deleted = self.make_role("Queryset deleted", ["assets.view_asset"])
        deleted.delete()
        offered = set(self.svc.assignable_roles_qs(self.customer_a).values_list("pk", flat=True))
        self.assertIn(shared.pk, offered)
        self.assertNotIn(self.read_role.pk, offered)
        self.assertNotIn(self.rival_role.pk, offered)
        self.assertNotIn(deleted.pk, offered)
        self.assertEqual(
            offered,
            {
                role.pk
                for role in Role._base_manager.filter(deleted_at__isnull=True)
                if self.svc.role_assignable_in(role, self.customer_a)
            },
        )
