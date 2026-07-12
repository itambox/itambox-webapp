"""Phase-1 regression tests for the read-only integrity checks in ``core.integrity``.

Covers ``TenantTopology.classify()`` (including cycle-safe ``group_root()``) and
every DATA check (``check_null_tenants``, ``check_stock_tenant_conflicts``,
``check_cross_tenant_assignments``, ``check_location_site_tenants``,
``check_purchase_orders``, ``check_license_seats``, ``check_custody_receipts``).
``check_rbac_grants`` is out of scope here (covered elsewhere).

Fixtures deliberately create VALID rows and only reach for
``Model._base_manager.filter(...).update(...)`` (bypassing ``save()``/``clean()``)
when the model's own ``clean()`` would reject the cross-tenant anomaly directly
(``Asset``/``PurchaseOrder`` tenant=NULL, ``AssetAssignment`` cross-tenant
target). Every other model under test here has no such ``clean()`` guard, so
the anomaly is created directly.
"""
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from organization.models import Tenant, TenantGroup, Location, Site, AssetHolder, Contact
from assets.models import Asset, AssetAssignment, Manufacturer, AssetType, Supplier
from inventory.models import Accessory, AccessoryStock, AccessoryAssignment
from procurement.models import PurchaseOrder, PurchaseOrderLine
from licenses.models import License, LicenseSeatAssignment, LicenseTypeChoices
from software.models import Software
from compliance.models import CustodyReceipt

from core.integrity import (
    TenantTopology,
    CLASS_SAME_TENANT, CLASS_PROVIDER_MANAGED, CLASS_TENANT_GROUP,
    CLASS_AMBIGUOUS, CLASS_INVALID,
    check_null_tenants, check_stock_tenant_conflicts,
    check_cross_tenant_assignments, check_location_site_tenants,
    check_purchase_orders, check_license_seats, check_custody_receipts,
    run_all_checks,
)


def _build_msp_topology():
    """Shared MSP tenant/group topology reused across every test class below.

    Provider layer:  provider (is_provider=True) --manages--> managed
    Group layer:      group_root
                         |-- group_a  (tenant_group_a lives here)
                         |-- group_b  (tenant_group_b lives here)
    Standalone:       unrelated  (no group, no management link to anything)
    """
    provider = Tenant.objects.create(name='Provider Co', slug='msp-provider', is_provider=True)
    managed = Tenant.objects.create(name='Managed Co', slug='msp-managed', managed_by=provider)

    group_root = TenantGroup.objects.create(name='Group Root', slug='msp-grp-root')
    group_a = TenantGroup.objects.create(name='Group A', slug='msp-grp-a', parent=group_root)
    group_b = TenantGroup.objects.create(name='Group B', slug='msp-grp-b', parent=group_root)

    tenant_group_a = Tenant.objects.create(name='Group Tenant A', slug='msp-tg-a', group=group_a)
    tenant_group_b = Tenant.objects.create(name='Group Tenant B', slug='msp-tg-b', group=group_b)

    unrelated = Tenant.objects.create(name='Unrelated Co', slug='msp-unrelated')

    return SimpleNamespace(
        provider=provider, managed=managed,
        group_root=group_root, group_a=group_a, group_b=group_b,
        tenant_group_a=tenant_group_a, tenant_group_b=tenant_group_b,
        unrelated=unrelated,
    )


def _site_location(tenant, slug):
    site = Site.objects.create(name=f'Site {slug}', slug=f'site-{slug}', tenant=tenant)
    return Location.objects.create(name=f'Loc {slug}', slug=f'loc-{slug}', site=site, tenant=tenant)


