"""Grant audit surfaces read from RoleGrant and GroupMembership only."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from organization.access import tenant_access_report
from organization.models import (
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
)
from users.models import GroupMembership, UserGroup


User = get_user_model()


class GrantVisibilityTests(TestCase):
    def setUp(self):
        self.provider = Tenant.objects.create(
            name='Visibility Provider', slug='visibility-provider', is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name='Visibility Customer', slug='visibility-customer',
            managed_by=self.provider,
        )
        self.admin = User.objects.create_superuser(
            username='visibility-admin', email='visibility-admin@example.com', password='pw',
        )
        self.tech = User.objects.create_user(username='visibility-tech')
        self.membership = Membership.objects.create(user=self.tech, tenant=self.provider)
        self.role = Role.objects.create(
            tenant=self.provider,
            name='Visibility reader',
            permissions=['assets.view_asset'],
        )

    def direct_grant(self, scope_type, **scope_kwargs):
        grant = RoleGrant.objects.create(membership=self.membership, role=self.role)
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=scope_type,
            **scope_kwargs,
        )
        return grant

    def login_at(self, tenant):
        self.client.force_login(self.admin)
        session = self.client.session
        session['active_tenant_id'] = tenant.pk
        session.save()

    def test_external_report_shows_provider_direct_grant(self):
        self.direct_grant(RoleGrantScope.SCOPE_TENANT, tenant=self.customer)

        report = tenant_access_report(self.customer, external_only=True)

        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]['user'], self.tech)
        self.assertEqual(report[0]['sources'], ['managed'])
        self.assertEqual(report[0]['permissions'], ['assets.view_asset'])

    def test_external_report_shows_group_provenance(self):
        group = UserGroup.objects.create(
            tenant=self.provider,
            name='Visibility technicians',
        )
        GroupMembership.objects.create(user_group=group, membership=self.membership)
        grant = RoleGrant.objects.create(user_group=group, role=self.role)
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=self.customer,
        )

        row = tenant_access_report(self.customer, external_only=True)[0]

        self.assertEqual(row['sources'], ['group', 'managed'])
        self.assertEqual(row['groups'], [group.name])

    def test_local_membership_removes_user_from_external_only_report(self):
        self.direct_grant(RoleGrantScope.SCOPE_TENANT, tenant=self.customer)
        Membership.objects.create(user=self.tech, tenant=self.customer)

        self.assertEqual(tenant_access_report(self.customer, external_only=True), [])

    def test_membership_detail_lists_canonical_grant_and_scope(self):
        grant = self.direct_grant(RoleGrantScope.SCOPE_OWN)
        self.login_at(self.provider)

        response = self.client.get(reverse(
            'organization:membership_detail',
            kwargs={'pk': self.membership.pk},
        ))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['grants']), [grant])
        self.assertContains(response, self.role.name)
        self.assertContains(response, 'Principal tenant')

    def test_outside_access_panel_uses_canonical_report(self):
        self.direct_grant(RoleGrantScope.SCOPE_TENANT, tenant=self.customer)
        self.login_at(self.customer)

        response = self.client.get(
            reverse('organization:membership_list'),
            {'panel': 'outside_access'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.tech.username)
        self.assertContains(response, 'Managed by')

    def test_outside_access_panel_is_empty_without_external_grants(self):
        self.login_at(self.customer)

        response = self.client.get(
            reverse('organization:membership_list'),
            {'panel': 'outside_access'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'')
