from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model

from core.schema import schema
from software.models import Software
from assets.models import Manufacturer
from organization.models import Tenant, TenantRole, TenantMembership
from itambox.middleware import set_current_tenant


class SoftwareGraphQLTenantPinningTestCase(TestCase):
    """WS3-N1: GraphQL Software mutations must pin the active tenant so a tenant member
    cannot mint a globally-visible (tenant=None) catalogue row reachable by every tenant."""

    def setUp(self):
        self.User = get_user_model()
        self.user = self.User.objects.create_user(
            username='swuser', email='sw@example.com', password='pw'
        )
        self.tenant = Tenant.objects.create(name='Tenant A', slug='tenant-a-sw')
        self.other_tenant = Tenant.objects.create(name='Tenant B', slug='tenant-b-sw')
        role = TenantRole.objects.create(
            tenant=self.tenant,
            name='SW Role',
            permissions=[
                'software.view_software', 'software.add_software',
                'software.change_software', 'software.delete_software',
            ],
        )
        membership = TenantMembership.objects.create(user=self.user, tenant=self.tenant)
        membership.roles.add(role)
        set_current_tenant(self.tenant)
        self.manufacturer = Manufacturer.objects.create(name='Acme', slug='acme-sw')
        self.factory = RequestFactory()

    def tearDown(self):
        super().tearDown()
        set_current_tenant(None)

    def _context(self, active_tenant):
        request = self.factory.post('/graphql')
        request.user = self.User.objects.get(pk=self.user.pk)
        request.active_tenant = active_tenant
        return request

    def test_create_software_pins_active_tenant(self):
        """A tenant member's createSoftware persists tenant=active_tenant (not a global row).
        Fails before the fix (tenant was never set → tenant=None)."""
        query = """
        mutation($mid: ID!) {
            createSoftware(name: "Acme Tool", manufacturerId: $mid) {
                software { id name }
            }
        }
        """
        context = self._context(self.tenant)
        result = schema.execute(
            query, variable_values={'mid': str(self.manufacturer.id)}, context_value=context
        )
        self.assertIsNone(result.errors, msg=str(result.errors))
        sw = Software.objects.get(name='Acme Tool')
        self.assertEqual(sw.tenant_id, self.tenant.id)

    def test_create_software_global_denied_for_non_superuser(self):
        """A non-superuser with no resolvable active tenant must NOT create a global row."""
        query = """
        mutation($mid: ID!) {
            createSoftware(name: "Global Tool", manufacturerId: $mid) {
                software { id }
            }
        }
        """
        set_current_tenant(None)
        context = self._context(None)
        result = schema.execute(
            query, variable_values={'mid': str(self.manufacturer.id)}, context_value=context
        )
        self.assertIsNotNone(result.errors)
        self.assertFalse(Software.objects.filter(name='Global Tool').exists())
