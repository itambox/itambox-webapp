"""Phase 2 tests for the Provider layer (models + auto-instantiation signal).

Phase 2 is purely additive: with no Provider rows the system behaves exactly as before.
These tests cover the new models, their constraints, the Tenant.provider FK, and the
post_save signal that instantiates default ProviderRoleTemplates as TenantRoles.
"""
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.managers import set_current_tenant, set_current_membership
from organization.models import (
    Tenant, TenantGroup, TenantRole, Provider, ProviderRole, ProviderRoleTemplate,
)
from users.models import ProviderMembership

User = get_user_model()


class _Ctx:
    def setUp(self):
        set_current_tenant(None)
        set_current_membership(None)

    def tearDown(self):
        set_current_tenant(None)
        set_current_membership(None)


class ProviderModelTests(_Ctx, TestCase):
    def test_provider_create_and_str(self):
        p = Provider.objects.create(name="Acme MSP")
        self.assertEqual(str(p), "Acme MSP")
        self.assertTrue(p.slug)  # AutoSlug populated

    def test_provider_unique_active_name(self):
        Provider.objects.create(name="Dup")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Provider.objects.create(name="Dup")

    def test_provider_is_global_manager(self):
        # Provider is not tenant-scoped: visible regardless of active tenant context.
        Provider.objects.create(name="GlobalP")
        t = Tenant.objects.create(name="T1", slug="t1")
        set_current_tenant(t)
        self.assertEqual(Provider.objects.filter(name="GlobalP").count(), 1)

    def test_provider_role_capabilities_default_false(self):
        p = Provider.objects.create(name="P")
        r = ProviderRole.objects.create(provider=p, name="Junior")
        self.assertFalse(r.can_manage_tenants)
        self.assertFalse(r.can_manage_provider_users)
        self.assertFalse(r.can_manage_groups)
        self.assertTrue(r.slug)


class TenantProviderFKTests(_Ctx, TestCase):
    def test_tenant_provider_nullable(self):
        t = Tenant.objects.create(name="NoProv", slug="noprov")
        self.assertIsNone(t.provider_id)

    def test_tenant_assigned_to_provider(self):
        p = Provider.objects.create(name="P2")
        t = Tenant.objects.create(name="WithProv", slug="withprov", provider=p)
        self.assertEqual(t.provider_id, p.pk)
        self.assertIn(t, p.tenants.all())


class DefaultTemplateInstantiationTests(_Ctx, TestCase):
    def test_default_templates_instantiated_on_provider_tenant_create(self):
        p = Provider.objects.create(name="P3")
        ProviderRoleTemplate.objects.create(
            provider=p, name="Technician", permissions=["assets.view_asset", "assets.change_asset"], is_default=True,
        )
        ProviderRoleTemplate.objects.create(
            provider=p, name="Auditor", permissions=["assets.view_asset"], is_default=False,
        )
        t = Tenant.objects.create(name="CustA", slug="custa", provider=p)

        roles = TenantRole._base_manager.filter(tenant=t)
        names = set(roles.values_list('name', flat=True))
        self.assertIn("Technician", names)        # default → instantiated
        self.assertNotIn("Auditor", names)        # non-default → not instantiated
        tech = roles.get(name="Technician")
        self.assertEqual(set(tech.permissions), {"assets.view_asset", "assets.change_asset"})

    def test_non_provider_tenant_gets_no_autoroles(self):
        # A tenant with no provider must not get any auto-created roles.
        t = Tenant.objects.create(name="Plain", slug="plain")
        self.assertEqual(TenantRole._base_manager.filter(tenant=t).count(), 0)

    def test_provider_with_no_default_templates_no_roles(self):
        p = Provider.objects.create(name="P4")
        ProviderRoleTemplate.objects.create(provider=p, name="OnlyOptional", is_default=False)
        t = Tenant.objects.create(name="CustB", slug="custb", provider=p)
        self.assertEqual(TenantRole._base_manager.filter(tenant=t).count(), 0)


class ProviderMembershipTests(_Ctx, TestCase):
    def setUp(self):
        super().setUp()
        self.p = Provider.objects.create(name="PM-P")
        self.u = User.objects.create_user(username="staff", email="s@e.com", password="pw")

    def test_membership_defaults(self):
        m = ProviderMembership.objects.create(user=self.u, provider=self.p)
        self.assertEqual(m.tenant_scope, ProviderMembership.SCOPE_EXPLICIT)  # least privilege
        self.assertTrue(m.is_active)
        self.assertIsNone(m.provider_role_id)

    def test_membership_unique_user_provider(self):
        ProviderMembership.objects.create(user=self.u, provider=self.p)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ProviderMembership.objects.create(user=self.u, provider=self.p)

    def test_membership_assigned_tenants_and_scope_group(self):
        g = TenantGroup.objects.create(name="EMEA", slug="emea")
        t = Tenant.objects.create(name="AssignT", slug="assignt", provider=self.p, group=g)
        m = ProviderMembership.objects.create(
            user=self.u, provider=self.p, tenant_scope=ProviderMembership.SCOPE_TENANT_GROUP, scope_group=g,
        )
        m.assigned_tenants.add(t)
        self.assertIn(t, m.assigned_tenants.all())
        self.assertEqual(m.scope_group_id, g.pk)
