"""MembershipForm keeps grant state in relational rows, never JSON payloads."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from organization.forms.membership_form import MembershipForm
from organization.models import Membership, Role, RoleGrantScope, Tenant
from organization.tests._membership_form_helpers import membership_post_data

User = get_user_model()


class MembershipFormRelationalGrantTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_superuser(username='admin')
        self.provider = Tenant.objects.create(
            name='Provider', slug='provider', is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name='Customer', slug='customer', managed_by=self.provider,
        )
        self.role = Role.objects.create(
            tenant=self.customer,
            name='Reader',
            permissions=['assets.view_asset'],
        )

    def test_form_has_no_json_grant_field(self):
        form = MembershipForm(user=self.actor, tenant=self.customer)
        self.assertNotIn('roles_json', form.fields)
        self.assertNotIn('grants_json', form.fields)
        self.assertIn('own_roles', form.fields)

    def test_inline_user_creation_writes_membership_and_scope_rows(self):
        data = membership_post_data(
            tenant=self.customer.pk,
            who=MembershipForm.WHO_NEW,
            new_user_email='new.user@example.com',
            new_user_first_name='New',
            new_user_last_name='User',
            own_roles=[self.role.pk],
        )
        form = MembershipForm(data=data, user=self.actor, tenant=self.customer)

        self.assertTrue(form.is_valid(), form.errors.as_json())
        membership = form.save()

        self.assertEqual(membership.user.email, 'new.user@example.com')
        self.assertFalse(membership.user.has_usable_password())
        role_grant = membership.role_grants.get(role=self.role)
        self.assertEqual(
            role_grant.scopes.get().scope_type,
            RoleGrantScope.SCOPE_OWN,
        )

    def test_inline_email_reuses_existing_user(self):
        existing = User.objects.create_user(
            username='existing',
            email='existing@example.com',
        )
        data = membership_post_data(
            tenant=self.customer.pk,
            who=MembershipForm.WHO_NEW,
            new_user_email='EXISTING@example.com',
            new_user_first_name='Existing',
            new_user_last_name='User',
            own_roles=[],
        )
        form = MembershipForm(data=data, user=self.actor, tenant=self.customer)

        self.assertTrue(form.is_valid(), form.errors.as_json())
        membership = form.save()
        self.assertEqual(membership.user, existing)
        self.assertEqual(User.objects.filter(email__iexact='existing@example.com').count(), 1)

    def test_commit_false_rejects_inline_user_side_effect(self):
        data = membership_post_data(
            tenant=self.customer.pk,
            who=MembershipForm.WHO_NEW,
            new_user_email='deferred@example.com',
            new_user_first_name='Deferred',
            new_user_last_name='User',
            own_roles=[],
        )
        form = MembershipForm(data=data, user=self.actor, tenant=self.customer)
        self.assertTrue(form.is_valid(), form.errors.as_json())
        with self.assertRaises(ValueError):
            form.save(commit=False)
        self.assertFalse(User.objects.filter(email='deferred@example.com').exists())

    def test_duplicate_membership_is_rejected(self):
        user = User.objects.create_user(
            username='already-member',
            email='already@example.com',
        )
        Membership.objects.create(user=user, tenant=self.customer)
        data = membership_post_data(
            tenant=self.customer.pk,
            who=MembershipForm.WHO_EXISTING,
            user=user.pk,
            own_roles=[],
        )
        form = MembershipForm(data=data, user=self.actor, tenant=self.customer)
        self.assertFalse(form.is_valid())
