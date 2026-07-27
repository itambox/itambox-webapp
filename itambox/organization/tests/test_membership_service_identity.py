"""``organization.services.membership`` — orchestration, identity, boundaries.

Identity resolution and the membership row are exercised here independently of
the grant reconciliation (which ``test_grant_sync_idempotency.py`` owns) and
independently of ``MembershipForm``: the service takes an actor and model
instances, so the boundary holds for a directly-built form, a tampered POST, and
any future API caller alike.

Per ``docs/development/security-test-expectations.md`` every boundary case
asserts BOTH the rejection and that nothing was written.
"""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

import users.services as users_services
from core.models import ObjectChange
from core.tasks.context import TaskContext
from core.tests.mixins import grant
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.rbac import effective_permissions
from organization.services import membership as membership_service
from organization.services import rolegrants as rolegrants_service
from organization.services.errors import (
    ActorNotAuthorized,
    AmbiguousIdentity,
    DuplicateMembership,
    EscalationDenied,
    MembershipServiceError,
)
from organization.services.membership import (
    MembershipIntent,
    MembershipWriteResult,
    NewIdentitySpec,
    apply_membership_grants,
    authorize_membership_write,
    execute_membership_write,
    may_manage_memberships,
    plan_membership_write,
    resolve_identity,
)
from organization.services.rolegrants import GrantPlan, ManagedGrantSpec, OwnGrantSpec
from users.models import GroupMembership, UserGroup

User = get_user_model()


class _MembershipServiceTestBase(TestCase):
    def setUp(self):
        self.actor = User.objects.create_superuser(username="root")
        self.provider = Tenant.objects.create(name="Provider", slug="provider", is_provider=True)
        self.customer = Tenant.objects.create(name="Customer", slug="customer", managed_by=self.provider)
        self.read_role = Role.objects.create(
            tenant=self.provider,
            name="Reader",
            permissions=["assets.view_asset"],
        )

    def _counts(self):
        return (
            User.objects.count(),
            Membership.objects.count(),
            RoleGrant.objects.count(),
            RoleGrantScope.objects.count(),
            ObjectChange.objects.count(),
        )

    def assert_wrote_nothing(self, before):
        self.assertEqual(self._counts(), before, "a rejected write must not leave a single row behind")

    def intent(self, **overrides):
        overrides.setdefault("tenant", self.provider)
        return MembershipIntent(**overrides)


class IdentityResolutionTests(_MembershipServiceTestBase):
    def test_resolve_identity_returns_none_for_an_unused_email(self):
        self.assertIsNone(resolve_identity(spec=NewIdentitySpec(email="nobody@example.com")))

    def test_resolve_identity_is_case_insensitive(self):
        existing = User.objects.create_user(username="mixed", email="Person@Example.com")

        self.assertEqual(resolve_identity(spec=NewIdentitySpec(email="person@EXAMPLE.com")), existing)

    def test_resolve_identity_fails_closed_on_a_duplicated_email(self):
        User.objects.create_user(username="a", email="dup@example.com")
        User.objects.create_user(username="b", email="dup@example.com")

        with self.assertRaises(AmbiguousIdentity) as ctx:
            resolve_identity(spec=NewIdentitySpec(email="dup@example.com"))

        self.assertEqual(ctx.exception.errors[0].field, "new_user_email")


