"""Input/error contracts of the membership + RBAC services (issue #86).

These are the type-level guarantees the rest of the service layer is built on:
an error that a form can put back on the exact field/row it came from, frozen
input objects that cannot be mutated between validation and the write, and the
assignability rule (INV-3) as a standalone predicate that takes model instances
rather than a widget queryset.
"""

import dataclasses

from django.core.exceptions import ValidationError
from django.test import TestCase

from organization.models import Role, RoleGrantScope, Tenant
from organization.services.errors import (
    ActorNotAuthorized,
    AmbiguousIdentity,
    ConcurrentGrantChange,
    CrossTenantObject,
    DuplicateMembership,
    ElevatedGrantIncomplete,
    EscalationDenied,
    MembershipServiceError,
    ServiceError,
)
from organization.services.membership import MembershipIntent, NewIdentitySpec
from organization.services.rolegrants import (
    SCOPE_EXPLICIT,
    GrantChange,
    GrantPlan,
    GrantSyncResult,
    ManagedGrantSpec,
    OwnGrantSpec,
    ValidatedGrantPlan,
    assignable_roles_qs,
    managed_target_tenants_qs,
    role_assignable_in,
)


class ServiceErrorContractTests(TestCase):
    """``MembershipServiceError`` must stay a ``ValidationError`` *and* carry
    enough location for a form to render each message on its own field/row."""

    def test_service_error_is_a_validation_error_with_round_tripping_messages(self):
        exc = MembershipServiceError(
            [
                ServiceError("first problem", "code_a"),
                ServiceError("second problem", "code_b", field="reason", row_index=2),
            ]
        )

        self.assertIsInstance(exc, ValidationError)
        self.assertEqual(exc.messages, ["first problem", "second problem"])

    def test_single_helper_defaults_the_code_to_the_subclass_default(self):
        exc = EscalationDenied.single("nope")

        self.assertEqual(exc.errors[0].code, "escalation_denied")
        self.assertEqual(exc.messages, ["nope"])
        self.assertIsNone(exc.errors[0].field)
        self.assertIsNone(exc.errors[0].row_index)

    def test_single_helper_carries_field_and_row_index(self):
        exc = CrossTenantObject.single(
            "not managed by this provider",
            field="assigned_tenants",
            row_index=1,
        )

        (err,) = exc.errors
        self.assertEqual(err.field, "assigned_tenants")
        self.assertEqual(err.row_index, 1)

    def test_errors_tuple_is_immutable_and_service_error_is_frozen(self):
        exc = MembershipServiceError([ServiceError("x", "c")])

        self.assertIsInstance(exc.errors, tuple)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            exc.errors[0].message = "tampered"

    def test_every_typed_rejection_is_catchable_as_the_service_base(self):
        for cls in (
            ActorNotAuthorized,
            CrossTenantObject,
            EscalationDenied,
            ElevatedGrantIncomplete,
            AmbiguousIdentity,
            DuplicateMembership,
            ConcurrentGrantChange,
        ):
            with self.subTest(cls=cls.__name__):
                exc = cls.single("boom")
                self.assertIsInstance(exc, MembershipServiceError)
                self.assertIsInstance(exc, ValidationError)
                self.assertNotEqual(cls.default_code, MembershipServiceError.default_code)


class GrantInputContractTests(TestCase):
    """The plan/intent objects are frozen: nothing may be edited between the
    read-only decision and the write it authorises (INV-1)."""

    FROZEN = (
        (OwnGrantSpec, {"role": None}),
        (ManagedGrantSpec, {"role": None}),
        (GrantPlan, {}),
        (GrantChange, {"action": "created", "reach": "own", "role_id": 1, "grant_id": None}),
        (GrantSyncResult, {}),
        (NewIdentitySpec, {"email": "a@example.com"}),
        (MembershipIntent, {"tenant": None}),
    )

    def test_input_and_result_objects_are_frozen(self):
        for cls, kwargs in self.FROZEN:
            with self.subTest(cls=cls.__name__):
                obj = cls(**kwargs)
                field = next(iter(dataclasses.fields(obj))).name
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    setattr(obj, field, "tampered")

    def test_grant_plan_defaults_to_empty_reach_on_both_halves(self):
        plan = GrantPlan()

        self.assertEqual(plan.own, ())
        self.assertEqual(plan.managed, ())

    def test_grant_plan_has_no_caller_supplied_reactivation_switch(self):
        """INV-14: the ``False -> True`` transition is DERIVED from stored state,
        never submitted, so no caller can opt out of the inheritance re-check."""
        names = {f.name for f in dataclasses.fields(GrantPlan)}

        self.assertNotIn("revalidate_inherited_groups", names)

    def test_managed_spec_defaults_to_the_explicit_scope_with_no_targets(self):
        spec = ManagedGrantSpec(role=None)

        self.assertEqual(spec.scope, SCOPE_EXPLICIT)
        self.assertEqual(spec.tenants, ())
        self.assertIsNone(spec.grant_id)
        self.assertIsNone(spec.row_index)

    def test_validated_plan_is_frozen_and_replaceable_for_the_create_rebind(self):
        """``_apply`` rebinds ``membership_id`` the moment the row is inserted;
        ``dataclasses.replace`` is the only sanctioned way to do it."""
        validated = ValidatedGrantPlan(
            principal_tenant=None,
            membership_id=None,
            plan=GrantPlan(),
            actor=None,
            revalidate_inherited_groups=False,
            existing_own_role_ids=frozenset(),
            existing_managed_grant_ids=frozenset(),
        )

        with self.assertRaises(dataclasses.FrozenInstanceError):
            validated.membership_id = 7
        self.assertEqual(dataclasses.replace(validated, membership_id=7).membership_id, 7)