class TenantTopologyClassifyTests(TestCase):
    def setUp(self):
        self.t = _build_msp_topology()
        self.topo = TenantTopology()

    def test_same_tenant(self):
        self.assertEqual(
            self.topo.classify(self.t.provider.pk, self.t.provider.pk), CLASS_SAME_TENANT,
        )

    def test_provider_to_managed_both_directions(self):
        self.assertEqual(
            self.topo.classify(self.t.provider.pk, self.t.managed.pk), CLASS_PROVIDER_MANAGED,
        )
        self.assertEqual(
            self.topo.classify(self.t.managed.pk, self.t.provider.pk), CLASS_PROVIDER_MANAGED,
        )

    def test_within_tenant_group(self):
        self.assertEqual(
            self.topo.classify(self.t.tenant_group_a.pk, self.t.tenant_group_b.pk),
            CLASS_TENANT_GROUP,
        )
        self.assertEqual(
            self.topo.classify(self.t.tenant_group_b.pk, self.t.tenant_group_a.pk),
            CLASS_TENANT_GROUP,
        )

    def test_ambiguous_when_either_side_none(self):
        self.assertEqual(self.topo.classify(None, self.t.provider.pk), CLASS_AMBIGUOUS)
        self.assertEqual(self.topo.classify(self.t.provider.pk, None), CLASS_AMBIGUOUS)
        self.assertEqual(self.topo.classify(None, None), CLASS_AMBIGUOUS)

    def test_unrelated_invalid(self):
        self.assertEqual(
            self.topo.classify(self.t.unrelated.pk, self.t.provider.pk), CLASS_INVALID,
        )
        self.assertEqual(
            self.topo.classify(self.t.unrelated.pk, self.t.tenant_group_a.pk), CLASS_INVALID,
        )

    def test_group_root_cycle_safety(self):
        # Build a 2-node TenantGroup parent cycle bypassing save()/clean() via
        # .update() (matches the general seeding-anomaly pattern used for
        # models whose clean() would otherwise reject the state).
        g1 = TenantGroup.objects.create(name='Cycle 1', slug='msp-cyc-1')
        g2 = TenantGroup.objects.create(name='Cycle 2', slug='msp-cyc-2')
        TenantGroup.objects.filter(pk=g1.pk).update(parent_id=g2.pk)
        TenantGroup.objects.filter(pk=g2.pk).update(parent_id=g1.pk)
        tenant_c1 = Tenant.objects.create(name='Cycle Tenant 1', slug='msp-cyc-t1', group=g1)
        tenant_c2 = Tenant.objects.create(name='Cycle Tenant 2', slug='msp-cyc-t2', group=g2)

        # Snapshot AFTER the fixture rows exist — TenantTopology loads its
        # tenant/group tables once at construction time.
        topo = TenantTopology()
        root1 = topo.group_root(g1.pk)
        root2 = topo.group_root(g2.pk)
        # Terminates (no infinite loop) and both cycle members agree on the
        # same deterministic representative (the smallest pk in the cycle).
        self.assertEqual(root1, root2)
        self.assertEqual(root1, min(g1.pk, g2.pk))
        self.assertEqual(topo.classify(tenant_c1.pk, tenant_c2.pk), CLASS_TENANT_GROUP)


class CheckNullTenantsTests(TestCase):
    def test_asset_null_tenant_is_flagged_with_sample_pks(self):
        tenant = Tenant.objects.create(name='Null Tenant Co', slug='nt-asset')
        asset = Asset.objects.create(name='Orphan Laptop', tenant=tenant, asset_tag='NT-AST-1')
        Asset._base_manager.filter(pk=asset.pk).update(tenant=None)

        findings = check_null_tenants()
        asset_findings = [f for f in findings if f.model == 'assets.Asset']
        self.assertEqual(len(asset_findings), 1)
        finding = asset_findings[0]
        self.assertEqual(finding.check, 'null_tenant')
        self.assertGreaterEqual(finding.details['count'], 1)
        self.assertIn(asset.pk, finding.details['sample_pks'])

    def test_purchase_order_null_tenant_is_flagged(self):
        tenant = Tenant.objects.create(name='Null Tenant PO Co', slug='nt-po')
        supplier = Supplier.objects.create(name='NT Supplier', slug='nt-supplier')
        destination = _site_location(tenant, 'nt-po-dest')
        po = PurchaseOrder.objects.create(
            tenant=tenant, order_number='NT-PO-1', supplier=supplier,
            destination_location=destination,
        )
        PurchaseOrder._base_manager.filter(pk=po.pk).update(tenant=None)

        findings = check_null_tenants()
        po_findings = [f for f in findings if f.model == 'procurement.PurchaseOrder']
        self.assertEqual(len(po_findings), 1)
        self.assertIn(po.pk, po_findings[0].details['sample_pks'])

    def test_global_contact_not_flagged(self):
        Contact.objects.create(name='Global Support Desk', tenant=None)
        findings = check_null_tenants()
        self.assertFalse(any(f.model == 'organization.Contact' for f in findings))

    def test_soft_deleted_null_tenant_row_not_counted(self):
        tenant = Tenant.objects.create(name='SD Tenant Co', slug='nt-sd')
        asset = Asset.objects.create(name='Deleted Orphan', tenant=tenant, asset_tag='NT-AST-SD')
        Asset._base_manager.filter(pk=asset.pk).update(tenant=None, deleted_at=timezone.now())

        findings = check_null_tenants()
        self.assertFalse(any(f.model == 'assets.Asset' for f in findings))