class InlineIdentityWriteTests(_MembershipServiceTestBase):
    def test_new_identity_creates_a_passwordless_account_and_its_membership(self):
        """I-1."""
        result = execute_membership_write(
            actor=self.actor,
            intent=self.intent(
                new_identity=NewIdentitySpec(email="fresh@example.com", first_name="Fresh", last_name="Hire"),
                own_roles=(OwnGrantSpec(role=self.read_role),),
            ),
        )

        self.assertIsInstance(result, MembershipWriteResult)
        self.assertTrue(result.identity_created)
        self.assertTrue(result.membership_created)
        created = User.objects.get(email="fresh@example.com")
        self.assertFalse(created.has_usable_password())
        self.assertEqual(result.membership.user_id, created.pk)
        self.assertEqual(
            sorted(scope.scope_type for g in result.membership.role_grants.all() for scope in g.scopes.all()),
            ["own"],
        )

    def test_an_existing_account_is_reused_and_never_overwritten(self):
        """I-2 — get-or-create semantics, profile fields untouched."""
        existing = User.objects.create_user(
            username="veteran",
            email="Veteran@Example.com",
            first_name="Original",
            last_name="Name",
        )

        result = execute_membership_write(
            actor=self.actor,
            intent=self.intent(
                new_identity=NewIdentitySpec(email="veteran@example.com", first_name="New", last_name="Label"),
            ),
        )

        existing.refresh_from_db()
        self.assertFalse(result.identity_created)
        self.assertEqual(result.membership.user_id, existing.pk)
        self.assertEqual((existing.first_name, existing.last_name), ("Original", "Name"))
        self.assertEqual(User.objects.filter(email__iexact="veteran@example.com").count(), 1)

    def test_an_ambiguous_email_writes_nothing_at_all(self):
        """I-3."""
        User.objects.create_user(username="a", email="dup@example.com")
        User.objects.create_user(username="b", email="dup@example.com")
        before = self._counts()

        with self.assertRaises(AmbiguousIdentity):
            execute_membership_write(
                actor=self.actor,
                intent=self.intent(
                    new_identity=NewIdentitySpec(email="dup@example.com", first_name="X", last_name="Y"),
                    own_roles=(OwnGrantSpec(role=self.read_role),),
                ),
            )

        self.assert_wrote_nothing(before)

    def test_a_very_long_email_still_fits_the_username_field(self):
        """I-4 — the length-safe handle lives in ``users.services``; the point
        here is that the membership service never bypasses it."""
        long_email = f"{'a' * 200}@example.com"

        result = execute_membership_write(
            actor=self.actor,
            intent=self.intent(new_identity=NewIdentitySpec(email=long_email, first_name="L", last_name="E")),
        )

        created = result.membership.user
        self.assertLessEqual(len(created.username), User._meta.get_field("username").max_length)
        self.assertEqual(created.email, long_email)

    def test_an_explicit_user_wins_over_a_new_identity_block(self):
        """I-5 — documented precedence, pinned."""
        chosen = User.objects.create_user(username="chosen", email="chosen@example.com")
        User.objects.create_user(username="other", email="other@example.com")

        result = execute_membership_write(
            actor=self.actor,
            intent=self.intent(
                user=chosen,
                new_identity=NewIdentitySpec(email="other@example.com"),
            ),
        )

        self.assertEqual(result.membership.user_id, chosen.pk)
        self.assertFalse(result.identity_created)

    def test_the_insert_race_re_resolves_to_the_winning_account(self):
        """Y-3 — driven deterministically, exactly as ``users`` tests drive it."""
        winner = User.objects.create_user(username="race@example.com", email="race@example.com")
        real = users_services.resolve_existing_user
        calls = {"n": 0}

        def flaky(email):
            calls["n"] += 1
            return None if calls["n"] == 1 else real(email)

        with (
            mock.patch("users.services.resolve_existing_user", side_effect=flaky),
            mock.patch("users.services._fitting_username", return_value="race@example.com"),
        ):
            result = execute_membership_write(
                actor=self.actor,
                intent=self.intent(new_identity=NewIdentitySpec(email="race@example.com")),
            )

        self.assertFalse(result.identity_created)
        self.assertEqual(result.membership.user_id, winner.pk)
        self.assertEqual(User.objects.filter(email__iexact="race@example.com").count(), 1)


class DuplicateMembershipTests(_MembershipServiceTestBase):
    def test_a_repeated_create_raises_and_leaves_exactly_one_row(self):
        """Y-2 / A15."""
        member = User.objects.create_user(username="member", email="member@example.com")
        execute_membership_write(actor=self.actor, intent=self.intent(user=member))
        before = self._counts()

        with self.assertRaises(DuplicateMembership):
            execute_membership_write(actor=self.actor, intent=self.intent(user=member))

        self.assert_wrote_nothing(before)
        self.assertEqual(Membership.objects.filter(user=member, tenant=self.provider).count(), 1)

    def test_a_concurrent_insert_surfaces_as_duplicate_membership_not_a_500(self):
        """The ``(user, tenant)`` unique constraint is the concurrency backstop.

        Driven deterministically, the same way ``users`` drives its identity
        race: the plan's read briefly sees nothing (the winner raced in right
        after), so the insert is the first thing to notice. It is isolated in
        its own savepoint, so the surrounding transaction survives to report a
        typed error instead of a 500.
        """
        member = User.objects.create_user(username="member", email="member@example.com")
        Membership.objects.create(user=member, tenant=self.provider)
        real = membership_service._membership_exists
        calls = {"n": 0}

        def flaky(user, tenant):
            calls["n"] += 1
            return False if calls["n"] == 1 else real(user, tenant)

        with (
            mock.patch.object(membership_service, "_membership_exists", side_effect=flaky),
            self.assertRaises(DuplicateMembership),
        ):
            execute_membership_write(actor=self.actor, intent=self.intent(user=member))

        self.assertEqual(Membership.objects.filter(user=member, tenant=self.provider).count(), 1)

    def test_an_update_never_collides_with_its_own_row(self):
        """A15 — the duplicate check is a create-path rule."""
        member = User.objects.create_user(username="member", email="member@example.com")
        membership = Membership.objects.create(user=member, tenant=self.provider)

        result = execute_membership_write(
            actor=self.actor,
            intent=self.intent(user=member, own_roles=(OwnGrantSpec(role=self.read_role),)),
            membership=membership,
        )

        self.assertFalse(result.membership_created)
        self.assertEqual(result.membership.pk, membership.pk)


