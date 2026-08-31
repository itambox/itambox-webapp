"""``MembershipForm`` delegates every domain decision to the services (issue #86).

The form keeps presentation — widgets, layout, required-ness messages, the
who-radio contract, the ``commit=False`` form-API contract — and nothing else.
These tests pin that split from the outside: the form no longer coordinates
grants, and the service-boundary authorization gate (matrix A1/A2) is
load-bearing for a directly-built form, not only for the view that normally
wraps it.
"""

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core.tests.mixins import grant
from organization.forms import membership_form
from organization.forms.membership_form import ManagedRoleGrantForm, MembershipForm
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.services.errors import ActorNotAuthorized, MembershipServiceError, ServiceError
from organization.services.rolegrants import live_managed_grants, live_own_grants
from organization.tests._membership_form_helpers import membership_post_data

User = get_user_model()


class _FormDelegationTestBase(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(username="root")
        self.member = User.objects.create_user(username="member", email="member@example.com")
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
        )

    def _actor_with(self, permissions, name):
        actor = User.objects.create_user(username=f"actor-{name.lower()}")
        role = Role.objects.create(tenant=self.provider, name=name, permissions=permissions)
        grant(actor, self.provider, role, reach="own")
        return actor

    def _create_form(self, actor, **overrides):
        data = membership_post_data(
            tenant=self.provider.pk,
            user=self.member.pk,
            who=MembershipForm.WHO_EXISTING,
            own_roles=[self.read_role.pk],
            managed=[],
        )
        data.update(overrides)
        return MembershipForm(data=data, user=actor, tenant=self.provider)

    def _edit_form(self, membership, actor, **kwargs):
        data = membership_post_data(
            tenant=self.provider.pk,
            user=membership.user_id,
            own_roles=kwargs.pop("own_roles", [self.read_role.pk]),
            managed=kwargs.pop("managed", []),
            **kwargs,
        )
        return MembershipForm(data=data, instance=membership, user=actor, tenant=self.provider)


class FormNoLongerCoordinatesGrantsTests(_FormDelegationTestBase):
    """D-1 / AC 3 — the reconciliation and the authorization predicate are gone
    from the form; if they came back, the service would no longer be THE path."""

    RELOCATED = (
        "_sync_grants",
        "_sync_own_roles",
        "_intended_managed_rows",
        "_sync_managed_formset",
        "_create_inline_user",
        "_actor_may_manage_memberships",
    )

    def test_the_relocated_methods_are_gone(self):
        for name in self.RELOCATED:
            with self.subTest(name=name):
                self.assertFalse(
                    hasattr(MembershipForm, name),
                    f"MembershipForm.{name} moved into organization.services and must not be reintroduced",
                )

    def test_the_form_builds_service_input_objects_instead(self):
        for name in ("_build_intent", "_grant_plan", "_own_specs", "_managed_specs"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(MembershipForm, name))

    def test_a_managed_row_carries_no_actor_state(self):
        """The escalation call and the cross-tenant checks that read the actor
        moved into the plan. A row that still held on to it would invite a
        second, form-local authorization beside the service's."""
        row = ManagedRoleGrantForm(membership_tenant=self.provider)

        self.assertFalse(hasattr(row, "_requesting_user"))
        self.assertFalse(hasattr(row, "_membership_tenant"))

    def test_the_managed_formset_is_not_handed_the_actor(self):
        form = MembershipForm(user=self.superuser, tenant=self.provider)

        self.assertEqual(set(form.managed_formset.form_kwargs), {"membership_tenant"})
        with self.assertRaises(TypeError):
            ManagedRoleGrantForm(membership_tenant=self.provider, requesting_user=self.superuser)


