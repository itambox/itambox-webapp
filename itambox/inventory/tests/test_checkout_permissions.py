"""B3 regression: inventory checkout/checkin views require a permission.

KitCheckoutView, Accessory/Consumable/Component checkout, and the accessory /
component check-in views previously declared no permission_required, so any
authenticated tenant member could mutate stock and assignments. They now require
the matching `inventory.change_<model>` permission, and the base service view
fails closed when permission_required is unset.

The permission gate runs at dispatch time (before any HTTP-method handler), so a
denied member is rejected on GET and POST alike. The tests deliberately avoid
issuing a *successful* mutating POST: a checkout that actually runs would commit
inventory side effects that leak into later tests in the same process (a known
cross-test isolation limitation of this suite), so the positive case is asserted
with a non-mutating GET.
"""
from django.test import TestCase
from django.urls import reverse
from model_bakery import baker

from organization.models import AssetHolder
from inventory.models import (
    Accessory, Consumable, Component, Kit,
    AccessoryAssignment, ComponentAllocation,
)
from core.tests.mixins import TenantTestMixin

# Import the view modules at collection time (no tenant context active) so their
# `queryset = Model.objects.all()` class attributes bake UNSCOPED. Otherwise the
# first reverse() in another test that runs under a tenant context would trigger
# the URLconf to import these views with that tenant active, freezing the
# querysets to the wrong tenant and 404-ing every object here. (Harmless in
# production, where the URLconf loads at startup with no tenant.)
import inventory.views  # noqa: F401,E402


class InventoryCheckoutPermissionTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name='Inv Tenant', slug='inv-tenant')
        self.set_active_tenant(self.tenant)
        self.holder = baker.make(AssetHolder, tenant=self.tenant)
        self.accessory = baker.make(Accessory, tenant=self.tenant)
        self.consumable = baker.make(Consumable, tenant=self.tenant)
        self.component = baker.make(Component, tenant=self.tenant)
        self.kit = baker.make(Kit, tenant=self.tenant)
        self.acc_assignment = baker.make(
            AccessoryAssignment, accessory=self.accessory, assigned_holder=self.holder, qty=1
        )
        self.comp_allocation = baker.make(
            ComponentAllocation, component=self.component, assigned_holder=self.holder, qty=1
        )

        # (url name, pk, required permission)
        self.endpoints = [
            ('inventory:accessory_checkout', self.accessory.pk, 'inventory.change_accessory'),
            ('inventory:accessory_checkin', self.acc_assignment.pk, 'inventory.change_accessory'),
            ('inventory:consumable_checkout', self.consumable.pk, 'inventory.change_consumable'),
            ('inventory:component_checkout', self.component.pk, 'inventory.change_component'),
            ('inventory:component_checkin', self.comp_allocation.pk, 'inventory.change_component'),
            ('inventory:kit_checkout_modal', self.kit.pk, 'inventory.change_kit'),
        ]

    def _url(self, name, pk):
        # Drive the request's active tenant deterministically through the
        # middleware's supported ?switch_tenant= param rather than relying on the
        # test client's session persistence (which is fragile across this suite's
        # test ordering). The member only has a membership in self.tenant, so the
        # middleware validates and selects it.
        return f"{reverse(name, kwargs={'pk': pk})}?switch_tenant={self.tenant.pk}"

    def test_member_without_permission_is_denied(self):
        # tenant_user's role was created with no permissions. A denied POST is
        # rejected at the permission gate before any service runs (no mutation).
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        for name, pk, perm in self.endpoints:
            with self.subTest(endpoint=name):
                url = self._url(name, pk)
                resp = self.client.post(url, {})
                self.assertIn(
                    resp.status_code, (302, 403),
                    f"{name} should deny a member lacking {perm} (got {resp.status_code})",
                )

    def test_member_with_permission_passes_the_gate(self):
        # Granting the permission must let the request past the gate. Asserted with
        # a non-mutating GET so no inventory side effects leak into later tests.
        self.tenant_role.permissions = [
            'inventory.change_accessory', 'inventory.change_consumable',
            'inventory.change_component', 'inventory.change_kit',
        ]
        self.tenant_role.save()
        self.client_login_to_tenant(self.tenant_user, self.tenant)
        for name, pk, perm in self.endpoints:
            with self.subTest(endpoint=name):
                url = self._url(name, pk)
                resp = self.client.get(url)
                # Past the gate the view answers 200 (checkout form), 405 (checkin
                # has no GET handler) or similar — never a 403 permission denial.
                self.assertNotEqual(
                    resp.status_code, 403,
                    f"{name} should admit a member holding {perm}",
                )
