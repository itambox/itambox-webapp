"""``MembershipForm`` delegates domain decisions and keeps only presentation (#86).

Three separate claims are pinned here, and only the first two depend on the new
service layer:

* **Delegation (D-1).** The reconciliation and authorization methods the form used
  to own are gone, so the form can no longer coordinate grants behind the
  service's back.
* **Error location (D-2).** A service rejection that names a managed row renders
  on that row, not as a form-wide error — the reason ``ServiceError`` carries
  ``field``/``row_index`` at all.
* **Form-API contract (D-3/D-4).** ``save(commit=False)`` + ``save_m2m()`` writes
  the same rows a ``commit=True`` save would, and inline user creation still
  refuses ``commit=False``. These are unchanged behaviour and are asserted
  without the service layer so they keep their value as regression pins on both
  sides of the refactor.
"""

from unittest import mock

from django.test import TestCase
from django.urls import reverse

from core.tests.mixins import grant
from organization.forms.membership_form import MANAGED_FORMSET_PREFIX, MembershipForm
from organization.models import Membership, RoleGrant, RoleGrantScope
from users.models import GroupMembership, UserGroup

from ._membership_form_helpers import membership_post_data
from ._membership_service_adversarial_helpers import ServiceWorldMixin, future, membership_services


def grant_shape(membership):
    """The rows a membership's grants amount to, independent of pk and ordering."""
    return sorted(
        (
            item.role_id,
            item.granted_by_id,
            item.reason,
            item.valid_until,
            tuple(sorted(item.scopes.values_list("scope_type", "tenant_id", "tenant_group_id"))),
        )
        for item in membership.role_grants.prefetch_related("scopes")
    )


class MembershipFormTestCase(ServiceWorldMixin, TestCase):
    prefix = "delegate"

    def setUp(self):
        self.setup_service_world(self.prefix)

    def post_data(self, **kwargs):
        kwargs.setdefault("tenant", self.provider.pk)
        return membership_post_data(**kwargs)

    def build_form(self, *, actor=None, instance=None, **kwargs):
        return MembershipForm(
            data=self.post_data(**kwargs),
            instance=instance,
            user=self.superuser if actor is None else actor,
            tenant=self.provider,
        )


class FormDelegationSurfaceTests(MembershipFormTestCase):
    """D-1 — the form no longer coordinates grants or resolves identities itself."""

    RELOCATED = (
        "_sync_grants",
        "_sync_own_roles",
        "_intended_managed_rows",
        "_sync_managed_formset",
        "_create_inline_user",
        "_actor_may_manage_memberships",
    )

    def test_the_reconciliation_and_authorization_methods_are_gone(self):
        still_present = [name for name in self.RELOCATED if hasattr(MembershipForm, name)]
        self.assertEqual(
            still_present,
            [],
            "these moved to organization.services; a form that still owns them can reconcile "
            f"grants without the service's validate-before-write gate (found: {still_present})",
        )

    def test_the_presentation_contract_the_template_depends_on_is_unchanged(self):
        """§8 compatibility: the extraction must not move a single POST key.

        ``static/src/membership-form.ts`` and the row template address the managed
        formset by prefix and field name, so a renamed field is a silently broken
        UI that no service test would notice.
        """
        form = MembershipForm(user=self.superuser, tenant=self.provider)
        self.assertIsNotNone(form.managed_formset)
        self.assertFalse(form.new_user_created)
        self.assertEqual(MANAGED_FORMSET_PREFIX, "managed")
        self.assertEqual(form.managed_formset.prefix, MANAGED_FORMSET_PREFIX)
        self.assertEqual(
            sorted(form.managed_formset.empty_form.fields),
            sorted(
                [
                    "id",
                    "role",
                    "managed_scope",
                    "scope_group",
                    "assigned_tenants",
                    "reason",
                    "valid_until",
                    "DELETE",
                ]
            ),
        )