class SeedingUsesTheServiceLiveReadsTests(_FormDelegationTestBase):
    """INV-10 has one definition, and the form reads it rather than restating it.

    ``rolegrants.live_own_grants`` / ``live_managed_grants`` are the reads the
    write phase and the tamper check use. Seeding the editor from a second,
    hand-rolled copy of the same "not expired, role not soft-deleted" window
    means two definitions of one rule, and the copy that drifts is the one the
    admin sees.
    """

    def setUp(self):
        super().setUp()
        self.membership = Membership.objects.create(user=self.member, tenant=self.provider)
        self.live_own = grant(self.member, self.provider, self.read_role)
        expired_role = Role.objects.create(
            tenant=self.provider,
            name="Expired reader",
            permissions=["assets.view_asset"],
        )
        expired = RoleGrant.objects.create(
            membership=self.membership,
            role=expired_role,
            valid_until=timezone.now() - timedelta(minutes=1),
        )
        RoleGrantScope.objects.create(role_grant=expired, scope_type=RoleGrantScope.SCOPE_OWN)
        self.live_managed = grant(
            self.member,
            self.provider,
            Role.objects.create(
                tenant=self.provider,
                name="Managed reader",
                permissions=["assets.view_asset"],
            ),
            reach="managed",
            assigned_tenants=[self.customer],
        )

    def test_the_form_module_uses_the_published_live_reads(self):
        self.assertIs(membership_form.live_own_grants, live_own_grants)
        self.assertIs(membership_form.live_managed_grants, live_managed_grants)

    def test_the_editor_is_seeded_through_those_reads(self):
        with (
            mock.patch.object(membership_form, "live_own_grants", wraps=live_own_grants) as own_read,
            mock.patch.object(membership_form, "live_managed_grants", wraps=live_managed_grants) as managed_read,
        ):
            form = MembershipForm(instance=self.membership, user=self.superuser)

        own_read.assert_called_once_with(self.membership)
        managed_read.assert_called_once_with(self.membership)
        self.assertEqual(list(form.fields["own_roles"].initial), [self.live_own.role_id])
        self.assertEqual([row["id"] for row in form.managed_formset.initial], [self.live_managed.pk])


class SaveTimeAuthorizationTests(_FormDelegationTestBase):
    """A1/A2 became load-bearing when ``save()`` started delegating.

    Before the extraction a directly-constructed form with an unauthorized actor
    and a valid payload WOULD write: the view's ``_authorized_tenant()`` was the
    only gate, and the form consulted the permission solely for its oracle
    defence. It cannot now.
    """

    def test_a_directly_built_create_form_cannot_write_without_add_membership(self):
        """B-1, through the form's own save path.

        Validate as an authorized actor and swap the narrower one in before
        saving (the pattern
        ``test_deferred_save_is_gated_for_an_actor_without_change_membership``
        uses): what stops the write must be the service gate, not the form's own
        error list, and a form that never validated would be refused by
        ``BaseModelForm.save``'s guard before the gate was ever consulted.
        """
        actor = self._actor_with(["assets.view_asset"], "ReaderOnly")
        form = self._create_form(self.superuser)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form._requesting_user = actor
        before = self._counts()

        with self.assertRaises(ActorNotAuthorized):
            form.save()

        self.assertEqual(self._counts(), before)

    def test_a_directly_built_edit_form_cannot_write_without_change_membership(self):
        """B-2, through the form's own save path."""
        actor = self._actor_with(["organization.add_membership"], "AdderOnly")
        membership = Membership.objects.create(user=self.member, tenant=self.provider)
        form = self._edit_form(membership, self.superuser)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form._requesting_user = actor
        before = self._counts()

        with self.assertRaises(ActorNotAuthorized):
            form.save()

        self.assertEqual(self._counts(), before)

    def test_an_authorized_actor_still_writes_exactly_what_it_always_did(self):
        actor = self._actor_with(
            ["assets.view_asset", "organization.add_membership", "organization.change_membership"],
            "Manager",
        )
        form = self._create_form(actor)

        self.assertTrue(form.is_valid(), form.errors.as_json())
        membership = form.save()

        self.assertEqual(membership.user_id, self.member.pk)
        self.assertEqual(
            sorted(scope.scope_type for g in membership.role_grants.all() for scope in g.scopes.all()),
            ["own"],
        )

    def test_the_superuser_path_is_unchanged(self):
        form = self._create_form(self.superuser)

        self.assertTrue(form.is_valid(), form.errors.as_json())
        membership = form.save()

        self.assertEqual(membership.role_grants.count(), 1)