class CheckStockTenantConflictsTests(TestCase):
    def setUp(self):
        self.t = _build_msp_topology()
        self.mfr = Manufacturer.objects.create(name='Stock Mfr', slug='stock-mfr')

    def test_provider_owned_location_managed_item_conflict(self):
        accessory = Accessory.objects.create(
            name='Provider Dock', manufacturer=self.mfr, tenant=self.t.provider,
        )
        location = _site_location(self.t.managed, 'stock-managed')
        stock = AccessoryStock.objects.create(accessory=accessory, location=location, qty=3)

        findings = check_stock_tenant_conflicts()
        matches = [f for f in findings if f.model == 'inventory.AccessoryStock' and f.pk == stock.pk]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].classification, CLASS_PROVIDER_MANAGED)

    def test_location_without_tenant_is_ambiguous(self):
        accessory = Accessory.objects.create(
            name='Homeless Dock', manufacturer=self.mfr, tenant=self.t.provider,
        )
        site = Site.objects.create(name='No-Tenant Site', slug='site-no-tenant', tenant=None)
        location = Location.objects.create(
            name='No-Tenant Loc', slug='loc-no-tenant', site=site, tenant=None,
        )
        stock = AccessoryStock.objects.create(accessory=accessory, location=location, qty=1)

        findings = check_stock_tenant_conflicts()
        matches = [f for f in findings if f.model == 'inventory.AccessoryStock' and f.pk == stock.pk]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].classification, CLASS_AMBIGUOUS)

    def test_global_item_at_owned_location_is_fine(self):
        accessory = Accessory.objects.create(
            name='Global Dock', manufacturer=self.mfr, tenant=None,
        )
        location = _site_location(self.t.provider, 'stock-global-owned')
        stock = AccessoryStock.objects.create(accessory=accessory, location=location, qty=2)

        findings = check_stock_tenant_conflicts()
        self.assertFalse(any(f.pk == stock.pk for f in findings))

    def test_matching_tenants_is_fine(self):
        accessory = Accessory.objects.create(
            name='Local Dock', manufacturer=self.mfr, tenant=self.t.provider,
        )
        location = _site_location(self.t.provider, 'stock-matching')
        stock = AccessoryStock.objects.create(accessory=accessory, location=location, qty=4)

        findings = check_stock_tenant_conflicts()
        self.assertFalse(any(f.pk == stock.pk for f in findings))


