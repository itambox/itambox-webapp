"""Cross-tenant inventory flows under ADR-0001 phase 4.

End-to-end through ``checkout_inventory_item``: pool ownership from the
location, grant-gated cross-tenant checkouts, provenance recording, stock
bookkeeping on the owner's pool, and the same-tenant fast path staying
untouched.
"""

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from assets.models import Manufacturer
from core.tasks.context import TaskContext
from core.tests.mixins import TenantTestMixin, grant
from inventory.models import Accessory, AccessoryAssignment, AccessoryStock
from inventory.services import checkout_inventory_item
from organization.models import (
    AssetHolder,
    Location,
    Role,
    Site,
    Tenant,
    TenantGroup,
    TenantResourceGrant,
)

User = get_user_model()

PERM = "inventory.add_accessoryassignment"


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
        cls.grantee_group = TenantGroup.objects.create(name="XT Grantee Group", slug="xt-grantee-group")
        cls.owner = Tenant.objects.create(name="XT Owner", slug="xt-owner", is_provider=True)
        cls.grantee = Tenant.objects.create(
            name="XT Grantee",
            slug="xt-grantee",
            managed_by=cls.owner,
            group=cls.grantee_group,
        )
        cls.sibling = Tenant.objects.create(
            name="XT Sibling",
            slug="xt-sibling",
            managed_by=cls.owner,
            group=cls.grantee_group,
        )
        owner_site = Site.objects.create(name="XT OSite", slug="xt-osite", tenant=cls.owner)
        cls.owner_location = Location.objects.create(
            name="XT Depot",
            slug="xt-depot",
            site=owner_site,
            tenant=cls.owner,
        )
        grantee_site = Site.objects.create(name="XT GSite", slug="xt-gsite", tenant=cls.grantee)
        cls.grantee_location = Location.objects.create(
            name="XT Office",
            slug="xt-office",
            site=grantee_site,
            tenant=cls.grantee,
        )
        mfr = Manufacturer.objects.create(name="XT Mfg", slug="xt-mfg")
        cls.accessory = Accessory.objects.create(
            name="XT Dock",
            slug="xt-dock",
            manufacturer=mfr,
            tenant=cls.owner,
        )
        cls.stock = AccessoryStock.objects.create(
            accessory=cls.accessory,
            location=cls.owner_location,
            qty=10,
        )
        cls.holder = AssetHolder.objects.create(
            first_name="Gran",
            last_name="Tee",
            upn="gran.tee@xt",
            tenant=cls.grantee,
        )
        cls.sibling_holder = AssetHolder.objects.create(
            first_name="Sib",
            last_name="Ling",
            upn="sib.ling@xt",
            tenant=cls.sibling,
        )
        cls.tech = User.objects.create_user(username="xt-tech", password="x")
        role = Role.objects.create(tenant=cls.grantee, name="XT Tech", permissions=[PERM])
        grant(cls.tech, cls.grantee, role)

    def test_stock_tenant_derived_from_location(self):
        assert self.stock.tenant_id == self.owner.pk
        moved = AccessoryStock.objects.create(
            accessory=self.accessory,
            location=self.grantee_location,
            qty=1,
        )
        assert moved.tenant_id == self.grantee.pk

    def test_stock_requires_owned_location(self):
        site = Site.objects.create(name="XT NoT", slug="xt-not-site")
        bare = Location.objects.create(name="XT Bare", slug="xt-bare", site=site)
        with self.assertRaises(ValidationError):
            AccessoryStock.objects.create(accessory=self.accessory, location=bare, qty=1)

    def test_cross_tenant_checkout_without_grant_denied(self):
        with self.tenant_context(self.grantee):
            with self.assertRaises(ValidationError):
                checkout_inventory_item(
                    self.accessory,
                    1,
                    holder=self.holder,
                    source_location=self.owner_location,
                    user=self.tech,
                )
        assert not AccessoryAssignment._base_manager.filter(accessory=self.accessory).exists()
        self.stock.refresh_from_db()
        assert self.stock.qty == 10

    def test_cross_tenant_checkout_with_grant_records_provenance(self):
        grant_row = _grant_use(self.owner, self.grantee, self.stock)
        with self.tenant_context(self.grantee):
            assignment = checkout_inventory_item(
                self.accessory,
                2,
                holder=self.holder,
                source_location=self.owner_location,
                user=self.tech,
            )
        assert assignment.resource_grant_id == grant_row.pk
        assert assignment.source_tenant_id == self.owner.pk
        assert assignment.target_tenant_id == self.grantee.pk
        self.stock.refresh_from_db()
        assert self.stock.qty == 8  # the OWNER's pool was deducted

    def test_cross_tenant_checkout_locks_exact_authorizing_grant(self):
        _grant_use(self.owner, self.grantee, self.stock)

        with self.tenant_context(self.grantee), CaptureQueriesContext(connection) as captured:
            checkout_inventory_item(
                self.accessory,
                1,
                holder=self.holder,
                source_location=self.owner_location,
                user=self.tech,
            )

        grant_table = connection.ops.quote_name(TenantResourceGrant._meta.db_table)
        grant_queries = [query["sql"] for query in captured.captured_queries if grant_table in query["sql"]]
        self.assertTrue(grant_queries)
        self.assertTrue(any("FOR UPDATE" in query.upper() for query in grant_queries), grant_queries)

    def test_actorless_checkout_requires_and_accepts_issued_system_authorization(self):
        grant_row = _grant_use(self.owner, self.grantee, self.stock)

        with TaskContext(tenant_id=self.grantee.pk, user_id=None) as task_context:
            authorization = task_context.authorize_system(
                permission=PERM,
                operation="inventory.checkout",
                reason="Approved actorless stock reconciliation",
            )
            assignment = checkout_inventory_item(
                self.accessory,
                1,
                holder=self.holder,
                source_location=self.owner_location,
                user=None,
                system_authorization=authorization,
            )

        self.assertEqual(assignment.resource_grant_id, grant_row.pk)
        self.assertEqual(assignment.source_tenant_id, self.owner.pk)
        self.assertEqual(assignment.target_tenant_id, self.grantee.pk)

    def test_group_grant_cannot_checkout_into_sibling_of_active_tenant(self):
        from django.contrib.contenttypes.models import ContentType

        TenantResourceGrant.objects.create(
            tenant=self.owner,
            grantee_tenant_group=self.grantee_group,
            resource_type=ContentType.objects.get_for_model(AccessoryStock),
            resource_id=self.stock.pk,
            access_level=TenantResourceGrant.ACCESS_USE,
        )

        with self.tenant_context(self.grantee):
            with self.assertRaises(ValidationError):
                checkout_inventory_item(
                    self.accessory,
                    1,
                    holder=self.sibling_holder,
                    source_location=self.owner_location,
                    user=self.tech,
                )

        self.assertFalse(
            AccessoryAssignment._base_manager.filter(
                accessory=self.accessory,
                assigned_holder=self.sibling_holder,
            ).exists()
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 10)

    def test_in_memory_target_tenant_spoof_cannot_cross_sibling_boundary(self):
        from django.contrib.contenttypes.models import ContentType

        TenantResourceGrant.objects.create(
            tenant=self.owner,
            grantee_tenant_group=self.grantee_group,
            resource_type=ContentType.objects.get_for_model(AccessoryStock),
            resource_id=self.stock.pk,
            access_level=TenantResourceGrant.ACCESS_USE,
        )
        self.sibling_holder.tenant_id = self.grantee.pk

        with self.tenant_context(self.grantee):
            with self.assertRaises(ValidationError):
                checkout_inventory_item(
                    self.accessory,
                    1,
                    holder=self.sibling_holder,
                    source_location=self.owner_location,
                    user=self.tech,
                )

        self.assertFalse(
            AccessoryAssignment._base_manager.filter(
                accessory=self.accessory,
                assigned_holder=self.sibling_holder,
            ).exists()
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.qty, 10)

    def test_service_uses_persisted_source_target_and_grant_provenance(self):
        grant_row = _grant_use(self.owner, self.grantee, self.stock)
        # Caller-held relation caches are deliberately falsified in both directions.
        self.owner_location.tenant_id = self.grantee.pk
        self.holder.tenant_id = self.owner.pk

        with self.tenant_context(self.grantee):
            assignment = checkout_inventory_item(
                self.accessory,
                1,
                holder=self.holder,
                source_location=self.owner_location,
                user=self.tech,
            )

        assignment.refresh_from_db()
        self.assertEqual(assignment.source_tenant_id, self.owner.pk)
        self.assertEqual(assignment.target_tenant_id, self.grantee.pk)
        self.assertEqual(assignment.resource_grant_id, grant_row.pk)

    def test_cross_tenant_checkout_view_grant_insufficient(self):
        grant_row = _grant_use(self.owner, self.grantee, self.stock)
        TenantResourceGrant._base_manager.filter(pk=grant_row.pk).update(
            access_level=TenantResourceGrant.ACCESS_VIEW,
        )
        with self.tenant_context(self.grantee):
            with self.assertRaises(ValidationError):
                checkout_inventory_item(
                    self.accessory,
                    1,
                    holder=self.holder,
                    source_location=self.owner_location,
                    user=self.tech,
                )

    def test_cross_tenant_checkout_requires_rbac_in_active_tenant(self):
        _grant_use(self.owner, self.grantee, self.stock)
        nobody = User.objects.create_user(username="xt-nobody", password="x")
        with self.tenant_context(self.grantee):
            with self.assertRaises(ValidationError):
                checkout_inventory_item(
                    self.accessory,
                    1,
                    holder=self.holder,
                    source_location=self.owner_location,
                    user=nobody,
                )

    def test_revoked_grant_blocks_new_checkout_but_keeps_history(self):
        grant_row = _grant_use(self.owner, self.grantee, self.stock)
        with self.tenant_context(self.grantee):
            assignment = checkout_inventory_item(
                self.accessory,
                1,
                holder=self.holder,
                source_location=self.owner_location,
                user=self.tech,
            )
        grant_row.delete()  # revoke
        assignment.refresh_from_db()
        assert assignment.resource_grant_id == grant_row.pk  # history survives
        with self.tenant_context(self.grantee):
            with self.assertRaises(ValidationError):
                checkout_inventory_item(
                    self.accessory,
                    1,
                    holder=self.holder,
                    source_location=self.owner_location,
                    user=self.tech,
                )

    def test_bare_actorless_same_tenant_checkout_is_denied(self):
        owner_holder = AssetHolder.objects.create(
            first_name="Own",
            last_name="Er",
            upn="own.er@xt",
            tenant=self.owner,
        )
        with self.tenant_context(self.owner):
            with self.assertRaises(ValidationError):
                checkout_inventory_item(
                    self.accessory,
                    1,
                    holder=owner_holder,
                    source_location=self.owner_location,
                )

    def test_issued_actorless_same_tenant_checkout_carries_no_grant(self):
        owner_holder = AssetHolder.objects.create(
            first_name="System",
            last_name="Target",
            upn="system.target@xt",
            tenant=self.owner,
        )
        with TaskContext(tenant_id=self.owner.pk, user_id=None) as task_context:
            authorization = task_context.authorize_system(
                permission="inventory.add_accessoryassignment",
                operation="inventory.checkout",
                reason="Approved same-tenant stock reconciliation",
            )
            assignment = checkout_inventory_item(
                self.accessory,
                1,
                holder=owner_holder,
                source_location=self.owner_location,
                system_authorization=authorization,
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

    def test_direct_actorless_same_tenant_assignment_factory_is_denied(self):
        owner_holder = AssetHolder.objects.create(
            first_name="Direct",
            last_name="Owner",
            upn="direct.owner@example.test",
            tenant=self.owner,
        )

        with self.assertRaises(ValidationError):
            AccessoryAssignment.objects.create(
                accessory=self.accessory,
                assigned_holder=owner_holder,
                from_location=self.owner_location,
                qty=1,
            )

    def test_direct_actorless_cross_tenant_assignment_factory_is_denied(self):
        grant_row = TenantResourceGrant.objects.create(
            tenant=self.owner,
            grantee_tenant=self.grantee,
            resource_type=ContentType.objects.get_for_model(AccessoryStock),
            resource_id=self.stock.pk,
            access_level=TenantResourceGrant.ACCESS_USE,
        )

        with self.assertRaises(ValidationError):
            AccessoryAssignment.objects.create(
                accessory=self.accessory,
                assigned_holder=self.holder,
                from_location=self.owner_location,
                qty=1,
                resource_grant=grant_row,
            )
