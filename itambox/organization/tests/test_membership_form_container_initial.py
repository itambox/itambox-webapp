"""MembershipForm container, visibility, and initial-state coverage."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.tests.mixins import grant
from organization.forms.membership_form import MembershipForm
from organization.models import Membership, Role, RoleGrantScope, Tenant
from organization.tests._membership_form_helpers import membership_post_data

User = get_user_model()


class MembershipFormContainerInitialTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_superuser(username='membership-admin')
        self.user = User.objects.create_user(username='member')
        self.provider = Tenant.objects.create(
            name='Provider', slug='provider', is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name='Customer', slug='customer', managed_by=self.provider,
        )
        self.provider_role = Role.objects.create(
            tenant=self.provider,
            name='Provider reader',
            permissions=['assets.view_asset'],
        )
        self.shared_role = Role.objects.create(
            tenant=self.provider,
            name='Shared reader',
            permissions=['assets.view_asset'],
            shared_with_managed=True,
        )
        self.customer_role = Role.objects.create(
            tenant=self.customer,
            name='Customer reader',
            permissions=['assets.view_asset'],
        )

    def test_customer_role_picker_contains_local_and_shared_provider_roles(self):
        form = MembershipForm(user=self.actor, tenant=self.customer)
        self.assertEqual(
            set(form.fields['own_roles'].queryset),
            {self.customer_role, self.shared_role},
        )
        self.assertIsNone(form.managed_formset)

    def test_provider_form_offers_managed_grant_formset(self):
        form = MembershipForm(user=self.actor, tenant=self.provider)
        self.assertIsNotNone(form.managed_formset)
        self.assertEqual(
            set(form.fields['own_roles'].queryset),
            {self.provider_role, self.shared_role},
        )

    def test_edit_seeds_own_and_managed_grants_independently(self):
        own = grant(self.user, self.provider, self.provider_role)
        managed = grant(
            self.user,
            self.provider,
            self.shared_role,
            reach='managed',
            managed_scope=RoleGrantScope.SCOPE_ALL_MANAGED,
        )

        form = MembershipForm(instance=own.membership, user=self.actor)

        self.assertEqual(list(form.fields['own_roles'].initial), [self.provider_role.pk])
        rows = form.managed_formset.initial
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['id'], managed.pk)
        self.assertEqual(rows[0]['managed_scope'], RoleGrantScope.SCOPE_ALL_MANAGED)

    def test_technician_preset_creates_all_managed_row(self):
        technician = Role.objects.create(
            tenant=self.provider,
            name='Technician',
            permissions=['assets.view_asset'],
            shared_with_managed=True,
        )
        form = MembershipForm(
            user=self.actor,
            tenant=self.provider,
            preset=MembershipForm.PRESET_TECHNICIAN,
        )

        self.assertEqual(form.fields['who'].initial, MembershipForm.WHO_NEW)
        self.assertEqual(form.managed_formset.initial, [{
            'role': technician.pk,
            'managed_scope': RoleGrantScope.SCOPE_ALL_MANAGED,
        }])

    def test_no_roles_creates_roleless_membership(self):
        data = membership_post_data(
            tenant=self.customer.pk,
            user=self.user.pk,
            who=MembershipForm.WHO_EXISTING,
            own_roles=[],
        )
        form = MembershipForm(data=data, user=self.actor, tenant=self.customer)

        self.assertTrue(form.is_valid(), form.errors.as_json())
        membership = form.save()
        self.assertEqual(membership.role_grants.count(), 0)

    def test_edit_reconciles_own_selection_without_touching_managed_scope(self):
        own = grant(self.user, self.provider, self.provider_role)
        managed = grant(
            self.user,
            self.provider,
            self.shared_role,
            reach='managed',
            managed_scope=RoleGrantScope.SCOPE_ALL_MANAGED,
        )
        data = membership_post_data(
            tenant=self.provider.pk,
            user=self.user.pk,
            own_roles=[],
            managed=[{
                'id': managed.pk,
                'role': self.shared_role.pk,
                'managed_scope': RoleGrantScope.SCOPE_ALL_MANAGED,
            }],
        )
        form = MembershipForm(
            data=data,
            instance=own.membership,
            user=self.actor,
        )

        self.assertTrue(form.is_valid(), form.errors.as_json())
        membership = form.save()
        self.assertFalse(membership.role_grants.filter(pk=own.pk).exists())
        self.assertTrue(membership.role_grants.filter(pk=managed.pk).exists())
        self.assertEqual(
            managed.scopes.get().scope_type,
            RoleGrantScope.SCOPE_ALL_MANAGED,
        )