class CheckCrossTenantAssignmentsTests(TestCase):
    def setUp(self):
        self.t = _build_msp_topology()
        self.mfr = Manufacturer.objects.create(name='Assignment Mfr', slug='assign-mfr')

    def test_provider_to_managed_checkout_yields_finding_and_grant_proposal(self):
        accessory = Accessory.objects.create(
            name='Shared Headset', manufacturer=self.mfr, tenant=self.t.provider,
            allow_overallocate=True,
        )
        from_location = _site_location(self.t.provider, 'assign-src-a')
        holder = AssetHolder.objects.create(
            first_name='Man', last_name='Aged', upn='man.aged@managed.example.com',
            tenant=self.t.managed,
        )
        assignment = AccessoryAssignment.objects.create(
            accessory=accessory, from_location=from_location, assigned_holder=holder, qty=1,
        )
        stock = AccessoryStock.objects.get(accessory=accessory, location=from_location)

        findings, proposals = check_cross_tenant_assignments()
        matches = [
            f for f in findings
            if f.model == 'inventory.AccessoryAssignment' and f.pk == assignment.pk
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].classification, CLASS_PROVIDER_MANAGED)

        matching_proposals = [
            p for p in proposals
            if p.resource_model == 'inventory.AccessoryStock' and p.item_id == accessory.pk
            and p.location_id == from_location.pk and p.grantee_tenant_id == self.t.managed.pk
        ]
        self.assertEqual(len(matching_proposals), 1)
        proposal = matching_proposals[0]
        self.assertEqual(proposal.owner_tenant_id, self.t.provider.pk)
        self.assertEqual(proposal.stock_id, stock.pk)
        self.assertEqual(proposal.access_level, 'use')
        self.assertEqual(proposal.classification, CLASS_PROVIDER_MANAGED)

    def test_unrelated_tenants_yield_finding_but_no_proposal(self):
        accessory = Accessory.objects.create(
            name='Rogue Headset', manufacturer=self.mfr, tenant=self.t.unrelated,
            allow_overallocate=True,
        )
        from_location = _site_location(self.t.unrelated, 'assign-src-unrel')
        # provider has no group and no management relation to `unrelated`.
        holder = AssetHolder.objects.create(
            first_name='Other', last_name='Holder', upn='other.holder@provider.example.com',
            tenant=self.t.provider,
        )
        assignment = AccessoryAssignment.objects.create(
            accessory=accessory, from_location=from_location, assigned_holder=holder, qty=1,
        )

        findings, proposals = check_cross_tenant_assignments()
        matches = [
            f for f in findings
            if f.model == 'inventory.AccessoryAssignment' and f.pk == assignment.pk
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].classification, CLASS_INVALID)
        evidence = f'inventory.AccessoryAssignment #{assignment.pk}'
        self.assertFalse(any(p.evidence == evidence for p in proposals))

    def test_no_source_location_and_global_item_is_ambiguous_no_proposal(self):
        accessory = Accessory.objects.create(
            name='Global Mouse', manufacturer=self.mfr, tenant=None,
        )
        holder = AssetHolder.objects.create(
            first_name='Any', last_name='Holder', upn='any.holder@managed.example.com',
            tenant=self.t.managed,
        )
        assignment = AccessoryAssignment.objects.create(
            accessory=accessory, from_location=None, assigned_holder=holder, qty=1,
        )

        findings, proposals = check_cross_tenant_assignments()
        matches = [
            f for f in findings
            if f.model == 'inventory.AccessoryAssignment' and f.pk == assignment.pk
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].classification, CLASS_AMBIGUOUS)
        evidence = f'inventory.AccessoryAssignment #{assignment.pk}'
        self.assertFalse(any(p.evidence == evidence for p in proposals))

    def test_proposal_dedup_across_assignments_to_same_grantee(self):
        accessory = Accessory.objects.create(
            name='Dedup Dock', manufacturer=self.mfr, tenant=self.t.provider,
            allow_overallocate=True,
        )
        from_location = _site_location(self.t.provider, 'assign-dedup')
        holder1 = AssetHolder.objects.create(
            first_name='One', last_name='Managed', upn='one.managed@managed.example.com',
            tenant=self.t.managed,
        )
        holder2 = AssetHolder.objects.create(
            first_name='Two', last_name='Managed', upn='two.managed@managed.example.com',
            tenant=self.t.managed,
        )
        AccessoryAssignment.objects.create(
            accessory=accessory, from_location=from_location, assigned_holder=holder1, qty=1,
        )
        AccessoryAssignment.objects.create(
            accessory=accessory, from_location=from_location, assigned_holder=holder2, qty=1,
        )

        _findings, proposals, _stats = run_all_checks()
        matching = [
            p for p in proposals
            if p.resource_model == 'inventory.AccessoryStock' and p.item_id == accessory.pk
            and p.location_id == from_location.pk and p.grantee_tenant_id == self.t.managed.pk
        ]
        self.assertEqual(len(matching), 1)

    def test_asset_assignment_cross_tenant_flagged(self):
        holder_a = AssetHolder.objects.create(
            first_name='Own', last_name='Tenant', upn='own.tenant@provider.example.com',
            tenant=self.t.provider,
        )
        holder_b = AssetHolder.objects.create(
            first_name='Cross', last_name='Tenant', upn='cross.tenant@managed.example.com',
            tenant=self.t.managed,
        )
        asset = Asset.objects.create(name='AA Laptop', tenant=self.t.provider, asset_tag='AA-CROSS-1')
        assignment = AssetAssignment.objects.create(asset=asset, assigned_user=holder_a)
        # AssetAssignment.clean() enforces same-tenant targets; seed the
        # cross-tenant anomaly by flipping the target after the valid save.
        AssetAssignment._base_manager.filter(pk=assignment.pk).update(assigned_user=holder_b)

        findings, _proposals = check_cross_tenant_assignments()
        matches = [
            f for f in findings if f.model == 'assets.AssetAssignment' and f.pk == assignment.pk
        ]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].details['source_tenant_id'], self.t.provider.pk)
        self.assertEqual(matches[0].details['target_tenant_id'], self.t.managed.pk)


