"""GraphQL is read-only for the inventory domain (#612).

The Update*/Delete* catalogue mutations (and their global-row guards) are removed; writes are REST-only.
A mutation document must be rejected and must leave global catalogue rows untouched.
"""

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from assets.models import Manufacturer
from core.managers import set_current_membership, set_current_tenant, set_current_tenant_group
from core.schema import schema
from core.tests.mixins import grant
from inventory.models import Accessory, Kit
from itambox.middleware import _current_user
from organization.models import Role, Tenant, TenantGroup


class GraphQLInventoryMutationsRemovedTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="member", email="member@x.com", password="pw")
        self.superuser = User.objects.create_superuser(username="root", email="root@x.com", password="pw")

        self.group = TenantGroup.objects.create(name="Grp", slug="grp")
        self.tenant = Tenant.objects.create(name="T1", slug="t1", group=self.group)

        role = Role.objects.create(
            tenant=self.tenant,
            name="CatMgr",
            permissions=[
                "inventory.view_kit",
                "inventory.add_kit",
                "inventory.change_kit",
                "inventory.delete_kit",
                "inventory.view_accessory",
                "inventory.add_accessory",
                "inventory.change_accessory",
                "inventory.delete_accessory",
            ],
        )
        self.membership = grant(self.user, self.tenant, role).membership

        set_current_tenant(self.tenant)
        self.global_kit = Kit.objects.create(name="Global Kit", tenant=None)
        self.global_mfr = Manufacturer.objects.create(name="GlobalMfr")
        self.global_accessory = Accessory.objects.create(
            name="Global Accessory",
            manufacturer=self.global_mfr,
            tenant=None,
        )
        set_current_tenant(None)

        self.factory = RequestFactory()

    def tearDown(self):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        _current_user.set(None)

    def _ctx(self, user):
        _current_user.set(user)
        set_current_tenant(None)
        set_current_tenant_group(self.group)
        set_current_membership(self.membership)
        request = self.factory.post("/graphql")
        request.user = user
        request.active_tenant = None
        return request

    def test_every_inventory_mutation_document_is_rejected(self):
        documents = (
            'mutation { createAccessory(name: "X") { accessory { id } } }',
            f'mutation {{ updateAccessory(id: {self.global_accessory.pk}, name: "Hacked") {{ accessory {{ id }} }} }}',
            f"mutation {{ deleteAccessory(id: {self.global_accessory.pk}) {{ success }} }}",
            'mutation { createConsumable(name: "X") { consumable { id } } }',
            'mutation { createComponent(name: "X") { component { id } } }',
            'mutation { createKit(name: "X") { kit { id } } }',
            f'mutation {{ updateKit(id: {self.global_kit.pk}, name: "Hacked") {{ kit {{ id }} }} }}',
            f"mutation {{ deleteKit(id: {self.global_kit.pk}) {{ success }} }}",
        )
        for user in (self.user, self.superuser):
            ctx = self._ctx(user)
            for document in documents:
                result = schema.execute(document, context_value=ctx)
                self.assertIsNotNone(result.errors, document)
        self.assertEqual(Kit.objects.filter(name="Global Kit").count(), 1)
        self.assertEqual(Accessory.objects.filter(name="Global Accessory").count(), 1)
