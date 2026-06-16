"""Phase 1 cross-tenant boundary tests for the Software API (F6).

SoftwareViewSet previously set permission_classes=[TokenPermissions] only,
dropping the global StrictTenantPermission default and letting a tenant-A member
PATCH/DELETE a tenant-B Software. These tests assert:
  (a) the list returns only the requester's own-tenant rows plus shared/global
      (tenant=None) catalogue entries, and
  (b) a cross-tenant detail mutation (PATCH) is blocked (404/403).

Auth/fixture pattern mirrors core/tests/test_security_boundaries.py.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse

from organization.models import Tenant, TenantRole, TenantMembership
from assets.models import Manufacturer
from software.models import Software

User = get_user_model()


class SoftwareApiCrossTenantTestCase(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name='Tenant A', slug='tenant-a')
        self.tenant_b = Tenant.objects.create(name='Tenant B', slug='tenant-b')

        # Requesting user is a member of tenant B with software view+change perms.
        self.user_b = User.objects.create_user(username='user_b', password='password123')
        self.role_b = TenantRole.objects.create(
            tenant=self.tenant_b,
            name='Admin',
            permissions=['software.view_software', 'software.change_software'],
        )
        self.membership_b = TenantMembership.objects.create(
            user=self.user_b, tenant=self.tenant_b, role=self.role_b,
        )

        self.mfr = Manufacturer.objects.create(name='Microsoft', slug='microsoft')

        # tenant-A software (must be invisible / immutable to user_b)
        self.software_a = Software.objects.create(
            name='Visio (A)', manufacturer=self.mfr, tenant=self.tenant_a,
        )
        # tenant-B software (own tenant — visible)
        self.software_b = Software.objects.create(
            name='Word (B)', manufacturer=self.mfr, tenant=self.tenant_b,
        )
        # global/shared catalogue entry (tenant=None — visible to all tenants)
        self.software_global = Software.objects.create(
            name='Notepad (global)', manufacturer=self.mfr, tenant=None,
        )

    def _login_b(self):
        self.client.force_login(self.user_b)
        session = self.client.session
        session['active_tenant_id'] = self.tenant_b.pk
        session.save()

    def test_list_scopes_to_own_and_global(self):
        self._login_b()
        url = reverse('api:software_api:software-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        ids = {row['id'] for row in response.json()['results']}
        self.assertIn(self.software_b.pk, ids)        # own tenant
        self.assertIn(self.software_global.pk, ids)   # shared/global
        self.assertNotIn(self.software_a.pk, ids)     # other tenant excluded

    def test_cross_tenant_patch_blocked(self):
        self._login_b()
        url = reverse('api:software_api:software-detail', kwargs={'pk': self.software_a.pk})
        response = self.client.patch(
            url, data={'name': 'Hacked'}, content_type='application/json',
        )
        # StrictTenantPermission raises Http404 (and tenant-scoped queryset hides
        # the row); either way a cross-tenant write must not succeed.
        self.assertIn(response.status_code, (403, 404))

        self.software_a.refresh_from_db()
        self.assertEqual(self.software_a.name, 'Visio (A)')