class CheckLocationSiteTenantsTests(TestCase):
    def setUp(self):
        self.t = _build_msp_topology()

    def test_mismatched_tenants_flagged(self):
        site = Site.objects.create(name='LS Site', slug='ls-site-a', tenant=self.t.provider)
        location = Location.objects.create(
            name='LS Location', slug='ls-loc-a', site=site, tenant=self.t.managed,
        )
        findings = check_location_site_tenants()
        matches = [f for f in findings if f.pk == location.pk]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].classification, CLASS_PROVIDER_MANAGED)

    def test_matching_tenants_not_flagged(self):
        site = Site.objects.create(name='LS Site B', slug='ls-site-b', tenant=self.t.provider)
        location = Location.objects.create(
            name='LS Location B', slug='ls-loc-b', site=site, tenant=self.t.provider,
        )
        findings = check_location_site_tenants()
        self.assertFalse(any(f.pk == location.pk for f in findings))


class CheckPurchaseOrdersTests(TestCase):
    def setUp(self):
        self.t = _build_msp_topology()
        self.mfr = Manufacturer.objects.create(name='PO Mfr', slug='po-mfr')
        self.supplier = Supplier.objects.create(name='PO Supplier', slug='po-supplier')

    def test_po_destination_tenant_mismatch_flagged(self):
        destination = _site_location(self.t.managed, 'po-dest-mismatch')
        po = PurchaseOrder.objects.create(
            tenant=self.t.provider, order_number='PO-MISMATCH-1',
            supplier=self.supplier, destination_location=destination,
        )
        findings = check_purchase_orders()
        matches = [f for f in findings if f.check == 'po_tenant_mismatch' and f.pk == po.pk]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].classification, CLASS_PROVIDER_MANAGED)

    def test_line_tenant_mismatch_flagged(self):
        # asset_type is global catalogue (no tenant field) — isolates this
        # scenario from the po_line_item_tenant_mismatch check.
        destination = _site_location(self.t.provider, 'po-dest-line-mismatch')
        po = PurchaseOrder.objects.create(
            tenant=self.t.provider, order_number='PO-LINE-MISMATCH-1',
            supplier=self.supplier, destination_location=destination,
        )
        asset_type = AssetType.objects.create(manufacturer=self.mfr, model='PO Line Model')
        line = PurchaseOrderLine.objects.create(
            purchase_order=po, asset_type=asset_type, tenant=self.t.managed, qty_ordered=1,
        )
        findings = check_purchase_orders()
        matches = [
            f for f in findings if f.check == 'po_line_tenant_mismatch' and f.pk == line.pk
        ]
        self.assertEqual(len(matches), 1)

    def test_line_item_tenant_mismatch_flagged(self):
        destination = _site_location(self.t.provider, 'po-dest-item-mismatch')
        po = PurchaseOrder.objects.create(
            tenant=self.t.provider, order_number='PO-ITEM-MISMATCH-1',
            supplier=self.supplier, destination_location=destination,
        )
        accessory = Accessory.objects.create(
            name='PO Item Accessory', manufacturer=self.mfr, tenant=self.t.managed,
        )
        line = PurchaseOrderLine.objects.create(
            purchase_order=po, accessory=accessory, tenant=self.t.provider, qty_ordered=1,
        )
        findings = check_purchase_orders()
        matches = [
            f for f in findings if f.check == 'po_line_item_tenant_mismatch' and f.pk == line.pk
        ]
        self.assertEqual(len(matches), 1)

    def test_global_line_item_not_flagged(self):
        destination = _site_location(self.t.provider, 'po-dest-global-item')
        po = PurchaseOrder.objects.create(
            tenant=self.t.provider, order_number='PO-GLOBAL-ITEM-1',
            supplier=self.supplier, destination_location=destination,
        )
        accessory = Accessory.objects.create(
            name='PO Global Accessory', manufacturer=self.mfr, tenant=None,
        )
        line = PurchaseOrderLine.objects.create(
            purchase_order=po, accessory=accessory, tenant=self.t.provider, qty_ordered=1,
        )
        findings = check_purchase_orders()
        self.assertFalse(any(f.pk == line.pk for f in findings))


