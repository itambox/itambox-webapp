"""Guard: a non-superuser must not modify/delete a GLOBAL (tenant=None) catalogue row
via the GraphQL Update*/Delete* mutations.

In a tenant-GROUP context the active tenant is None, so ``get_object_or_denied`` skips its
``tenant=`` filter and the ``allow_global_tenant`` scoping manager surfaces tenant=None
rows to a non-superuser group member. The per-mutation guard (mirroring the global-create
guard) blocks the write; superusers are unaffected. Companion to the WS3-N1 create guards.
"""
from django.test import TestCase, RequestFactory
from django.contrib.auth import get_user_model

from core.schema import schema
from core.managers import set_current_tenant, set_current_tenant_group, set_current_membership
from itambox.middleware import _current_user
from organization.models import Tenant, TenantGroup, TenantRole, TenantMembership
from inventory.models import Kit, Accessory
from assets.models import Manufacturer


class GraphQLGlobalCatalogueGuardTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username='member', email='member@x.com', password='pw')
        self.superuser = User.objects.create_superuser(username='root', email='root@x.com', password='pw')

        self.group = TenantGroup.objects.create(name='Grp', slug='grp')
        self.tenant = Tenant.objects.create(name='T1', slug='t1', group=self.group)

        role = TenantRole.objects.create(
            tenant=self.tenant, name='CatMgr',
            permissions=[
                'inventory.view_kit', 'inventory.change_kit', 'inventory.delete_kit',
                'inventory.view_accessory', 'inventory.change_accessory', 'inventory.delete_accessory',
            ],
        )
        self.membership = TenantMembership.objects.create(user=self.user, tenant=self.tenant, role=role)

        # Create the global (tenant=None) rows the attacker would target.
        set_current_tenant(self.tenant)
        self.global_kit = Kit.objects.create(name='Global Kit', tenant=None)
        self.global_mfr = Manufacturer.objects.create(name='GlobalMfr')  # Manufacturer has no tenant field
        self.global_accessory = Accessory.objects.create(
            name='Global Accessory', manufacturer=self.global_mfr, tenant=None,
        )
        set_current_tenant(None)

        self.factory = RequestFactory()

    def tearDown(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        _current_user.set(None)
        super().tearDown()

    def _group_context(self, user, membership):
        """A tenant-GROUP scope: active_tenant=None (so get_object_or_denied skips its
        tenant filter) while the group + current user make the scoping manager surface the
        global rows — the exact cross-scope condition a real group-scoped request hits."""
        set_current_tenant(None)
        set_current_tenant_group(self.group)
        set_current_membership(membership)
        _current_user.set(user)
        request = self.factory.post('/graphql')
        request.user = get_user_model().objects.get(pk=user.pk)
        request.active_tenant = None
        request.active_tenant_group = self.group
        return request

    def test_update_global_kit_denied_for_standard_user(self):
        ctx = self._group_context(self.user, self.membership)
        mutation = 'mutation { updateKit(id: "%s", name: "Hacked") { kit { id } } }' % self.global_kit.id
        result = schema.execute(mutation, context_value=ctx)
        self.assertIsNotNone(result.errors)
        self.assertIn("Only superusers can modify global kits.", result.errors[0].message)
        self.global_kit.refresh_from_db()
        self.assertEqual(self.global_kit.name, 'Global Kit')  # unchanged

    def test_delete_global_kit_denied_for_standard_user(self):
        ctx = self._group_context(self.user, self.membership)
        mutation = 'mutation { deleteKit(id: "%s") { success } }' % self.global_kit.id
        result = schema.execute(mutation, context_value=ctx)
        self.assertIsNotNone(result.errors)
        self.assertIn("Only superusers can delete global kits.", result.errors[0].message)
        self.assertTrue(
            Kit.all_objects.filter(pk=self.global_kit.pk, deleted_at__isnull=True).exists()
        )

    def test_update_global_accessory_denied_for_standard_user(self):
        # Same guard on an AbstractInventoryItem model (inherited allow_global_tenant).
        ctx = self._group_context(self.user, self.membership)
        mutation = 'mutation { updateAccessory(id: "%s", name: "Hacked") { accessory { id } } }' % self.global_accessory.id
        result = schema.execute(mutation, context_value=ctx)
        self.assertIsNotNone(result.errors)
        self.assertIn("Only superusers can modify global accessories.", result.errors[0].message)

    def test_superuser_can_modify_global_kit(self):
        # The guard exempts superusers — the global catalogue stays editable by them.
        ctx = self._group_context(self.superuser, None)
        mutation = 'mutation { updateKit(id: "%s", name: "Renamed") { kit { id name } } }' % self.global_kit.id
        result = schema.execute(mutation, context_value=ctx)
        self.assertIsNone(result.errors)
        self.global_kit.refresh_from_db()
        self.assertEqual(self.global_kit.name, 'Renamed')