class DeferredSaveEquivalenceTests(MembershipFormTestCase):
    """D-3/D-4 — ``commit=False`` is a two-step save, never a silent grant drop.

    Asserted without loading the service layer: this is the ModelForm API
    contract and must hold identically before and after the extraction.
    """

    def setUp(self):
        super().setUp()
        # One timestamp for both submissions: the two forms must be byte-identical
        # inputs, or the comparison below would diff on microseconds.
        self.expiry = future().isoformat()

    def managed_rows(self):
        return [
            {
                "role": self.other_read_role.pk,
                "managed_scope": "explicit",
                "assigned_tenants": [self.customer_a.pk, self.customer_z.pk],
                "reason": "handover",
                "valid_until": self.expiry,
            }
        ]

    def form_for(self, user):
        return self.build_form(
            user=user.pk,
            who=MembershipForm.WHO_EXISTING,
            own_roles=[self.read_role.pk],
            managed=self.managed_rows(),
        )

    def test_deferred_save_m2m_writes_the_same_rows_as_a_committed_save(self):
        committed_form = self.form_for(self.member)
        self.assertTrue(committed_form.is_valid(), committed_form.errors.as_json())
        committed = committed_form.save()

        deferred_user = self.make_user("delegate-deferred")
        deferred_form = self.form_for(deferred_user)
        self.assertTrue(deferred_form.is_valid(), deferred_form.errors.as_json())
        instance = deferred_form.save(commit=False)
        self.assertIsNone(instance.pk, "commit=False must not persist the row")
        self.assertFalse(
            RoleGrant._base_manager.filter(membership__user=deferred_user).exists(),
            "commit=False must not write grants either",
        )
        instance.save()
        deferred_form.save_m2m()

        self.assertEqual(grant_shape(instance), grant_shape(committed))
        self.assertTrue(RoleGrant._base_manager.filter(membership=instance, role=self.read_role).exists())

    def test_inline_user_creation_refuses_a_deferred_save(self):
        """D-4 — ``Membership.user`` is a required FK, so a deferred save would have
        to persist the account now; fail loudly instead of writing one."""
        form = self.build_form(
            who=MembershipForm.WHO_NEW,
            new_user_email="deferred@delegate.test",
            new_user_first_name="D",
            new_user_last_name="S",
            own_roles=[],
            managed=[],
        )
        self.assertTrue(form.is_valid(), form.errors.as_json())
        with self.assert_writes_nothing("a refused deferred inline-user save"):
            with self.assertRaises(ValueError):
                form.save(commit=False)
        self.assertFalse(self.member.__class__.objects.filter(email="deferred@delegate.test").exists())


class ServiceErrorLocationTests(MembershipFormTestCase):
    """D-2 — a rejection that names a managed row must render on that row.

    ``ServiceError.row_index`` is an index into ``managed_formset.forms``, so an
    admin sees the message beside the row that caused it instead of a form-wide
    error they have to match up by hand.
    """

    def setUp(self):
        super().setUp()
        self.svc = membership_services()

    def test_a_duplicate_managed_role_renders_on_the_second_row(self):
        form = self.build_form(
            user=self.member.pk,
            who=MembershipForm.WHO_EXISTING,
            own_roles=[],
            managed=[
                {
                    "role": self.read_role.pk,
                    "managed_scope": "explicit",
                    "assigned_tenants": [self.customer_a.pk],
                },
                {
                    "role": self.read_role.pk,
                    "managed_scope": "explicit",
                    "assigned_tenants": [self.customer_z.pk],
                },
            ],
        )

        with self.assert_writes_nothing("a rejected duplicate-role submission"):
            self.assertFalse(form.is_valid())

        rows = form.managed_formset.forms
        self.assertTrue(rows[1].errors, "the second occurrence is the one to flag")
        self.assertFalse(rows[0].errors, "the first occurrence is legitimate on its own")
        self.assertEqual(form.non_field_errors(), [])
        self.assertEqual(
            list(form.managed_formset.non_form_errors()),
            [],
            "the duplicate-role rule became a located plan check, not a formset-wide error",
        )

    def test_an_escalated_managed_row_renders_on_that_row_only(self):
        actor = self.actor_with(
            "delegate-narrow",
            [
                "organization.add_membership",
                "organization.change_membership",
                "organization.add_rolegrant",
                "assets.view_asset",
            ],
            coverage=[self.customer_a],
            coverage_permissions=["assets.view_asset"],
        )
        form = self.build_form(
            actor=actor,
            user=self.member.pk,
            who=MembershipForm.WHO_EXISTING,
            own_roles=[],
            managed=[
                {
                    "role": self.read_role.pk,
                    "managed_scope": "explicit",
                    "assigned_tenants": [self.customer_a.pk],
                },
                {
                    "role": self.other_read_role.pk,
                    "managed_scope": "explicit",
                    "assigned_tenants": [self.customer_z.pk],
                },
            ],
        )

        with self.assert_writes_nothing("a rejected escalating managed row"):
            self.assertFalse(form.is_valid())

        rows = form.managed_formset.forms
        self.assertTrue(rows[1].errors)
        self.assertFalse(rows[0].errors)

    def test_own_reach_rejections_stay_on_the_main_form(self):
        """The complement: own-reach escalation carries no ``row_index``, so it
        must remain in ``form.non_field_errors()`` exactly as today."""
        actor = self.actor_with(
            "delegate-own-narrow",
            ["organization.add_membership", "organization.change_membership", "assets.view_asset"],
        )
        form = self.build_form(
            actor=actor,
            user=self.member.pk,
            who=MembershipForm.WHO_EXISTING,
            own_roles=[self.editor_role.pk],
            reason="ops",
            valid_until=future().isoformat(),
            managed=[],
        )

        with self.assert_writes_nothing("a rejected own-reach escalation"):
            self.assertFalse(form.is_valid())

        self.assertIn("assets.change_asset", " ".join(form.non_field_errors()))