class AuthorizationBoundaryTests(_MembershipServiceTestBase):
    """A1-A3 / B-1..B-4 — the gate the form only consulted for its oracle
    defence is now load-bearing at the service boundary."""

    def setUp(self):
        super().setUp()
        self.outsider = User.objects.create_user(username="outsider")
        self.member = User.objects.create_user(username="member", email="member@example.com")

    def _actor_with(self, permissions, name):
        role = Role.objects.create(tenant=self.provider, name=name, permissions=permissions)
        return grant(self.outsider, self.provider, role, reach="own")

    def test_create_without_add_membership_is_refused_and_writes_nothing(self):
        """B-1."""
        self._actor_with(["assets.view_asset"], "Reader-only")
        before = self._counts()

        with self.assertRaises(ActorNotAuthorized):
            execute_membership_write(
                actor=self.outsider,
                intent=self.intent(user=self.member, own_roles=(OwnGrantSpec(role=self.read_role),)),
            )

        self.assert_wrote_nothing(before)

    def test_update_without_change_membership_is_refused_and_writes_nothing(self):
        """B-2."""
        self._actor_with(["organization.add_membership"], "AdderOnly")
        membership = Membership.objects.create(user=self.member, tenant=self.provider)
        before = self._counts()

        with self.assertRaises(ActorNotAuthorized):
            execute_membership_write(
                actor=self.outsider,
                intent=self.intent(user=self.member),
                membership=membership,
            )

        self.assert_wrote_nothing(before)

    def test_the_create_permission_does_not_satisfy_an_update(self):
        """B-3."""
        self._actor_with(["organization.add_membership"], "AdderOnly")

        self.assertTrue(may_manage_memberships(actor=self.outsider, tenant=self.provider, creating=True))
        self.assertFalse(may_manage_memberships(actor=self.outsider, tenant=self.provider, creating=False))

    def test_a_trusted_null_actor_and_a_superuser_always_pass(self):
        """INV-2 — seeds, management commands, and SSO provisioning are unaffected."""
        for actor in (None, self.actor):
            with self.subTest(actor=actor):
                authorize_membership_write(actor=actor, tenant=self.provider, creating=True)
                authorize_membership_write(actor=actor, tenant=self.provider, creating=False)
                self.assertTrue(may_manage_memberships(actor=actor, tenant=self.provider, creating=True))

    def test_an_unauthorized_actor_never_learns_whether_the_account_is_a_member(self):
        """INV-12 / A3 — the authorization gate short-circuits BEFORE identity
        resolution and the duplicate check, so neither can leak target state."""
        Membership.objects.create(user=self.member, tenant=self.provider)
        User.objects.create_user(username="dup-a", email="dup@example.com")
        User.objects.create_user(username="dup-b", email="dup@example.com")

        for spec in (
            self.intent(new_identity=NewIdentitySpec(email="member@example.com")),
            self.intent(new_identity=NewIdentitySpec(email="dup@example.com")),
        ):
            with self.subTest(email=spec.new_identity.email):
                with self.assertRaises(ActorNotAuthorized) as ctx:
                    plan_membership_write(actor=self.outsider, intent=spec)

                message = " ".join(ctx.exception.messages)
                self.assertNotIn("already a member", message)
                self.assertNotIn("More than one account", message)
                self.assertNotIn(self.provider.name, message)

    def test_planning_writes_nothing_and_is_safe_from_a_form_clean(self):
        before = self._counts()

        plan = plan_membership_write(
            actor=self.actor,
            intent=self.intent(user=self.member, own_roles=(OwnGrantSpec(role=self.read_role),)),
        )

        self.assertTrue(plan.will_create_identity is False)
        self.assertIsNone(plan.membership)
        self.assertEqual(self._counts(), before)