class CheckLicenseSeatsTests(TestCase):
    def setUp(self):
        self.t = _build_msp_topology()
        self.mfr = Manufacturer.objects.create(name='Seat Mfr', slug='seat-mfr')
        self.software = Software.objects.create(
            name='Seat Software', manufacturer=self.mfr, tenant=self.t.provider,
        )

    def _license(self, name):
        return License.objects.create(
            name=name, software=self.software,
            license_type=LicenseTypeChoices.PERPETUAL_SEAT, seats=5, tenant=self.t.provider,
        )

    def test_cross_tenant_asset_seat_flagged(self):
        license_ = self._license('Seat License A')
        asset = Asset.objects.create(name='Seat Asset', tenant=self.t.managed, asset_tag='SEAT-AST-1')
        seat = LicenseSeatAssignment.objects.create(license=license_, asset=asset)

        findings = check_license_seats()
        matches = [f for f in findings if f.pk == seat.pk]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].classification, CLASS_PROVIDER_MANAGED)

    def test_cross_tenant_holder_seat_flagged(self):
        license_ = self._license('Seat License B')
        holder = AssetHolder.objects.create(
            first_name='Seat', last_name='Holder', upn='seat.holder@managed.example.com',
            tenant=self.t.managed,
        )
        seat = LicenseSeatAssignment.objects.create(license=license_, assigned_holder=holder)

        findings = check_license_seats()
        matches = [f for f in findings if f.pk == seat.pk]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].classification, CLASS_PROVIDER_MANAGED)

    def test_same_tenant_seat_not_flagged(self):
        license_ = self._license('Seat License C')
        holder = AssetHolder.objects.create(
            first_name='Local', last_name='Holder', upn='local.holder@provider.example.com',
            tenant=self.t.provider,
        )
        seat = LicenseSeatAssignment.objects.create(license=license_, assigned_holder=holder)

        findings = check_license_seats()
        self.assertFalse(any(f.pk == seat.pk for f in findings))


class CheckCustodyReceiptsTests(TestCase):
    def setUp(self):
        self.t = _build_msp_topology()

    def test_cross_tenant_holder_flagged(self):
        asset = Asset.objects.create(name='Custody Asset', tenant=self.t.provider, asset_tag='CUST-AST-1')
        holder = AssetHolder.objects.create(
            first_name='Custody', last_name='Holder', upn='custody.holder@managed.example.com',
            tenant=self.t.managed,
        )
        receipt = CustodyReceipt.objects.create(asset=asset, holder=holder)

        findings = check_custody_receipts()
        matches = [f for f in findings if f.pk == receipt.pk]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].classification, CLASS_PROVIDER_MANAGED)

    def test_same_tenant_not_flagged(self):
        asset = Asset.objects.create(name='Custody Asset 2', tenant=self.t.provider, asset_tag='CUST-AST-2')
        holder = AssetHolder.objects.create(
            first_name='Local', last_name='Custody', upn='local.custody@provider.example.com',
            tenant=self.t.provider,
        )
        receipt = CustodyReceipt.objects.create(asset=asset, holder=holder)

        findings = check_custody_receipts()
        self.assertFalse(any(f.pk == receipt.pk for f in findings))