class FormServiceAuthorizationTests(MembershipFormTestCase):
    """T6/T8 — the A1/A2 gate reaches the form, and the oracle defence survives it.

    Before #86 a directly-constructed form with an unauthorized actor and a
    *fresh* email would validate and write; only the view's ``_authorized_tenant``
    stood in the way. Both halves are asserted: the form is now invalid, and the
    same actor is rejected calling the service directly.
    """

    def setUp(self):
        super().setUp()
        self.svc = membership_services()
        self.outsider = self.actor_with("delegate-outsider", ["organization.view_membership"])

    def test_a_directly_built_form_with_an_unauthorized_actor_writes_nothing(self):
        form = self.build_form(
            actor=self.outsider,
            who=MembershipForm.WHO_NEW,
            new_user_email="fresh@delegate.test",
            new_user_first_name="F",
            new_user_last_name="R",
            own_roles=[],
            managed=[],
        )

        with self.assert_writes_nothing("a form bound to an unauthorized actor"):
            self.assertFalse(form.is_valid())

        self.assertFalse(self.member.__class__.objects.filter(email="fresh@delegate.test").exists())
        self.assertFalse(Membership.objects.filter(tenant=self.provider, user__email="fresh@delegate.test").exists())

    def test_the_same_actor_is_rejected_calling_the_service_directly(self):
        newcomer = self.make_user("delegate-newcomer")
        with self.assert_writes_nothing("the same actor at the service boundary"):
            with self.assertRaises(self.svc.ActorNotAuthorized):
                self.svc.execute_membership_write(
                    actor=self.outsider,
                    intent=self.svc.MembershipIntent(tenant=self.provider, user=newcomer),
                )

    def test_the_membership_oracle_defence_still_owns_the_email_field(self):
        """§12 risk row — planning only ADDS errors, so ``_clean_who``'s
        non-revealing message must still be the one on ``new_user_email``."""
        self.member.email = "insider@delegate.test"
        self.member.save(update_fields=["email"])
        self.membership_for(self.member, self.provider)

        form = self.build_form(
            actor=self.outsider,
            who=MembershipForm.WHO_NEW,
            new_user_email="insider@delegate.test",
            new_user_first_name="I",
            new_user_last_name="N",
            own_roles=[],
            managed=[],
        )

        with self.assert_writes_nothing("an unauthorized membership probe through the form"):
            self.assertFalse(form.is_valid())

        errors = " ".join(form.errors.get("new_user_email", []))
        self.assertIn("cannot be added", errors)
        self.assertNotIn("already a member", errors)


class SaveTimeRevalidationTests(MembershipFormTestCase):
    """INV-14 — the locked-row derivation is the authoritative one.

    ``MembershipForm.clean()`` derives the reactivation transition from the row as
    loaded so a blocked reactivation renders as a form error rather than a 500.
    That derivation is for reporting only: ``execute_membership_write`` re-derives
    it from the ``select_for_update()``-locked row and re-plans there, so a group
    that lands between validation and save is still caught.
    """

    def setUp(self):
        super().setUp()
        self.svc = membership_services()
        self.actor = self.actor_with(
            "delegate-react",
            [
                "organization.change_membership",
                "organization.add_rolegrant",
                "assets.delete_asset",
            ],
        )
        self.membership = self.membership_for(self.member, self.provider, is_active=False)

    def projecting_group(self):
        group = UserGroup.objects.create(tenant=self.provider, name="Delegate projected admins")
        projected_role = self.make_role("Delegate projected deleter", ["assets.delete_asset"])
        group_grant = RoleGrant.objects.create(user_group=group, role=projected_role)
        RoleGrantScope.objects.create(
            role_grant=group_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )
        return group

    def reactivation_form(self):
        return MembershipForm(
            data=self.post_data(user=self.member.pk, own_roles=[], managed=[], is_active=True),
            instance=self.membership,
            user=self.actor,
            tenant=self.provider,
        )

    def test_a_group_added_between_validation_and_save_still_blocks_the_write(self):
        form = self.reactivation_form()
        self.assertTrue(form.is_valid(), form.errors.as_json())

        # A concurrent group assignment the form's clean() could not have seen.
        GroupMembership.objects.create(user_group=self.projecting_group(), membership=self.membership)

        with self.assert_writes_nothing("a reactivation blocked at write time"):
            with self.assertRaises(self.svc.EscalationDenied):
                form.save()

        self.membership.refresh_from_db()
        self.assertFalse(
            self.membership.is_active,
            "the row must stay inactive: the whole write unwinds together (INV-15)",
        )

    def test_a_reactivation_the_actor_may_perform_still_succeeds(self):
        """Positive control — the guard is a boundary, not a blanket refusal."""
        GroupMembership.objects.create(user_group=self.projecting_group(), membership=self.membership)
        grant(
            self.actor,
            self.provider,
            self.make_role("Delegate react coverage", ["assets.delete_asset"]),
            reach=RoleGrant.REACH_MANAGED,
            assigned_tenants=[self.customer_a],
        )

        form = self.reactivation_form()
        self.assertTrue(form.is_valid(), form.errors.as_json())
        form.save()

        self.membership.refresh_from_db()
        self.assertTrue(self.membership.is_active)


