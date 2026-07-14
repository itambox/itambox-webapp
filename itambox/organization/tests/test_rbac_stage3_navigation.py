"""Stage 3 navigation and managed-tenant workflow regressions."""
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from core.tests.mixins import TenantTestMixin
from itambox.context_processors import tenant_switcher_processor
from organization.forms import TenantForm
from organization.models import Membership, Role, RoleAssignment, Tenant, TenantGroup

User = get_user_model()


class ManagedTenantsTabTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.clear_tenant_context()
        self.provider = Tenant.objects.create(
            name='Provider Tab', slug='provider-tab', is_provider=True,
        )
        self.group = TenantGroup.objects.create(name='Managed Group', slug='managed-group-tab')
        self.customer = Tenant.objects.create(
            name='Customer Tab', slug='customer-tab', managed_by=self.provider,
            group=self.group,
        )
        self.member = User.objects.create_user(
            username='customer_tab_member', email='customer_tab_member@example.com',
        )
        Membership.objects.create(user=self.member, tenant=self.customer, is_active=True)
        self.superuser = User.objects.create_superuser(
            username='managed_tab_root', email='managed_tab_root@example.com', password='pw',
        )
        self.client.force_login(self.superuser)

    def tearDown(self):
        self.clear_tenant_context()

    def test_provider_tab_lists_managed_tenants_and_add_link(self):
        url = reverse(
            'organization:tenant_managed_tenants_tab', kwargs={'pk': self.provider.pk},
        ) + f'?switch_tenant={self.provider.pk}'

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.customer.name)
        self.assertContains(response, self.group.name)
        self.assertContains(response, '?managed_by=%s' % self.provider.pk)
        managed = list(response.context['managed_tenants'])
        self.assertEqual(managed[0].member_count, 1)

    def test_non_provider_has_no_managed_tenants_partial(self):
        url = reverse(
            'organization:tenant_managed_tenants_tab', kwargs={'pk': self.customer.pk},
        ) + f'?switch_tenant={self.customer.pk}'

        response = self.client.get(url)

        self.assertEqual(response.status_code, 404)


class ForcedManagedByFormTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.clear_tenant_context()
        self.provider = Tenant.objects.create(
            name='Forced Provider', slug='forced-provider', is_provider=True,
        )
        self.other_provider = Tenant.objects.create(
            name='Other Provider', slug='other-provider', is_provider=True,
        )
        self.actor = User.objects.create_user(
            username='provider_tenant_creator',
            email='provider_tenant_creator@example.com',
            password='pw',
        )
        role = Role.objects.create(
            tenant=self.provider,
            name='Tenant Creator',
            permissions=['organization.add_tenant'],
        )
        self.grant(self.actor, self.provider, role)

    def tearDown(self):
        self.clear_tenant_context()

    def test_authorized_provider_admin_gets_server_forced_managed_by(self):
        form = TenantForm(
            data={
                'name': 'New Customer',
                'slug': 'new-customer-forced',
                'currency': 'EUR',
                'managed_by': self.other_provider.pk,
            },
            user=self.actor,
            managed_by_param=str(self.provider.pk),
        )

        self.assertNotIn('managed_by', form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        tenant = form.save()
        self.assertEqual(tenant.managed_by_id, self.provider.pk)

    def test_superuser_gets_editable_prefilled_managed_by(self):
        superuser = User.objects.create_superuser(
            username='managed_by_root', email='managed_by_root@example.com', password='pw',
        )

        form = TenantForm(
            user=superuser,
            managed_by_param=str(self.provider.pk),
        )

        self.assertIn('managed_by', form.fields)
        self.assertEqual(form.fields['managed_by'].initial, self.provider.pk)


class TenantSwitcherGroupingTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.clear_tenant_context()
        self.user = User.objects.create_user(
            username='switcher_stage3', email='switcher_stage3@example.com',
        )
        self.provider = Tenant.objects.create(
            name='Zeta Provider', slug='zeta-provider-switcher', is_provider=True,
        )
        self.direct = Tenant.objects.create(
            name='Alpha Direct', slug='alpha-direct-switcher',
        )
        self.group = TenantGroup.objects.create(
            name='Customers', slug='customers-switcher',
        )
        self.managed = Tenant.objects.create(
            name='Managed Customer', slug='managed-customer-switcher',
            managed_by=self.provider, group=self.group,
        )
        role = Role.objects.create(
            tenant=self.provider, name='Managed Reach', permissions=[],
        )
        self.grant(self.user, self.provider, role)
        self.grant(
            self.user,
            self.provider,
            role,
            reach=RoleAssignment.REACH_MANAGED,
            managed_scope=RoleAssignment.SCOPE_ALL,
        )
        Membership.objects.create(user=self.user, tenant=self.direct, is_active=True)

    def tearDown(self):
        self.clear_tenant_context()

    def test_direct_tenants_are_separate_provider_first_and_managed_are_grouped(self):
        request = RequestFactory().get('/')
        request.user = self.user

        context = tenant_switcher_processor(request)
        own = list(context['own_tenants_switcher'])
        managed_groups = list(context['grouped_managed_tenants_switcher'])

        self.assertEqual([tenant.pk for tenant in own], [self.provider.pk, self.direct.pk])
        self.assertEqual(len(managed_groups), 1)
        self.assertEqual(managed_groups[0]['group'], self.group)
        self.assertEqual(
            [tenant.pk for tenant in managed_groups[0]['tenants']],
            [self.managed.pk],
        )