class ServiceBoundaryAuthorizationTests(_FormDelegationTestBase):
    """A1-A3 at the boundary, not at the widget.

    The create view already 404s an unauthorized deep link; these cases cover
    what it cannot — a directly-built form, a tampered POST, and any future
    caller that constructs the same payload without a view.
    """

    def setUp(self):
        super().setUp()
        self.unauthorized = self._actor_with(["assets.view_asset"], "ReaderOnly")

    def _who_new_form(self, email, actor, instance=None):
        data = membership_post_data(
            tenant=self.provider.pk,
            who=MembershipForm.WHO_NEW,
            new_user_email=email,
            new_user_first_name="A",
            new_user_last_name="B",
            own_roles=[],
            managed=[],
        )
        return MembershipForm(data=data, instance=instance, user=actor, tenant=self.provider)

    def test_an_unauthorized_actor_with_a_fresh_email_cannot_validate(self):
        """A1 — a fresh email is the case a widget queryset could never catch."""
        form = self._who_new_form("brand-new@example.com", self.unauthorized)
        before = self._counts()

        self.assertFalse(form.is_valid())

        self.assertEqual(self._counts(), before)

    def test_an_unauthorized_actor_with_a_fresh_email_cannot_write(self):
        """The other half: even a payload that validated for somebody else is
        refused at the service, so the form's error list is not what gates it."""
        form = self._who_new_form("brand-new@example.com", self.superuser)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form._requesting_user = self.unauthorized
        before = self._counts()

        with self.assertRaises(ActorNotAuthorized):
            form.save()

        self.assertEqual(self._counts(), before)
        self.assertFalse(User.objects.filter(email="brand-new@example.com").exists())

    def test_the_same_actor_is_rejected_by_the_service_called_directly(self):
        """The form and the service agree, because the form has no say."""
        from organization.services.membership import MembershipIntent, execute_membership_write

        before = self._counts()

        with self.assertRaises(ActorNotAuthorized):
            execute_membership_write(
                actor=self.unauthorized,
                intent=MembershipIntent(tenant=self.provider, user=self.member),
            )

        self.assertEqual(self._counts(), before)

    def test_no_membership_or_identity_state_leaks_to_an_unauthorized_actor(self):
        """INV-12 / B-4 — the non-revealing message, and nothing more."""
        Membership.objects.create(user=self.member, tenant=self.provider)
        User.objects.create_user(username="dup-a", email="dup@example.com")
        User.objects.create_user(username="dup-b", email="dup@example.com")

        for email in ("member@example.com", "dup@example.com"):
            with self.subTest(email=email):
                form = self._who_new_form(email, self.unauthorized)
                self.assertFalse(form.is_valid())
                rendered = form.errors.as_text() + form.errors.as_json()

                self.assertNotIn("already a member", rendered)
                self.assertNotIn("More than one account", rendered)
                self.assertIn("cannot be added to the selected tenant", rendered)

    def test_an_authorized_actor_still_gets_the_precise_duplicate_message(self):
        """A3 — the oracle defence hides the detail from everyone else, not from
        the admin who is entitled to it."""
        authorized = self._actor_with(
            ["assets.view_asset", "organization.add_membership", "organization.change_membership"],
            "Manager",
        )
        Membership.objects.create(user=self.member, tenant=self.provider)

        form = self._who_new_form("member@example.com", authorized)

        self.assertFalse(form.is_valid())
        self.assertIn("already a member", form.errors.as_text())

    def test_the_oracle_message_stays_on_the_email_field(self):
        """The service only ADDS errors; ``_clean_who`` still owns this one."""
        Membership.objects.create(user=self.member, tenant=self.provider)

        form = self._who_new_form("member@example.com", self.unauthorized)

        self.assertFalse(form.is_valid())
        self.assertIn("new_user_email", form.errors)