class RollbackTests(_MembershipServiceTestBase):
    """INV-15 / R-1, R-2 — all-or-nothing across identity, row, and grants."""

    def test_a_failure_in_grant_sync_rolls_back_the_inline_created_user(self):
        """R-2."""
        before = self._counts()
        boom = RuntimeError("grant sync exploded")

        with (
            mock.patch(
                "organization.services.membership.sync_membership_grants",
                side_effect=boom,
            ),
            self.assertRaises(RuntimeError),
        ):
            execute_membership_write(
                actor=self.actor,
                intent=self.intent(
                    new_identity=NewIdentitySpec(email="rollback@example.com", first_name="R", last_name="B"),
                    own_roles=(OwnGrantSpec(role=self.read_role),),
                ),
            )

        self.assertFalse(User.objects.filter(email="rollback@example.com").exists())
        self.assert_wrote_nothing(before)

    def test_a_scope_violation_after_own_rows_were_written_unwinds_everything(self):
        """R-1 — the managed pass raises from ``RoleGrantScope.clean`` after the
        own pass already wrote rows."""
        member = User.objects.create_user(username="member", email="member@example.com")
        membership = Membership.objects.create(user=member, tenant=self.provider)
        before = self._counts()

        # A managed spec whose scope child violates RoleGrantScope.clean:
        # the target tenant is not managed by the role owner. The plan cannot
        # see it because the tenant is swapped in after validation.
        stray = Tenant.objects.create(name="Stray", slug="stray")
        good_spec = ManagedGrantSpec(role=self.read_role, tenants=(self.customer,), row_index=0)
        real_keys = rolegrants_service._desired_scope_keys

        def poisoned(spec):
            if spec is good_spec:
                return {(RoleGrantScope.SCOPE_TENANT, stray.pk, None)}
            return real_keys(spec)

        with (
            mock.patch.object(rolegrants_service, "_desired_scope_keys", side_effect=poisoned),
            self.assertRaises(Exception),
        ):
            execute_membership_write(
                actor=self.actor,
                intent=self.intent(
                    user=member,
                    own_roles=(OwnGrantSpec(role=self.read_role),),
                    managed_grants=(good_spec,),
                ),
                membership=membership,
            )

        self.assert_wrote_nothing(before)

    def test_a_rolled_back_write_leaves_effective_authorization_unchanged(self):
        """R-3 — the authorization-cache generation may still be bumped (that was
        always true); what must hold is that recomputed authority is unchanged."""
        member = User.objects.create_user(username="member", email="member@example.com")
        membership = Membership.objects.create(user=member, tenant=self.provider)
        before_perms = set(effective_permissions(member, self.provider))

        with (
            mock.patch(
                "organization.services.membership.sync_membership_grants",
                side_effect=RuntimeError("boom"),
            ),
            self.assertRaises(RuntimeError),
        ):
            execute_membership_write(
                actor=self.actor,
                intent=self.intent(user=member, own_roles=(OwnGrantSpec(role=self.read_role),)),
                membership=membership,
            )

        member.refresh_from_db()
        self.assertEqual(set(effective_permissions(member, self.provider)), before_perms)