class GrantSyncResultTests(TestCase):
    def test_wrote_anything_is_false_for_an_all_unchanged_result(self):
        result = GrantSyncResult(
            changes=(
                GrantChange(action="unchanged", reach="own", role_id=1, grant_id=5),
                GrantChange(action="unchanged", reach="managed", role_id=2, grant_id=6),
            )
        )

        self.assertFalse(result.wrote_anything)
        self.assertEqual(len(result.of("unchanged")), 2)
        self.assertEqual(result.of("created"), ())

    def test_wrote_anything_is_true_when_any_row_moved(self):
        result = GrantSyncResult(
            changes=(
                GrantChange(action="unchanged", reach="own", role_id=1, grant_id=5),
                GrantChange(action="revoked", reach="managed", role_id=2, grant_id=6),
            )
        )

        self.assertTrue(result.wrote_anything)
        self.assertEqual(result.of("revoked")[0].role_id, 2)


class RoleAssignabilityTests(TestCase):
    """INV-3, as a predicate over model instances — no queryset, no widget."""

    def setUp(self):
        self.provider = Tenant.objects.create(name="Provider", slug="p", is_provider=True)
        self.customer = Tenant.objects.create(name="Customer", slug="c", managed_by=self.provider)
        self.unrelated = Tenant.objects.create(name="Unrelated", slug="u")
        self.own_role = Role.objects.create(tenant=self.customer, name="Own", permissions=["assets.view_asset"])
        self.shared_role = Role.objects.create(
            tenant=self.provider,
            name="Shared",
            permissions=["assets.view_asset"],
            shared_with_managed=True,
        )
        self.unshared_role = Role.objects.create(
            tenant=self.provider,
            name="Unshared",
            permissions=["assets.view_asset"],
            shared_with_managed=False,
        )

    def test_role_owned_by_the_tenant_is_assignable(self):
        self.assertTrue(role_assignable_in(self.own_role, self.customer))

    def test_role_shared_down_by_the_managing_provider_is_assignable(self):
        self.assertTrue(role_assignable_in(self.shared_role, self.customer))

    def test_unshared_provider_role_is_not_assignable_in_the_customer(self):
        self.assertFalse(role_assignable_in(self.unshared_role, self.customer))

    def test_shared_role_from_a_non_provider_owner_is_not_assignable(self):
        self.unrelated.is_provider = False
        self.unrelated.save(update_fields=["is_provider"])
        stray = Role.objects.create(
            tenant=self.unrelated,
            name="Stray",
            permissions=["assets.view_asset"],
            shared_with_managed=True,
        )

        self.assertFalse(role_assignable_in(stray, self.customer))

    def test_shared_role_is_not_assignable_in_a_tenant_it_does_not_manage(self):
        other = Tenant.objects.create(name="Other", slug="o")

        self.assertFalse(role_assignable_in(self.shared_role, other))

    def test_assignable_roles_qs_returns_own_plus_shared_down_only(self):
        names = set(assignable_roles_qs(self.customer).values_list("name", flat=True))

        self.assertEqual(names, {"Own", "Shared"})

    def test_assignable_roles_qs_falls_back_to_every_live_role_without_a_tenant(self):
        deleted = Role.objects.create(tenant=self.provider, name="Gone", permissions=[])
        deleted.delete()

        names = set(assignable_roles_qs(None).values_list("name", flat=True))

        self.assertEqual(names, {"Own", "Shared", "Unshared"})

    def test_managed_target_tenants_qs_is_the_provider_reach(self):
        self.assertEqual(
            set(managed_target_tenants_qs(self.provider).values_list("slug", flat=True)),
            {"c"},
        )
        self.assertEqual(
            set(managed_target_tenants_qs(None).values_list("slug", flat=True)),
            {"p", "c", "u"},
        )


class ScopeWireValueTests(TestCase):
    """``SCOPE_EXPLICIT`` is the UI's wire value for "specific tenants" and maps
    onto ``RoleGrantScope.SCOPE_TENANT`` children — it is deliberately NOT one of
    the model's scope-type constants."""

    def test_explicit_is_the_ui_wire_value_not_a_model_scope_type(self):
        self.assertEqual(SCOPE_EXPLICIT, "explicit")
        self.assertNotIn(SCOPE_EXPLICIT, dict(RoleGrantScope.SCOPE_CHOICES))