class CommitFalseContractTests(_FormDelegationTestBase):
    """INV-13 / D-3, D-4 — the form-API contract stays exactly where it was."""

    def test_deferred_save_writes_the_same_rows_a_commit_true_save_would(self):
        """D-3 — equivalence is scoped to an actor that also holds
        ``change_membership``, because the deferred grant path correctly gates on
        A2 (it cannot know the caller's write created the row)."""
        reference = self._create_form(self.superuser)
        self.assertTrue(reference.is_valid(), reference.errors.as_json())
        reference_membership = reference.save()
        reference_rows = sorted(
            (g.role_id, scope.scope_type, scope.tenant_id, scope.tenant_group_id)
            for g in reference_membership.role_grants.all()
            for scope in g.scopes.all()
        )
        reference_membership.delete()

        form = self._create_form(self.superuser)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        instance = form.save(commit=False)
        self.assertEqual(RoleGrant.objects.count(), 0, "nothing may be written before the caller saves the row")
        instance.save()
        form.save_m2m()

        deferred_rows = sorted(
            (g.role_id, scope.scope_type, scope.tenant_id, scope.tenant_group_id)
            for g in instance.role_grants.all()
            for scope in g.scopes.all()
        )
        self.assertEqual(deferred_rows, reference_rows)

    def test_deferred_save_is_gated_for_an_actor_without_change_membership(self):
        """The documented asymmetry: A2 is what the deferred path can check."""
        actor = self._actor_with(["assets.view_asset", "organization.add_membership"], "AdderOnly")
        # Validate as a superuser, then hand the deferred write to the narrower
        # actor: the service gate is what stops the write, not the form errors.
        form = self._create_form(self.superuser)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form._requesting_user = actor
        instance = form.save(commit=False)
        instance.save()

        with self.assertRaises(ActorNotAuthorized):
            form.save_m2m()

        self.assertEqual(RoleGrant.objects.filter(membership=instance).count(), 0)

    def test_commit_false_while_creating_an_inline_user_raises_and_writes_nothing(self):
        """D-4."""
        data = membership_post_data(
            tenant=self.provider.pk,
            who=MembershipForm.WHO_NEW,
            new_user_email="brand-new@example.com",
            new_user_first_name="Brand",
            new_user_last_name="New",
            own_roles=[self.read_role.pk],
            managed=[],
        )
        form = MembershipForm(data=data, user=self.superuser, tenant=self.provider)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        before = self._counts()

        with self.assertRaises(ValueError):
            form.save(commit=False)

        self.assertFalse(User.objects.filter(email="brand-new@example.com").exists())
        self.assertEqual(self._counts(), before)


class InvalidFormSaveGuardTests(_FormDelegationTestBase):
    """``BaseModelForm.save``'s "the data didn't validate" guard survives the
    delegation — on BOTH branches.

    ``save(commit=True)`` deliberately does not call ``super().save()`` (the
    service owns the row, the identity and the transaction), which also skips
    the guard Django puts at the top of it. Without re-adding it the two
    branches disagree: ``commit=False`` still raises the informative
    ``ValueError`` while ``commit=True`` walks into ``cleaned_data["tenant"]``
    with a ``KeyError``, or hands the service a half-built intent.
    """

    def _invalid_form(self):
        """Main-form errors and no ``cleaned_data["tenant"]`` — the exact shape
        the ``commit=True`` branch used to trip over."""
        form = self._create_form(self.superuser, tenant="")
        self.assertFalse(form.is_valid())
        self.assertNotIn("tenant", form.cleaned_data)
        return form

    def test_saving_an_invalid_form_raises_the_model_form_value_error(self):
        before = self._counts()

        with self.assertRaises(ValueError) as ctx:
            self._invalid_form().save()

        self.assertIn("could not be created", str(ctx.exception))
        self.assertEqual(self._counts(), before)

    def test_both_commit_branches_refuse_an_invalid_form_identically(self):
        commit_true = self._invalid_form()
        commit_false = self._invalid_form()

        with self.assertRaises(ValueError) as with_commit:
            commit_true.save()
        with self.assertRaises(ValueError) as without_commit:
            commit_false.save(commit=False)

        self.assertEqual(str(with_commit.exception), str(without_commit.exception))

    def test_an_invalid_edit_names_the_change_it_refused(self):
        membership = Membership.objects.create(user=self.member, tenant=self.provider)
        form = self._edit_form(membership, self.superuser, own_roles=["not-a-role-id"])
        self.assertFalse(form.is_valid())
        before = self._counts()

        with self.assertRaises(ValueError) as ctx:
            form.save()

        self.assertIn("could not be changed", str(ctx.exception))
        self.assertEqual(self._counts(), before)


