"""Phase 5 tests: template-sync action + per-tenant access report (testable cores)."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from core.managers import set_current_tenant, set_current_membership
from organization.access import tenant_access_report
from organization.models import (
    Tenant, TenantRole, TenantMembership, Provider, ProviderRole, ProviderRoleTemplate,
)
from users.models import UserGroup, ProviderMembership

User = get_user_model()


class TemplateSyncTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.provider = Provider.objects.create(name="MSP")
        self.t1 = Tenant.objects.create(name="C1", slug="c1", provider=self.provider)
        self.t2 = Tenant.objects.create(name="C2", slug="c2", provider=self.provider)
        self.other = Tenant.objects.create(name="Other", slug="other")  # no provider
        self.tmpl = ProviderRoleTemplate.objects.create(
            provider=self.provider, name="Technician",
            permissions=["assets.view_asset", "assets.change_asset"],
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_sync_creates_roles_in_provider_tenants(self):
        # t1 and t2 already have a "Technician" role auto-created by the Phase 2 signal?
        # No — the signal only fires for is_default templates; this one is not default.
        created, updated = self.tmpl.sync_to_tenant_roles()
        self.assertEqual(created, 2)  # t1, t2
        self.assertEqual(updated, 0)
        for t in (self.t1, self.t2):
            role = TenantRole._base_manager.get(tenant=t, name="Technician")
            self.assertEqual(set(role.permissions), {"assets.view_asset", "assets.change_asset"})
        # Non-provider tenant is never touched.
        self.assertFalse(TenantRole._base_manager.filter(tenant=self.other, name="Technician").exists())

    def test_sync_updates_existing_role_permissions(self):
        TenantRole._base_manager.create(tenant=self.t1, name="Technician", permissions=["assets.view_asset"])
        created, updated = self.tmpl.sync_to_tenant_roles()
        self.assertEqual(created, 1)   # t2 created
        self.assertEqual(updated, 1)   # t1 updated
        role = TenantRole._base_manager.get(tenant=self.t1, name="Technician")
        self.assertEqual(set(role.permissions), {"assets.view_asset", "assets.change_asset"})

    def test_sync_to_selected_tenants(self):
        created, updated = self.tmpl.sync_to_tenant_roles(tenants=[self.t1])
        self.assertEqual(created, 1)
        self.assertFalse(TenantRole._base_manager.filter(tenant=self.t2, name="Technician").exists())


class TenantAccessReportTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)
        self.provider = Provider.objects.create(name="MSP")
        self.tenant = Tenant.objects.create(name="Cust", slug="cust", provider=self.provider)
        # membership user
        self.u_mem = User.objects.create_user(username="mem", email="m@e.com", password="pw")
        m = TenantMembership.objects.create(user=self.u_mem, tenant=self.tenant, is_active=True)
        m.roles.set([TenantRole.objects.create(tenant=self.tenant, name="Viewer", permissions=["assets.view_asset"])])
        # group user
        self.u_grp = User.objects.create_user(username="grp", email="g@e.com", password="pw")
        grp_role = TenantRole.objects.create(tenant=self.tenant, name="GroupRole", permissions=["assets.change_asset"])
        g = UserGroup.objects.create(name="Team", is_active=True)
        g.roles.set([grp_role])
        g.members.set([self.u_grp])
        # provider user
        self.u_prov = User.objects.create_user(username="prov", email="p@e.com", password="pw")
        tmpl = ProviderRoleTemplate.objects.create(provider=self.provider, name="T", permissions=["assets.delete_asset"])
        role = ProviderRole.objects.create(provider=self.provider, name="Admin", tenant_role_template=tmpl)
        pm = ProviderMembership.objects.create(
            user=self.u_prov, provider=self.provider, provider_role=role,
            tenant_scope=ProviderMembership.SCOPE_ALL,
        )

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)

    def test_report_lists_all_sources(self):
        report = tenant_access_report(self.tenant)
        by_user = {r['user'].username: r for r in report}
        self.assertEqual(set(by_user), {"mem", "grp", "prov"})
        self.assertEqual(by_user["mem"]["sources"], ["membership"])
        self.assertEqual(by_user["grp"]["sources"], ["group"])
        self.assertEqual(by_user["prov"]["sources"], ["provider"])
        self.assertIn("assets.view_asset", by_user["mem"]["permissions"])
        self.assertIn("assets.change_asset", by_user["grp"]["permissions"])
        self.assertIn("assets.delete_asset", by_user["prov"]["permissions"])
