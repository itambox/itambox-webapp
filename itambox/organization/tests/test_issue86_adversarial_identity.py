"""Inline identity resolution and creation at the membership service (issue #86).

The "add a member by email" flow either reuses an account or creates one, and it
must do so through ``users.services.resolve_or_create_user`` only (INV-11) — never
a direct ``User(...)`` insert. That delegation is what keeps the write inside a
savepoint whose ``IntegrityError`` is caught and re-resolved, keeps the username
inside ``max_length`` while the full address stays in ``User.email``, and keeps a
new account without a usable password. Those observable properties are asserted
here rather than the call itself, so the tests survive any refactor that keeps the
contract.

Ambiguity fails closed, a duplicate ``(user, tenant)`` is rejected, and a failure
anywhere in the write takes the inline-created account with it (INV-15).
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save
from django.test import TestCase

from core.models import ObjectChange
from core.tasks.context import TaskContext
from organization.models import Membership, RoleGrant, RoleGrantScope

from ._membership_service_adversarial_helpers import ServiceWorldMixin, membership_services

User = get_user_model()

USERNAME_MAX = User._meta.get_field("username").max_length


class IdentityServiceTestCase(ServiceWorldMixin, TestCase):
    prefix = "ident"

    def setUp(self):
        self.svc = membership_services()
        self.setup_service_world(self.prefix)

    def new_identity(self, email, *, first_name="Fresh", last_name="Hire"):
        return self.svc.NewIdentitySpec(email=email, first_name=first_name, last_name=last_name)

    def intent(self, **kwargs):
        kwargs.setdefault("tenant", self.provider)
        return self.svc.MembershipIntent(**kwargs)

    def own(self, role, **kwargs):
        return self.svc.OwnGrantSpec(role=role, **kwargs)

    def create(self, intent, *, actor=None):
        return self.svc.execute_membership_write(actor=self.superuser if actor is None else actor, intent=intent)


class InlineIdentityCreationTests(IdentityServiceTestCase):
    def test_a_fresh_email_creates_a_passwordless_account_and_its_grants(self):
        """I-1 — one call writes identity, membership, and own reach together."""
        result = self.create(
            self.intent(
                new_identity=self.new_identity("newcomer@ident.test"),
                own_roles=(self.own(self.read_role),),
            )
        )

        self.assertTrue(result.identity_created)
        self.assertTrue(result.membership_created)
        created = User.objects.get(email="newcomer@ident.test")
        self.assertFalse(
            created.has_usable_password(),
            "credentials are issued by the explicit 'send setup link' action, never implicitly",
        )
        membership = Membership.objects.get(user=created, tenant=self.provider)
        self.assertEqual(result.membership, membership)
        own_grant = RoleGrant._base_manager.get(membership=membership, role=self.read_role)
        self.assertEqual(
            list(own_grant.scopes.values_list("scope_type", flat=True)),
            [RoleGrantScope.SCOPE_OWN],
        )

    def test_an_existing_email_is_reused_without_touching_its_profile(self):
        """I-2 — get-or-create semantics, case-insensitive, non-destructive."""
        existing = self.make_user(
            "ident-existing",
            email="Reuse@Ident.TEST",
            first_name="Original",
            last_name="Name",
        )
        existing.set_password("keep-me")
        existing.save(update_fields=["password"])

        result = self.create(self.intent(new_identity=self.new_identity("reuse@ident.test")))

        self.assertFalse(result.identity_created)
        self.assertEqual(result.membership.user_id, existing.pk)
        self.assertEqual(User.objects.filter(email__iexact="reuse@ident.test").count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.first_name, "Original")
        self.assertEqual(existing.last_name, "Name")
        self.assertTrue(existing.has_usable_password())

    def test_an_ambiguous_email_fails_closed_and_writes_nothing(self):
        """I-3 — email is deliberately not globally unique, so never pick one."""
        self.make_user("ident-dupe-1", email="ambiguous@ident.test")
        self.make_user("ident-dupe-2", email="AMBIGUOUS@ident.test")

        with self.assert_writes_nothing("an ambiguous inline identity"):
            with self.assertRaises(self.svc.AmbiguousIdentity):
                self.create(
                    self.intent(
                        new_identity=self.new_identity("ambiguous@ident.test"),
                        own_roles=(self.own(self.read_role),),
                    )
                )

    def test_resolve_identity_never_creates(self):
        """The planning half is resolve-only; the write lives in the apply phase."""
        with self.assert_writes_nothing("a resolve-only identity lookup"):
            self.assertIsNone(self.svc.resolve_identity(spec=self.new_identity("nobody@ident.test")))
        existing = self.make_user("ident-resolvable", email="known@ident.test")
        self.assertEqual(self.svc.resolve_identity(spec=self.new_identity("KNOWN@ident.test")), existing)

    def test_a_long_email_keeps_a_valid_username_and_the_full_address(self):
        """I-4 — a 254-char address cannot be copied into a 150-char username."""
        long_email = ("z" * 200) + "@ident.test"
        self.assertGreater(len(long_email), USERNAME_MAX)

        result = self.create(self.intent(new_identity=self.new_identity(long_email)))

        created = result.membership.user
        self.assertEqual(created.email, long_email)
        self.assertLessEqual(len(created.username), USERNAME_MAX)

    def test_a_taken_username_does_not_bind_the_wrong_account(self):
        """An unrelated account already owning the would-be username must not be
        adopted — the service takes a distinct, collision-resistant handle."""
        squatter = self.make_user("taken@ident.test", email="someone-else@ident.test")

        result = self.create(self.intent(new_identity=self.new_identity("taken@ident.test")))

        self.assertTrue(result.identity_created)
        self.assertNotEqual(result.membership.user_id, squatter.pk)
        self.assertEqual(result.membership.user.email, "taken@ident.test")
        self.assertNotEqual(result.membership.user.username, "taken@ident.test")
        self.assertLessEqual(len(result.membership.user.username), USERNAME_MAX)

    def test_an_explicit_user_wins_over_a_new_identity_block(self):
        """I-5 — the precedence is documented, so pin it rather than leave it to
        whichever branch happens to run first."""
        chosen = self.make_user("ident-chosen", email="chosen@ident.test")

        result = self.create(self.intent(user=chosen, new_identity=self.new_identity("shadow@ident.test")))

        self.assertFalse(result.identity_created)
        self.assertEqual(result.membership.user_id, chosen.pk)
        self.assertFalse(User.objects.filter(email="shadow@ident.test").exists())


class DuplicateMembershipTests(IdentityServiceTestCase):
    """A15 / Y-2 — one membership per ``(user, tenant)``, on both code paths."""

    def test_a_second_create_for_the_same_pair_is_rejected(self):
        first = self.create(self.intent(new_identity=self.new_identity("repeat@ident.test")))

        with self.assert_writes_nothing("a repeated create"):
            with self.assertRaises(self.svc.DuplicateMembership):
                self.create(self.intent(new_identity=self.new_identity("repeat@ident.test")))

        self.assertEqual(Membership.objects.filter(user=first.membership.user, tenant=self.provider).count(), 1)

    def test_an_update_never_collides_with_its_own_row(self):
        """The duplicate check is a CREATE rule: on update the ``(user, tenant)``
        pair IS the row being edited."""
        membership = self.membership_for(self.member, self.provider)
        result = self.svc.execute_membership_write(
            actor=self.superuser,
            intent=self.intent(user=self.member, own_roles=(self.own(self.read_role),)),
            membership=membership,
        )
        self.assertFalse(result.membership_created)
        self.assertEqual(result.membership.pk, membership.pk)


class InlineIdentityRollbackTests(IdentityServiceTestCase):
    """R-2 / INV-15 — the inline account is part of the write, not a side effect."""

    def fail_on_scope_write(self):
        def explode(sender, instance, created, **kwargs):
            if created:
                raise RuntimeError("simulated failure after the identity was created")

        post_save.connect(explode, sender=RoleGrantScope, weak=False)
        self.addCleanup(post_save.disconnect, explode, sender=RoleGrantScope)

    def test_a_failure_after_the_insert_removes_the_inline_account(self):
        self.fail_on_scope_write()

        with self.assert_writes_nothing("a create that failed after the identity insert"):
            with self.assertRaises(RuntimeError):
                self.create(
                    self.intent(
                        new_identity=self.new_identity("rollback@ident.test"),
                        own_roles=(self.own(self.read_role),),
                    )
                )

        self.assertFalse(
            User.objects.filter(email="rollback@ident.test").exists(),
            "the inline-created account must not survive a failed membership write",
        )
        self.assertFalse(Membership.objects.filter(tenant=self.provider, user__email="rollback@ident.test").exists())

    def test_a_rejected_plan_never_reaches_the_identity_write(self):
        """The plan runs before any mutation, so a cross-tenant role rejects the
        whole call without an account ever existing."""
        with self.assert_writes_nothing("a create whose plan was rejected"):
            with self.assertRaises(self.svc.CrossTenantObject):
                self.create(
                    self.intent(
                        new_identity=self.new_identity("never@ident.test"),
                        own_roles=(self.own(self.rival_role),),
                    )
                )
        self.assertFalse(User.objects.filter(email="never@ident.test").exists())


class ServiceWriteAttributionTests(IdentityServiceTestCase):
    """Design §11 — a non-HTTP caller must wrap the write in ``TaskContext``.

    ``ChangeLoggingMixin._log_change`` returns early when ``_request_id`` is unset,
    so a management command or django-q task that forgets the wrapper writes
    grants with no audit trail and no error anywhere. The service does not set
    that context; asserting the attributed rows is the only way to prove the
    documented contract is reachable.
    """

    def test_a_service_write_inside_task_context_is_attributed(self):
        newcomer = self.make_user("ident-task-target")
        before = ObjectChange._base_manager.count()

        with TaskContext(tenant_id=self.provider.pk, user_id=self.superuser.pk):
            result = self.svc.execute_membership_write(
                actor=self.superuser,
                intent=self.intent(user=newcomer, own_roles=(self.own(self.read_role),)),
            )

        self.assertGreater(ObjectChange._base_manager.count(), before)
        grant_type = ContentType.objects.get_for_model(RoleGrant)
        created_grant = RoleGrant._base_manager.get(membership=result.membership, role=self.read_role)
        change = ObjectChange._base_manager.get(
            changed_object_type=grant_type,
            changed_object_id=created_grant.pk,
            action="create",
        )
        self.assertEqual(change.user_id, self.superuser.pk)
        self.assertEqual(change.tenant_id, self.provider.pk)
