"""Shared provider role details remain tenant-safe after RoleGrant cutover."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.managers import set_current_membership, set_current_tenant
from core.tests.mixins import grant
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant


User = get_user_model()


class SharedRoleDetailAuthzTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.provider = Tenant.objects.create(
            name='Detail Provider', slug='detail-provider', is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name='Detail Customer', slug='detail-customer', managed_by=self.provider,
        )
        self.sibling = Tenant.objects.create(
            name='Detail Sibling', slug='detail-sibling', managed_by=self.provider,
        )
        self.shared_role = Role.objects.create(
            tenant=self.provider,
            name='Shared reader',
            shared_with_managed=True,
            permissions=['assets.view_asset'],
        )
        self.private_role = Role.objects.create(
            tenant=self.provider,
            name='Provider private',
            permissions=[],
        )
        self.customer_admin = User.objects.create_user(
            username='detail-customer-admin', password='pw',
        )
        customer_admin_role = Role.objects.create(
            tenant=self.customer,
            name='Customer role auditor',
            permissions=['organization.view_role'],
        )
        grant(self.customer_admin, self.customer, customer_admin_role)
        self.provider_admin = User.objects.create_user(
            username='detail-provider-admin', password='pw',
        )
        provider_admin_role = Role.objects.create(
            tenant=self.provider,
            name='Provider role administrator',
            permissions=[
                'organization.view_role',
                'organization.change_role',
                'organization.add_membership',
                'organization.change_membership',
            ],
        )
        grant(self.provider_admin, self.provider, provider_admin_role)
        self.superuser = User.objects.create_superuser(
            username='detail-root', email='detail-root@example.com', password='pw',
        )
        for index in range(2):
            grant(
                User.objects.create_user(username=f'detail-customer-member-{index}'),
                self.customer,
                self.shared_role,
            )
        grant(
            User.objects.create_user(username='detail-sibling-member'),
            self.sibling,
            self.shared_role,
        )
        grant(
            User.objects.create_user(username='detail-provider-member'),
            self.provider,
            self.shared_role,
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def login_at(self, user, tenant):
        self.client.force_login(user)
        session = self.client.session
        session['active_tenant_id'] = tenant.pk
        session.save()
        membership = Membership.objects.filter(user=user, tenant=tenant).first()
        set_current_tenant(tenant)
        set_current_membership(membership)

    @staticmethod
    def detail_url(role):
        return reverse('organization:role_detail', kwargs={'pk': role.pk})

    def test_customer_admin_reads_shared_role_as_read_only(self):
        self.login_at(self.customer_admin, self.customer)

        response = self.client.get(self.detail_url(self.shared_role))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['shared_in_role'])
        self.assertFalse(response.context['role_editable'])
        self.assertFalse(response.context['can_change'])
        self.assertFalse(response.context['can_delete'])
        self.assertNotContains(response, 'Assign Users')

    def test_customer_admin_cannot_open_private_provider_role(self):
        self.login_at(self.customer_admin, self.customer)

        response = self.client.get(self.detail_url(self.private_role))

        self.assertEqual(response.status_code, 404)

    def test_shared_role_member_count_is_scoped_to_active_customer(self):
        self.login_at(self.customer_admin, self.customer)

        response = self.client.get(self.detail_url(self.shared_role))

        expected = RoleGrant.objects.filter(
            role=self.shared_role,
            membership__tenant=self.customer,
            scopes__scope_type=RoleGrantScope.SCOPE_OWN,
        ).values('membership_id').distinct().count()
        self.assertEqual(expected, 2)
        self.assertEqual(response.context['member_count'], expected)

    def test_provider_owner_sees_provider_scoped_count_and_controls(self):
        self.login_at(self.provider_admin, self.provider)

        response = self.client.get(self.detail_url(self.shared_role))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['shared_in_role'])
        self.assertTrue(response.context['role_editable'])
        self.assertEqual(response.context['member_count'], 1)
        self.assertContains(response, 'Assign Users')

    def test_superuser_global_count_includes_each_tenant(self):
        self.client.force_login(self.superuser)

        response = self.client.get(self.detail_url(self.shared_role))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['member_count'], 4)