class DeferredGrantEntryPointTests(_MembershipServiceTestBase):
    """``apply_membership_grants`` is a FULL write path, not a shortcut: it locks,
    authorizes, validates, and only then writes."""

    def setUp(self):
        super().setUp()
        self.member = User.objects.create_user(username="member", email="member@example.com")
        self.membership = Membership.objects.create(user=self.member, tenant=self.provider)

    def test_it_writes_the_same_rows_a_full_execute_would(self):
        result = apply_membership_grants(
            actor=self.actor,
            membership=self.membership,
            plan=GrantPlan(own=(OwnGrantSpec(role=self.read_role),)),
            previous_is_active=True,
        )

        self.assertEqual([c.action for c in result.changes], ["created"])
        self.assertEqual(self.membership.role_grants.count(), 1)

    def test_it_gates_on_change_membership_for_an_unauthorized_actor(self):
        outsider = User.objects.create_user(username="outsider")
        before = self._counts()

        with self.assertRaises(ActorNotAuthorized):
            apply_membership_grants(
                actor=outsider,
                membership=self.membership,
                plan=GrantPlan(own=(OwnGrantSpec(role=self.read_role),)),
                previous_is_active=True,
            )

        self.assert_wrote_nothing(before)

    def test_an_unknown_previous_state_fails_closed_and_re_validates_groups(self):
        """INV-14 — omission never disables the inheritance guard."""
        narrow_actor = User.objects.create_user(username="narrow")
        actor_role = Role.objects.create(
            tenant=self.provider,
            name="NarrowAdmin",
            permissions=[
                "assets.view_asset",
                "assets.delete_asset",
                "organization.add_rolegrant",
                "organization.change_membership",
            ],
        )
        grant(narrow_actor, self.provider, actor_role, reach="own")
        grant(narrow_actor, self.provider, actor_role, reach="managed", assigned_tenants=[self.customer])
        group = UserGroup.objects.create(tenant=self.provider, name="Field Techs")
        GroupMembership.objects.create(membership=self.membership, user_group=group)
        projected = Role.objects.create(
            tenant=self.provider,
            name="Projected",
            permissions=["assets.delete_asset"],
        )
        group_grant = RoleGrant.objects.create(user_group=group, role=projected)
        RoleGrantScope.objects.create(role_grant=group_grant, scope_type=RoleGrantScope.SCOPE_ALL_MANAGED)
        before = self._counts()

        with self.assertRaises(EscalationDenied):
            apply_membership_grants(
                actor=narrow_actor,
                membership=self.membership,
                plan=GrantPlan(),
            )

        self.assert_wrote_nothing(before)

        # Positively asserting the previous state skips the guard, as documented.
        apply_membership_grants(
            actor=narrow_actor,
            membership=self.membership,
            plan=GrantPlan(),
            previous_is_active=True,
        )


class ReactivationDerivationTests(_MembershipServiceTestBase):
    """INV-14 / A12 / E-8 — the transition is derived from the LOCKED row, so no
    caller can opt out by omitting a flag."""

    def setUp(self):
        super().setUp()
        self.narrow_actor = User.objects.create_user(username="narrow")
        actor_role = Role.objects.create(
            tenant=self.provider,
            name="NarrowAdmin",
            permissions=[
                "assets.view_asset",
                "assets.delete_asset",
                "organization.add_rolegrant",
                "organization.change_membership",
            ],
        )
        grant(self.narrow_actor, self.provider, actor_role, reach="own")
        grant(self.narrow_actor, self.provider, actor_role, reach="managed", assigned_tenants=[self.customer])
        self.member = User.objects.create_user(username="member", email="member@example.com")
        self.membership = Membership.objects.create(user=self.member, tenant=self.provider, is_active=False)
        self.group = UserGroup.objects.create(tenant=self.provider, name="Field Techs")
        GroupMembership.objects.create(membership=self.membership, user_group=self.group)
        projected = Role.objects.create(
            tenant=self.provider,
            name="Projected",
            permissions=["assets.delete_asset"],
        )
        group_grant = RoleGrant.objects.create(user_group=self.group, role=projected)
        RoleGrantScope.objects.create(role_grant=group_grant, scope_type=RoleGrantScope.SCOPE_ALL_MANAGED)

    def test_reactivation_beyond_the_actor_reach_is_refused_and_the_row_stays_off(self):
        """E-8."""
        before = self._counts()

        with self.assertRaises(EscalationDenied):
            execute_membership_write(
                actor=self.narrow_actor,
                intent=self.intent(user=self.member, is_active=True),
                membership=self.membership,
            )

        self.membership.refresh_from_db()
        self.assertFalse(self.membership.is_active)
        self.assert_wrote_nothing(before)

    def test_an_inactive_group_projects_nothing_so_reactivation_is_allowed(self):
        """E-9."""
        self.group.is_active = False
        self.group.save(update_fields=["is_active"])

        result = execute_membership_write(
            actor=self.narrow_actor,
            intent=self.intent(user=self.member, is_active=True),
            membership=self.membership,
        )

        self.assertTrue(result.membership.is_active)

    def test_leaving_the_membership_inactive_does_not_trigger_the_guard(self):
        result = execute_membership_write(
            actor=self.narrow_actor,
            intent=self.intent(user=self.member, is_active=False),
            membership=self.membership,
        )

        self.assertFalse(result.membership.is_active)


