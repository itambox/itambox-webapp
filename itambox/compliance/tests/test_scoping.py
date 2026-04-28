from django.test import TestCase
from organization.models import Tenant, TenantGroup
from compliance.models import CustodyTemplate

class CustodyTemplateTenantScopingTests(TestCase):
    def setUp(self):
        # Create TenantGroups
        self.group_x = TenantGroup.objects.create(name="Group X", slug="group-x")
        self.group_y = TenantGroup.objects.create(name="Group Y", slug="group-y")

        # Create Tenants
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a", group=self.group_x)
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b", group=self.group_y)
        self.tenant_c = Tenant.objects.create(name="Tenant C", slug="tenant-c", group=self.group_x)

        # Create Custody Templates
        self.tpl_tenant_a = CustodyTemplate.objects.create(
            name="Template Tenant A", tenant=self.tenant_a, eula_text="eula A"
        )
        self.tpl_tenant_b = CustodyTemplate.objects.create(
            name="Template Tenant B", tenant=self.tenant_b, eula_text="eula B"
        )
        self.tpl_group_x = CustodyTemplate.objects.create(
            name="Template Group X", tenant_group=self.group_x, eula_text="eula group x"
        )
        self.tpl_group_y = CustodyTemplate.objects.create(
            name="Template Group Y", tenant_group=self.group_y, eula_text="eula group y"
        )
        self.tpl_global = CustodyTemplate.objects.create(
            name="Template Global", tenant=None, tenant_group=None, eula_text="eula global"
        )

    def tearDown(self):
        from core.managers import set_current_tenant, set_current_tenant_group
        set_current_tenant(None)
        set_current_tenant_group(None)

    def test_tenant_a_scoping(self):
        from core.managers import set_current_tenant, set_current_tenant_group
        set_current_tenant(self.tenant_a)
        set_current_tenant_group(None)

        templates = list(CustodyTemplate.objects.all())
        # Tenant A should see:
        # - its own template (tpl_tenant_a)
        # - the group template for group X (since Tenant A is in group X)
        # - the global template
        # Tenant A should NOT see:
        # - Tenant B template
        # - Group Y template
        self.assertIn(self.tpl_tenant_a, templates)
        self.assertIn(self.tpl_group_x, templates)
        self.assertIn(self.tpl_global, templates)
        self.assertNotIn(self.tpl_tenant_b, templates)
        self.assertNotIn(self.tpl_group_y, templates)

    def test_tenant_group_x_scoping(self):
        from core.managers import set_current_tenant, set_current_tenant_group
        set_current_tenant(None)
        set_current_tenant_group(self.group_x)

        templates = list(CustodyTemplate.objects.all())
        # Group X should see:
        # - templates of member tenants (tpl_tenant_a)
        # - the group X template
        # - the global template
        # Group X should NOT see:
        # - Tenant B template (since B is in group Y)
        # - Group Y template
        self.assertIn(self.tpl_tenant_a, templates)
        self.assertIn(self.tpl_group_x, templates)
        self.assertIn(self.tpl_global, templates)
        self.assertNotIn(self.tpl_tenant_b, templates)
        self.assertNotIn(self.tpl_group_y, templates)

    def test_global_scoping_no_filter(self):
        from core.managers import set_current_tenant, set_current_tenant_group
        set_current_tenant(None)
        set_current_tenant_group(None)

        templates = list(CustodyTemplate.objects.all())
        # With no filters set, all templates should be visible
        self.assertEqual(len(templates), 5)