class SaveTimeServiceErrorRendersInTheViewTests(MembershipFormTestCase):
    """Y-6 — a rejection raised AFTER ``clean()`` re-renders; it is not a 500.

    ``ConcurrentGrantChange`` is a typed service error rather than a
    ``ValueError`` precisely so the form can be re-rendered: an unmigrated §7.5
    writer (or a grant whose ``valid_until`` simply elapses between the two
    reads) can legitimately win the race, and that is not a programmer error. The
    type existed before this suite; nothing drove a *view* through it, and
    ``ObjectEditView.form_valid`` calls ``form.save()`` with no handler — so the
    admin's POST answered HTTP 500 instead of the resubmit message.
    """

    prefix = "delegate-save-error"

    def setUp(self):
        super().setUp()
        self.svc = membership_services()
        self.membership = self.membership_for(self.member, self.provider)
        self.client.force_login(self.superuser)
        session = self.client.session
        session["active_tenant_id"] = self.provider.pk
        session.pop("active_tenant_group_id", None)
        session.save()

    def edit_url(self):
        return reverse("organization:membership_update", args=[self.membership.pk])

    def valid_edit_payload(self, **overrides):
        payload = {
            "user": self.member.pk,
            "own_roles": [self.read_role.pk],
            "managed": [],
            "is_active": True,
        }
        payload.update(overrides)
        return self.post_data(**payload)

    def test_a_lost_grant_race_at_save_time_re_renders_instead_of_500(self):
        losing_race = self.svc.ConcurrentGrantChange.single(
            "Another change to this membership's roles landed first. Review and resubmit."
        )

        with self.assert_writes_nothing("a POST that lost the grant race at save time"):
            with mock.patch(
                "organization.services.membership.sync_membership_grants",
                side_effect=[losing_race],
            ) as sync:
                response = self.client.post(self.edit_url(), self.valid_edit_payload())

        self.assertEqual(sync.call_count, 1, "the view must re-render, not retry the write")
        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "review and resubmit",
            " ".join(response.context["form"].non_field_errors()).lower(),
        )

    def test_a_reactivation_blocked_at_write_time_re_renders_without_any_patching(self):
        """The same handler, driven by a genuinely raised ``EscalationDenied``.

        ``MembershipForm.clean()`` planned against the row as loaded and found
        nothing wrong; the group lands afterwards, so only the locked-row
        re-plan inside ``execute_membership_write`` can catch it.
        """
        actor = self.actor_with(
            "delegate-save-error-react",
            ["organization.change_membership", "organization.add_rolegrant", "assets.delete_asset"],
        )
        self.client.force_login(actor)
        session = self.client.session
        session["active_tenant_id"] = self.provider.pk
        session.save()
        Membership.objects.filter(pk=self.membership.pk).update(is_active=False)
        group = UserGroup.objects.create(tenant=self.provider, name="Delegate save-error admins")
        group_grant = RoleGrant.objects.create(
            user_group=group,
            role=self.make_role("Delegate save-error deleter", ["assets.delete_asset"]),
        )
        RoleGrantScope.objects.create(
            role_grant=group_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer_a,
        )

        real_plan = self.svc.plan_membership_write

        def race_then_plan(*args, **kwargs):
            # The concurrent group assignment the form's clean() could not have
            # seen, landing after the read-only plan and before the locked one.
            # (``MembershipForm`` holds its own reference to the real function,
            # so only the re-plan inside the transaction is wrapped.)
            GroupMembership.objects.get_or_create(user_group=group, membership=self.membership)
            return real_plan(*args, **kwargs)

        with mock.patch(
            "organization.services.membership.plan_membership_write",
            side_effect=race_then_plan,
        ):
            response = self.client.post(self.edit_url(), self.valid_edit_payload(own_roles=[]))

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "outside your own reach",
            " ".join(response.context["form"].non_field_errors()).lower(),
        )
        self.membership.refresh_from_db()
        self.assertFalse(self.membership.is_active, "the blocked reactivation must not have been persisted")