class AuditAttributionTests(_MembershipServiceTestBase):
    """§11 — the service does not set the change-log request context; a non-HTTP
    caller must, and when it does the rows are attributed to its principal."""

    def test_a_service_write_inside_task_context_produces_attributed_changes(self):
        member = User.objects.create_user(username="member", email="member@example.com")

        with TaskContext(tenant_id=self.provider.pk, user_id=self.actor.pk):
            execute_membership_write(
                actor=self.actor,
                intent=self.intent(user=member, own_roles=(OwnGrantSpec(role=self.read_role),)),
            )

        changed_models = set(ObjectChange.objects.values_list("changed_object_type__model", flat=True))
        self.assertEqual(changed_models, {"membership", "rolegrant", "rolegrantscope"})
        self.assertEqual(set(ObjectChange.objects.values_list("user_id", flat=True)), {self.actor.pk})


class TransactionalBoundaryTests(_MembershipServiceTestBase):
    """§7.1 — ``execute_membership_write`` opens the only transaction that
    matters, and ``plan_membership_write`` never opens one."""

    def test_execute_runs_the_whole_write_in_one_atomic_block(self):
        member = User.objects.create_user(username="member", email="member@example.com")
        observed = {}

        real = rolegrants_service.sync_membership_grants

        def spy(**kwargs):
            observed["in_atomic"] = transaction.get_connection().in_atomic_block
            return real(**kwargs)

        with mock.patch("organization.services.membership.sync_membership_grants", side_effect=spy):
            execute_membership_write(actor=self.actor, intent=self.intent(user=member))

        self.assertTrue(observed["in_atomic"])

    def test_an_update_that_only_toggles_is_active_writes_the_row(self):
        member = User.objects.create_user(username="member", email="member@example.com")
        membership = Membership.objects.create(user=member, tenant=self.provider, is_active=True)

        execute_membership_write(
            actor=self.actor,
            intent=self.intent(user=member, is_active=False),
            membership=membership,
        )

        membership.refresh_from_db()
        self.assertFalse(membership.is_active)

    def test_planning_an_update_for_a_different_user_is_a_programmer_error(self):
        member = User.objects.create_user(username="member", email="member@example.com")
        stranger = User.objects.create_user(username="stranger", email="stranger@example.com")
        membership = Membership.objects.create(user=member, tenant=self.provider)

        with self.assertRaises(ValueError):
            execute_membership_write(
                actor=self.actor,
                intent=self.intent(user=stranger),
                membership=membership,
            )


class ElevatedMetadataThroughTheServiceTests(_MembershipServiceTestBase):
    """The service is the only writer, so INV-5's own/managed asymmetry has to
    hold end to end, not just inside ``validate_grant_plan``."""

    def test_a_privileged_own_role_needs_metadata_and_then_stores_it(self):
        member = User.objects.create_user(username="member", email="member@example.com")
        admin_role = Role.objects.create(
            tenant=self.provider,
            name="Admin",
            permissions=["assets.view_asset", "assets.change_asset"],
        )
        before = self._counts()

        with self.assertRaises(MembershipServiceError):
            execute_membership_write(
                actor=self.actor,
                intent=self.intent(user=member, own_roles=(OwnGrantSpec(role=admin_role),)),
            )
        self.assert_wrote_nothing(before)

        expiry = timezone.now() + timedelta(days=3)
        result = execute_membership_write(
            actor=self.actor,
            intent=self.intent(
                user=member,
                own_roles=(OwnGrantSpec(role=admin_role, reason="on call", valid_until=expiry),),
            ),
        )

        stored = result.membership.role_grants.get()
        self.assertEqual(stored.reason, "on call")
        self.assertEqual(stored.valid_until, expiry)

    def test_the_membership_insert_failure_path_leaves_no_orphan_identity(self):
        """The savepoint around the insert must not swallow the rollback of a
        user created moments earlier in the same call."""
        User.objects.create_user(username="taken", email="taken@example.com")
        Membership.objects.create(
            user=User.objects.get(username="taken"),
            tenant=self.provider,
        )
        before = self._counts()

        with self.assertRaises(DuplicateMembership):
            execute_membership_write(
                actor=self.actor,
                intent=self.intent(new_identity=NewIdentitySpec(email="taken@example.com")),
            )

        self.assert_wrote_nothing(before)

    def test_integrity_errors_that_are_not_duplicates_are_not_masked(self):
        member = User.objects.create_user(username="member", email="member@example.com")

        with (
            mock.patch.object(
                Membership,
                "save",
                side_effect=IntegrityError("some other constraint"),
            ),
            self.assertRaises(IntegrityError),
        ):
            execute_membership_write(actor=self.actor, intent=self.intent(user=member))
