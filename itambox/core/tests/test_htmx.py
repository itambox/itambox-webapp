from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from organization.models import Tenant, Role, Membership

User = get_user_model()

class HTMXViewsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='htmxuser', password='password123')
        self.tenant = Tenant.objects.create(name='HTMX Tenant', slug='htmx-tenant')
        self.role = Role.objects.create(
            tenant=self.tenant,
            name='Admin',
            permissions=[
                'organization.view_tenant',
                'assets.view_manufacturer',
            ]
        )
        self.membership = Membership.objects.create(person_type=Membership.PERSON_MEMBER, user=self.user,
            tenant=self.tenant,
        )
        self.membership.roles.add(self.role)
        self.client.force_login(self.user)

    def test_non_htmx_request_returns_full_template(self):
        # Normal request should render full HTML
        response = self.client.get(reverse('assets:manufacturer_list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<html')
        self.assertContains(response, 'Manufacturers')

    def test_htmx_list_request_returns_partial_template(self):
        # HTMX request without boosted/history restore should return only the partial wrapper
        headers = {
            'HTTP_HX_Request': 'true',
        }
        response = self.client.get(reverse('assets:manufacturer_list'), **headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<html')
        # Should render the partial list wrapper content
        self.assertContains(response, 'id="object-list-table-container"')

    def test_htmx_detail_tab_request_returns_tab_partial(self):
        # Requesting a tab via HTMX on Tenant detail view should render that tab's partial content
        url = reverse('organization:tenant_detail', kwargs={'pk': self.tenant.pk})
        headers = {
            'HTTP_HX_Request': 'true',
        }
        response = self.client.get(url + '?tab=locations', **headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<html')
        # The locations tab lists locations for this tenant
        self.assertContains(response, 'Locations')