class ServiceErrorPlacementTests(_FormDelegationTestBase):
    """D-2 — a service rejection is rendered where it came from.

    ``ServiceError.row_index`` is an index into ``managed_formset.forms``, so a
    row rejection lands on that row rather than collapsing into the main form's
    non-field errors, and a main-form rejection stays exactly where the existing
    assertions expect it.
    """

    def _managed_form(self, managed, actor=None, **overrides):
        data = membership_post_data(
            tenant=self.provider.pk,
            user=self.member.pk,
            who=MembershipForm.WHO_EXISTING,
            own_roles=overrides.pop("own_roles", []),
            managed=managed,
            **overrides,
        )
        return MembershipForm(data=data, user=actor or self.superuser, tenant=self.provider)

    def test_a_duplicated_managed_role_is_reported_on_the_second_row(self):
        """X-8 — the rule moved off the formset, so it can name a row."""
        second = Tenant.objects.create(name="Customer Two", slug="customer-two", managed_by=self.provider)
        form = self._managed_form(
            [
                {
                    "role": self.read_role.pk,
                    "managed_scope": "explicit",
                    "assigned_tenants": [self.customer.pk],
                },
                {
                    "role": self.read_role.pk,
                    "managed_scope": "explicit",
                    "assigned_tenants": [second.pk],
                },
            ]
        )

        self.assertFalse(form.is_valid())
        rows = form.managed_formset.forms
        self.assertEqual(rows[0].errors, {})
        self.assertIn("appears more than once", " ".join(rows[1].non_field_errors()))
        self.assertEqual(form.managed_formset.non_form_errors(), [])

    def test_a_group_row_reaching_no_managed_tenant_is_reported_on_its_field(self):
        """X-7 — a plan-only check, surfaced during validation instead of at save."""
        from organization.models import TenantGroup

        empty_group = TenantGroup.objects.create(name="Elsewhere", slug="elsewhere")
        form = self._managed_form(
            [
                {
                    "role": self.read_role.pk,
                    "managed_scope": RoleGrantScope.SCOPE_TENANT_GROUP,
                    "scope_group": empty_group.pk,
                }
            ]
        )

        self.assertFalse(form.is_valid())
        self.assertIn("scope_group", form.managed_formset.forms[0].errors)
        self.assertEqual(RoleGrant.objects.count(), 0)

    def test_a_target_managed_by_another_provider_is_reported_on_its_field(self):
        """X-3 — the message keeps its precise placement after the move."""
        other_provider = Tenant.objects.create(name="Other MSP", slug="other-msp", is_provider=True)
        foreign = Tenant.objects.create(name="Foreign", slug="foreign", managed_by=other_provider)
        form = self._managed_form(
            [
                {
                    "role": self.read_role.pk,
                    "managed_scope": "explicit",
                    "assigned_tenants": [foreign.pk],
                }
            ]
        )

        self.assertFalse(form.is_valid())
        row_errors = form.managed_formset.forms[0].errors
        self.assertIn("assigned_tenants", row_errors)

    def test_a_row_that_already_failed_a_field_rule_gets_no_duplicate_message(self):
        """The intent skips already-errored rows, so the plan's shape check
        cannot pile a second message onto the same field."""
        form = self._managed_form([{"role": self.read_role.pk, "managed_scope": "explicit"}])

        self.assertFalse(form.is_valid())
        row_errors = form.managed_formset.forms[0].errors
        self.assertEqual(len(row_errors["assigned_tenants"]), 1)

    def test_own_role_escalation_stays_in_the_main_form_non_field_errors(self):
        """A4 keeps ``field=None, row_index=None`` so the existing assertions in
        ``test_membership_grant_editor`` continue to hold."""
        actor = self._actor_with(
            ["assets.view_asset", "organization.add_membership", "organization.change_membership"],
            "Manager",
        )
        admin_role = Role.objects.create(
            tenant=self.provider,
            name="Admin",
            permissions=["assets.view_asset", "assets.change_asset"],
        )
        form = self._managed_form(
            [],
            actor=actor,
            own_roles=[admin_role.pk],
            reason="because",
            valid_until=(timezone.now() + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M"),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("cannot grant permissions you do not hold", " ".join(form.non_field_errors()))

    def test_a_non_provider_tenant_renders_no_managed_block_and_ignores_posted_rows(self):
        """There is nowhere to render a row-level error: the Managed block only
        exists on a provider tenant, so posted rows are dropped rather than
        reported. The row-level rejection itself is pinned at the service, in
        ``test_membership_service_authz``."""
        customer_role = Role.objects.create(
            tenant=self.customer,
            name="Local",
            permissions=["assets.view_asset"],
        )
        data = membership_post_data(
            tenant=self.customer.pk,
            user=self.member.pk,
            who=MembershipForm.WHO_EXISTING,
            own_roles=[],
            managed=[
                {
                    "role": customer_role.pk,
                    "managed_scope": "explicit",
                    "assigned_tenants": [self.customer.pk],
                }
            ],
        )
        form = MembershipForm(data=data, user=self.superuser, tenant=self.customer)

        self.assertIsNone(form.managed_formset, "a non-provider tenant renders no managed block at all")
        self.assertTrue(form.is_valid(), form.errors.as_json())


class ServiceErrorDeduplicationTests(_FormDelegationTestBase):
    """A service message the target already carries is not rendered twice.

    ``_add_service_errors`` only ever ADDS. The form's own field-level rules run
    first — deliberately, because that is what keeps the non-revealing message
    on ``new_user_email`` — so a service rejection can name a target that
    already shows exactly that sentence, and the admin then reads it twice under
    one label. Dedupe is per TARGET: the same text on two different rows is two
    distinct locations and both must survive, matching the service's own
    ``(message, field, row_index)`` de-duplication.
    """

    def _validated_form(self):
        form = self._create_form(self.superuser)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        return form

    def _validated_form_with_a_managed_row(self):
        data = membership_post_data(
            tenant=self.provider.pk,
            user=self.member.pk,
            who=MembershipForm.WHO_EXISTING,
            own_roles=[],
            managed=[
                {
                    "role": self.read_role.pk,
                    "managed_scope": "explicit",
                    "assigned_tenants": [self.customer.pk],
                }
            ],
        )
        form = MembershipForm(data=data, user=self.superuser, tenant=self.provider)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        return form

    @staticmethod
    def _service_error(message, **location):
        return MembershipServiceError([ServiceError(message, "test_code", **location)])

    def test_a_message_already_on_the_same_field_is_added_once(self):
        form = self._validated_form()
        exc = self._service_error("Pick a different role.", field="own_roles")

        form._add_service_errors(exc)
        form._add_service_errors(exc)

        self.assertEqual(list(form.errors["own_roles"]), ["Pick a different role."])

    def test_a_message_already_in_the_non_field_errors_is_added_once(self):
        form = self._validated_form()
        exc = self._service_error("Another change landed first.")

        form._add_service_errors(exc)
        form._add_service_errors(exc)

        self.assertEqual(list(form.non_field_errors()), ["Another change landed first."])

    def test_a_message_already_on_the_same_row_is_added_once(self):
        form = self._validated_form_with_a_managed_row()
        exc = self._service_error("This coverage is not yours to grant.", row_index=0)

        form._add_service_errors(exc)
        form._add_service_errors(exc)

        self.assertEqual(
            list(form.managed_formset.forms[0].non_field_errors()),
            ["This coverage is not yours to grant."],
        )

    def test_a_different_message_on_the_same_target_is_still_reported(self):
        form = self._validated_form()

        form._add_service_errors(self._service_error("First problem."))
        form._add_service_errors(self._service_error("Second problem."))

        self.assertEqual(list(form.non_field_errors()), ["First problem.", "Second problem."])

    def test_the_same_message_on_a_different_target_keeps_both_locations(self):
        form = self._validated_form_with_a_managed_row()

        form._add_service_errors(self._service_error("Same complaint.", row_index=0))
        form._add_service_errors(self._service_error("Same complaint.", field="own_roles"))

        self.assertEqual(list(form.managed_formset.forms[0].non_field_errors()), ["Same complaint."])
        self.assertEqual(list(form.errors["own_roles"]), ["Same complaint."])


class DuplicateMembershipThroughTheFormTests(_FormDelegationTestBase):
    """Y-2, form half — a replayed create never becomes a second row.

    The direct-service half (``plan_membership_write`` raising
    ``DuplicateMembership``, and the ``(user, tenant)`` constraint behind it) is
    covered by the service suites. This pins the other end: ModelForm's own
    uniqueness validation rejects the identical resubmission during
    ``_post_clean``, before ``save()`` is ever reached.
    """

    def test_replaying_an_identical_create_is_rejected_with_exactly_one_row(self):
        first = self._create_form(self.superuser)
        self.assertTrue(first.is_valid(), first.errors.as_json())
        first.save()

        second = self._create_form(self.superuser)

        self.assertFalse(second.is_valid())
        self.assertIn("already exists", " ".join(second.errors.get("__all__", [])))
        self.assertEqual(Membership.objects.filter(user=self.member, tenant=self.provider).count(), 1)
        self.assertEqual(Membership.objects.count(), 1)

    def test_the_service_reports_the_duplicate_once_beside_the_uniqueness_error(self):
        """The two messages are different sentences from different layers; the
        service's must appear exactly once."""
        Membership.objects.create(user=self.member, tenant=self.provider)

        form = self._create_form(self.superuser)

        self.assertFalse(form.is_valid())
        rendered = list(form.non_field_errors())
        self.assertEqual(len([message for message in rendered if "already a member" in message]), 1)


class InlineIdentityThroughTheFormTests(_FormDelegationTestBase):
    """The who-block still creates or reuses an account, through the service."""

    def test_a_brand_new_email_creates_the_account_and_flags_the_view(self):
        data = membership_post_data(
            tenant=self.provider.pk,
            who=MembershipForm.WHO_NEW,
            new_user_email="fresh@example.com",
            new_user_first_name="Fresh",
            new_user_last_name="Hire",
            own_roles=[self.read_role.pk],
            managed=[],
        )
        form = MembershipForm(data=data, user=self.superuser, tenant=self.provider)

        self.assertTrue(form.is_valid(), form.errors.as_json())
        membership = form.save()

        self.assertTrue(form.new_user_created)
        self.assertEqual(membership.user.email, "fresh@example.com")
        self.assertFalse(membership.user.has_usable_password())

    def test_an_existing_email_is_reused_without_flagging_a_new_account(self):
        existing = User.objects.create_user(username="veteran", email="Veteran@Example.com")
        data = membership_post_data(
            tenant=self.provider.pk,
            who=MembershipForm.WHO_NEW,
            new_user_email="veteran@example.com",
            new_user_first_name="V",
            new_user_last_name="T",
            own_roles=[],
            managed=[],
        )
        form = MembershipForm(data=data, user=self.superuser, tenant=self.provider)

        self.assertTrue(form.is_valid(), form.errors.as_json())
        membership = form.save()

        self.assertFalse(form.new_user_created)
        self.assertEqual(membership.user_id, existing.pk)


class ElevatedMetadataThroughTheFormTests(_FormDelegationTestBase):
    """INV-5's asymmetry survives the delegation end to end."""

    def setUp(self):
        super().setUp()
        self.admin_role = Role.objects.create(
            tenant=self.provider,
            name="Admin",
            permissions=["assets.view_asset", "assets.change_asset"],
        )

    def test_a_privileged_own_role_without_metadata_is_rejected(self):
        form = self._create_form(self.superuser, own_roles=[self.admin_role.pk])

        self.assertFalse(form.is_valid())
        self.assertIn("reason", form.errors)

    def test_a_view_only_managed_row_keeps_its_operator_chosen_expiry(self):
        expiry = timezone.now() + timedelta(days=30)
        data = membership_post_data(
            tenant=self.provider.pk,
            user=self.member.pk,
            who=MembershipForm.WHO_EXISTING,
            own_roles=[],
            managed=[
                {
                    "role": self.read_role.pk,
                    "managed_scope": "explicit",
                    "assigned_tenants": [self.customer.pk],
                    "reason": "temporary audit",
                    "valid_until": expiry.strftime("%Y-%m-%dT%H:%M"),
                }
            ],
        )
        form = MembershipForm(data=data, user=self.superuser, tenant=self.provider)

        self.assertTrue(form.is_valid(), form.errors.as_json())
        membership = form.save()

        stored = membership.role_grants.get()
        self.assertEqual(stored.reason, "temporary audit")
        self.assertIsNotNone(stored.valid_until)
