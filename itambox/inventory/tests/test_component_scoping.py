from django.test import TestCase
from assets.models import Manufacturer, Category
from organization.models import Tenant, TenantGroup
from inventory.models import Component

class ComponentTenantScopingTests(TestCase):
    def setUp(self):
        self.tenant_a = Tenant.objects.create(name="Tenant A", slug="tenant-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="tenant-b")
        
        self.manufacturer = Manufacturer.objects.create(name='Samsung', slug='samsung')
        self.category = Category.objects.create(name='Storage', slug='storage', applies_to={'component': True})

        # Components
        self.comp_a = Component.objects.create(
            name="Component A", manufacturer=self.manufacturer, category=self.category, tenant=self.tenant_a
        )
        self.comp_b = Component.objects.create(
            name="Component B", manufacturer=self.manufacturer, category=self.category, tenant=self.tenant_b
        )
        self.comp_global = Component.objects.create(
            name="Component Global", manufacturer=self.manufacturer, category=self.category, tenant=None
        )

    def tearDown(self):
        from core.managers import set_current_tenant
        set_current_tenant(None)

    def test_tenant_a_scoping(self):
        from core.managers import set_current_tenant
        set_current_tenant(self.tenant_a)

        components = list(Component.objects.all())
        self.assertIn(self.comp_a, components)
        self.assertIn(self.comp_global, components)
        self.assertNotIn(self.comp_b, components)

    def test_tenant_b_scoping(self):
        from core.managers import set_current_tenant
        set_current_tenant(self.tenant_b)

        components = list(Component.objects.all())
        self.assertIn(self.comp_b, components)
        self.assertIn(self.comp_global, components)
        self.assertNotIn(self.comp_a, components)

    def test_no_tenant_scoping(self):
        from core.managers import set_current_tenant
        set_current_tenant(None)

        components = list(Component.objects.all())
        self.assertIn(self.comp_a, components)
        self.assertIn(self.comp_b, components)
        self.assertIn(self.comp_global, components)

    def test_tenant_group_sharing(self):
        # Create a TenantGroup
        group = TenantGroup.objects.create(name="Shared Group", slug="shared-group")
        
        # Associate Tenant A and a new Tenant C with the TenantGroup
        self.tenant_a.group = group
        self.tenant_a.save()
        
        tenant_c = Tenant.objects.create(name="Tenant C", slug="tenant-c", group=group)
        
        # Create a component for Tenant C
        comp_c = Component.objects.create(
            name="Component C", manufacturer=self.manufacturer, category=self.category, tenant=tenant_c
        )

        from core.managers import set_current_tenant, set_current_tenant_group
        
        # 1. Under Tenant A context (strict isolation):
        set_current_tenant(self.tenant_a)
        set_current_tenant_group(None)
        
        components = list(Component.objects.all())
        self.assertIn(self.comp_a, components)
        self.assertIn(self.comp_global, components)
        self.assertNotIn(comp_c, components)
        self.assertNotIn(self.comp_b, components)

        # 2. Under Tenant Group context (group aggregation):
        set_current_tenant(None)
        set_current_tenant_group(group)

        # The Group should be able to see Component A, Component Global, and Component C, but NOT Component B
        components = list(Component.objects.all())
        self.assertIn(self.comp_a, components)
        self.assertIn(self.comp_global, components)
        self.assertIn(comp_c, components)
        self.assertNotIn(self.comp_b, components)

        # Clean up context
        set_current_tenant(None)
        set_current_tenant_group(None)
