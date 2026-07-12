"""Cross-tenant inventory flows under ADR-0001 phase 4.

End-to-end through ``checkout_inventory_item``: pool ownership from the
location, grant-gated cross-tenant checkouts, provenance recording, stock
bookkeeping on the owner's pool, and the same-tenant fast path staying
untouched.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from assets.models import Manufacturer
from core.tests.mixins import TenantTestMixin, grant
from inventory.models import Accessory, AccessoryAssignment, AccessoryStock
from inventory.services import checkout_inventory_item
from organization.models import (
    AssetHolder, Location, Role, Site, Tenant, TenantResourceGrant,
)

User = get_user_model()

PERM = 'inventory.add_accessoryassignment'


def _grant_use(owner, grantee, stock):
    from django.contrib.contenttypes.models import ContentType
    return TenantResourceGrant.objects.create(
        tenant=owner,
        grantee_tenant=grantee,
        resource_type=ContentType.objects.get_for_model(AccessoryStock),
        resource_id=stock.pk,
        access_level=TenantResourceGrant.ACCESS_USE,
    )


class CrossTenantCheckoutTests(TenantTestMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = Tenant.objects.create(name='XT Owner', slug='xt-owner', is_provider=True)
        cls.grantee = Tenant.objects.create(
            name='XT Grantee', slug='xt-grantee', managed_by=cls.owner,
        )
        owner_site = Site.objects.create(name='XT OSite', slug='xt-osite', tenant=cls.owner)
        cls.owner_location = Location.objects.create(
            name='XT Depot', slug='xt-depot', site=owner_site, tenant=cls.owner,
        )
        grantee_site = Site.objects.create(name='XT GSite', slug='xt-gsite', tenant=cls.grantee)
        cls.grantee_location = Location.objects.create(
            name='XT Office', slug='xt-office', site=grantee_site, tenant=cls.grantee,
        )
        mfr = Manufacturer.objects.create(name='XT Mfg', slug='xt-mfg')
        cls.accessory = Accessory.objects.create(
            name='XT Dock', slug='xt-dock', manufacturer=mfr, tenant=cls.owner,
        )
        cls.stock = AccessoryStock.objects.create(
            accessory=cls.accessory, location=cls.owner_location, qty=10,
        )
        cls.holder = AssetHolder.objects.create(
            first_name='Gran', last_name='Tee', upn='gran.tee@xt',
            tenant=cls.grantee,
        )
        cls.tech = User.objects.create_user(username='xt-tech', password='x')
        role = Role.objects.create(tenant=cls.grantee, name='XT Tech', permissions=[PERM])
        grant(cls.tech, cls.grantee, role)

    def test_stock_tenant_derived_from_location(self):
        assert self.stock.tenant_id == self.owner.pk
        moved = AccessoryStock.objects.create(
            accessory=self.accessory, location=self.grantee_location, qty=1,
        )
        assert moved.tenant_id == self.grantee.pk

    def test_stock_requires_owned_location(self):
        site = Site.objects.create(name='XT NoT', slug='xt-not-site')
        bare = Location.objects.create(name='XT Bare', slug='xt-bare', site=site)
        with self.assertRaises(ValidationError):
            AccessoryStock.objects.create(accessory=self.accessory, location=bare, qty=1)

    def test_cross_tenant_checkout_without_grant_denied(self):
        with self.tenant_context(self.grantee):
            with self.assertRaises(ValidationError):
                checkout_inventory_item(
                    self.accessory, 1, holder=self.holder,
                    source_location=self.owner_location, user=self.tech,
                )
        assert not AccessoryAssignment._base_manager.filter(
            accessory=self.accessory).exists()
        self.stock.refresh_from_db()
        assert self.stock.qty == 10

    def test_cross_tenant_checkout_with_grant_records_provenance(self):
        grant_row = _grant_use(self.owner, self.grantee, self.stock)
        with self.tenant_context(self.grantee):
            assignment = checkout_inventory_item(
                self.accessory, 2, holder=self.holder,
                source_location=self.owner_location, user=self.tech,
            )
        assert assignment.resource_grant_id == grant_row.pk
        assert assignment.source_tenant_id == self.owner.pk
        assert assignment.target_tenant_id == self.grantee.pk
        self.stock.refresh_from_db()
        assert self.stock.qty == 8  # the OWNER's pool was deducted

    def test_cross_tenant_checkout_view_grant_insufficient(self):
        grant_row = _grant_use(self.owner, self.grantee, self.stock)
        TenantResourceGrant._base_manager.filter(pk=grant_row.pk).update(
            access_level=TenantResourceGrant.ACCESS_VIEW,
        )
        with self.tenant_context(self.grantee):
            with self.assertRaises(ValidationError):
                checkout_inventory_item(
                    self.accessory, 1, holder=self.holder,
                    source_location=self.owner_location, user=self.tech,
                )

    def test_cross_tenant_checkout_requires_rbac_in_active_tenant(self):
        _grant_use(self.owner, self.grantee, self.stock)
        nobody = User.objects.create_user(username='xt-nobody', password='x')
        with self.tenant_context(self.grantee):
            with self.assertRaises(ValidationError):
                checkout_inventory_item(
                    self.accessory, 1, holder=self.holder,
                    source_location=self.owner_location, user=nobody,
                )

    def test_revoked_grant_blocks_new_checkout_but_keeps_history(self):
        grant_row = _grant_use(self.owner, self.grantee, self.stock)
        with self.tenant_context(self.grantee):
            assignment = checkout_inventory_item(
                self.accessory, 1, holder=self.holder,
                source_location=self.owner_location, user=self.tech,
            )
        grant_row.delete()  # revoke
        assignment.refresh_from_db()
        assert assignment.resource_grant_id == grant_row.pk  # history survives
        with self.tenant_context(self.grantee):
            with self.assertRaises(ValidationError):
                checkout_inventory_item(
                    self.accessory, 1, holder=self.holder,
                    source_location=self.owner_location, user=self.tech,
                )

    def test_same_tenant_checkout_carries_no_grant(self):
        owner_holder = AssetHolder.objects.create(
            first_name='Own', last_name='Er', upn='own.er@xt', tenant=self.owner,
        )
        with self.tenant_context(self.owner):
            assignment = checkout_inventory_item(
                self.accessory, 1, holder=owner_holder,
                source_location=self.owner_location,
            )
        assert assignment.resource_grant_id is None
        assert assignment.source_tenant_id == self.owner.pk
        assert assignment.target_tenant_id == self.owner.pk

    def test_direct_orm_cross_tenant_create_without_grant_denied(self):
        # The model-layer guard holds even when the service is bypassed.
        with self.assertRaises(ValidationError):
            AccessoryAssignment.objects.create(
                accessory=self.accessory,
                assigned_holder=self.holder,
                from_location=self.owner_location,
                qty=1,
            )
