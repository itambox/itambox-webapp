"""WS6 regression suite — robust inline identity resolution/creation.

``users.services`` centralises the inline "new user" onboarding write so it is
transaction-/race-safe, rejects ambiguous emails instead of silently picking the
lowest-PK account, and never overflows the username field with a long email. See
``RBAC_STAGE3_POST_REVIEW_FIX_PLAN.md`` §6.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from users.services import (
    AmbiguousEmailError, resolve_existing_user, resolve_or_create_user, _fitting_username,
)

User = get_user_model()

USERNAME_MAX = User._meta.get_field('username').max_length


class ResolveExistingUserTests(TestCase):
    def test_single_case_insensitive_match_is_returned(self):
        u = User.objects.create_user(username='ci', email='Person@Example.COM', password='pw')
        self.assertEqual(resolve_existing_user('person@example.com'), u)

    def test_no_match_returns_none(self):
        self.assertIsNone(resolve_existing_user('nobody@example.com'))

    def test_blank_email_returns_none(self):
        self.assertIsNone(resolve_existing_user(''))

    def test_duplicate_matches_fail_closed(self):
        # Email is not globally unique (SSO/import provision independently), so two
        # accounts CAN share one — resolution must fail closed, never pick the
        # lowest-PK row.
        User.objects.create_user(username='dup1', email='dup@example.com', password='pw')
        User.objects.create_user(username='dup2', email='DUP@example.com', password='pw')
        with self.assertRaises(AmbiguousEmailError):
            resolve_existing_user('dup@example.com')


class ResolveOrCreateUserTests(TestCase):
    def test_reuses_exactly_one_match_without_overwriting_profile(self):
        existing = User.objects.create_user(
            username='keep', email='Keep@Example.com', password='pw',
            first_name='Original', last_name='Name',
        )
        user, created = resolve_or_create_user(
            email='keep@example.com', first_name='New', last_name='Nope',
        )
        self.assertFalse(created)
        self.assertEqual(user.pk, existing.pk)
        existing.refresh_from_db()
        self.assertEqual(existing.first_name, 'Original')  # not overwritten
        self.assertTrue(existing.has_usable_password())

    def test_creates_with_unusable_password_and_email_username(self):
        user, created = resolve_or_create_user(
            email='fresh@example.com', first_name='Fresh', last_name='Hire',
        )
        self.assertTrue(created)
        self.assertEqual(user.email, 'fresh@example.com')
        self.assertEqual(user.username, 'fresh@example.com')
        self.assertFalse(user.has_usable_password())

    def test_long_email_creates_safe_username_and_keeps_full_email(self):
        local = 'a' * 200
        long_email = f'{local}@example.com'  # ~212 chars, > username max
        self.assertGreater(len(long_email), USERNAME_MAX)
        user, created = resolve_or_create_user(email=long_email, first_name='L', last_name='E')
        self.assertTrue(created)
        self.assertEqual(user.email, long_email)             # full email preserved
        self.assertLessEqual(len(user.username), USERNAME_MAX)  # username fits
        self.assertTrue(User.objects.filter(pk=user.pk).exists())

    def test_username_collision_with_a_different_email_binds_the_right_account(self):
        # An unrelated account already owns the would-be username (== the email).
        User.objects.create_user(
            username='taken@example.com', email='someone-else@example.com', password='pw',
        )
        user, created = resolve_or_create_user(
            email='taken@example.com', first_name='T', last_name='C',
        )
        self.assertTrue(created)
        self.assertEqual(user.email, 'taken@example.com')
        self.assertNotEqual(user.username, 'taken@example.com')  # a distinct, safe handle
        self.assertLessEqual(len(user.username), USERNAME_MAX)

    def test_ambiguous_email_raises(self):
        User.objects.create_user(username='a1', email='amb@example.com', password='pw')
        User.objects.create_user(username='a2', email='AMB@example.com', password='pw')
        with self.assertRaises(AmbiguousEmailError):
            resolve_or_create_user(email='amb@example.com')

    def test_fitting_username_is_deterministic_for_an_email(self):
        # Same email → same generated handle (so concurrent creates collide instead
        # of duplicating). Force generation with a too-long email.
        email = ('x' * 200) + '@example.com'
        self.assertEqual(_fitting_username(email), _fitting_username(email))


class InlineIdentityThroughFormTests(TestCase):
    """The membership form delegates the write to users.services end-to-end."""

    def setUp(self):
        from organization.models import Tenant
        from core.managers import set_current_tenant, set_current_membership
        set_current_tenant(None)
        set_current_membership(None)
        self.tenant = Tenant.objects.create(name='WS6 Corp', slug='ws6-corp')
        self.superuser = User.objects.create_superuser(
            username='ws6_su', email='ws6_su@x.com', password='pw',
        )

    def tearDown(self):
        from core.managers import set_current_tenant, set_current_membership
        set_current_tenant(None)
        set_current_membership(None)

    def _form(self, email):
        from organization.forms.membership_form import MembershipForm
        from organization.tests._membership_form_helpers import membership_post_data
        return MembershipForm(
            data=membership_post_data(
                who=MembershipForm.WHO_NEW, tenant=self.tenant.pk,
                new_user_email=email, new_user_first_name='F', new_user_last_name='L',
            ),
            tenant=self.tenant, user=self.superuser,
        )

    def test_ambiguous_email_is_rejected_with_no_membership(self):
        from organization.models import Membership
        User.objects.create_user(username='d1', email='dupe@ws6.test', password='pw')
        User.objects.create_user(username='d2', email='DUPE@ws6.test', password='pw')
        form = self._form('dupe@ws6.test')
        self.assertFalse(form.is_valid())
        self.assertIn('new_user_email', form.errors)
        self.assertFalse(Membership.objects.filter(tenant=self.tenant).exists())

    def test_long_email_creates_member_with_safe_username(self):
        long_email = ('z' * 200) + '@ws6.test'
        form = self._form(long_email)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        membership = form.save()
        self.assertEqual(membership.user.email, long_email)
        self.assertLessEqual(len(membership.user.username), USERNAME_MAX)


class RaceSafeInlineCreateTests(TestCase):
    """The concurrent-creation recovery path: our resolve saw nothing, but a
    concurrent request created the account first and won the race on the
    deterministic username. Our INSERT then loses on that username, and the service
    must re-resolve to the winner (never 500, never a duplicate) rather than
    surfacing the collision.

    Driven deterministically (no threads): the two racing conditions — resolve
    briefly seeing nothing, then a same-username collision — are simulated so the
    IntegrityError → re-resolve → reuse branch runs the same way it would under a
    real race.
    """

    def test_insert_race_reresolves_to_the_winning_account(self):
        winner = User.objects.create_user(
            username='race@example.com', email='race@example.com', password='pw',
        )
        real = resolve_existing_user
        calls = {'n': 0}

        def flaky(email):
            calls['n'] += 1
            # First look-up (top of resolve_or_create) sees nothing — the winner
            # raced in after; the re-resolve after IntegrityError sees the winner.
            return None if calls['n'] == 1 else real(email)

        with mock.patch('users.services.resolve_existing_user', side_effect=flaky), \
                mock.patch('users.services._fitting_username', return_value='race@example.com'):
            user, created = resolve_or_create_user(
                email='race@example.com', first_name='R', last_name='R',
            )
        self.assertFalse(created)
        self.assertEqual(user.pk, winner.pk)
        self.assertEqual(User.objects.filter(email__iexact='race@example.com').count(), 1)
