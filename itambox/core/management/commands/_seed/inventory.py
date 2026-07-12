"""Inventory seed mixin: accessory/consumable catalog, stock, assignments, kits.

Designed to be mixed into ``Command`` in seed_data.py:

    from core.management.commands._seed.inventory import SeedInventoryStockMixin

    class Command(SeedInventoryStockMixin, BaseCommand):
        ...

``_seed_inventory_stock`` must run after ``_seed_catalog`` /
``_seed_organizations`` / ``_seed_assets`` (it reads ``self._accessory_defs`` /
``self._consumable_defs`` / ``self._manufacturers`` / ``self._categories`` /
``self._tenants`` / ``self._tenant_locations`` / ``self._tenant_holders`` /
``self._components`` / ``self._asset_types``). It populates / overwrites
``self._accessories`` and ``self._consumables``.
"""

import random


class SeedInventoryStockMixin:
    """Mixin for Command(BaseCommand).  Reads/writes self._ registries."""

    def _seed_inventory_stock(self):
        from inventory.models import (Accessory, Consumable, AccessoryStock, ConsumableStock,
                                       ComponentStock, AccessoryAssignment, ConsumableAssignment, Kit, KitItem)
        self.stdout.write('--- Inventory: stock & kits ---')

        catalog_tenant = self._tenants['northwind-internal-it']
        self._accessories = {}
        for name, slug, mfr, cat, part, min_qty in self._accessory_defs:
            self._accessories[slug] = Accessory.objects.get_or_create(slug=slug, defaults={
                'name': name, 'manufacturer': self._manufacturers[mfr], 'category': self._categories[cat],
                'part_number': part, 'min_qty': min_qty, 'tenant': catalog_tenant})[0]
        self._consumables = {}
        for name, slug, mfr, cat, part, min_qty in self._consumable_defs:
            self._consumables[slug] = Consumable.objects.get_or_create(slug=slug, defaults={
                'name': name, 'manufacturer': self._manufacturers[mfr], 'category': self._categories[cat],
                'part_number': part, 'min_qty': min_qty, 'tenant': catalog_tenant})[0]

        # Stock at the MSP and at each tenant's first location.
        stock_count = 0
        for slug in self._tenants:
            locs = self._tenant_locations[slug]
            if not locs:
                continue
            loc = locs[0]
            for acc_slug in random.sample(list(self._accessories), k=4):
                # Deliberately leave a couple below min_qty to trigger low-stock alerts.
                qty = random.choice([0, 2, 3, 8, 12, 20])
                AccessoryStock.objects.get_or_create(accessory=self._accessories[acc_slug], location=loc,
                                                      defaults={'qty': qty})
                stock_count += 1
            for con_slug in random.sample(list(self._consumables), k=2):
                ConsumableStock.objects.get_or_create(consumable=self._consumables[con_slug], location=loc,
                                                       defaults={'qty': random.choice([1, 4, 10, 25])})
                stock_count += 1

        # Spare-parts (component) stock held at server rooms / DC racks. The MSP holds
        # the deepest spares pool; tenants with their own server location keep a few.
        comp_count = 0

        def _infra_loc(tslug):
            for kw in ('srv', 'rack', 'dc', 'server', 'network', 'closet', 'cabinet', 'farm'):
                for lo in self._tenant_locations.get(tslug, []):
                    if kw in lo.slug:
                        return lo
            return None

        # Deep central spares pool at the MSP Frankfurt DC (or its first infra location).
        msp_loc = _infra_loc('northwind-internal-it') or self._tenant_locations['northwind-internal-it'][0]
        for comp_slug, comp in self._components.items():
            ComponentStock.objects.get_or_create(
                component=comp, location=msp_loc,
                defaults={'qty': random.choice([2, 4, 5, 8, 12, 0])})  # one 0 → low-stock signal
            comp_count += 1
        # A shallower spares pool at customer server rooms.
        for tslug in self._tenants:
            loc = _infra_loc(tslug)
            if not loc or tslug == 'northwind-internal-it':
                continue
            for comp_slug in random.sample(list(self._components), k=random.randint(2, 4)):
                ComponentStock.objects.get_or_create(
                    component=self._components[comp_slug], location=loc,
                    defaults={'qty': random.choice([1, 2, 3, 4])})
                comp_count += 1

        # Accessory/consumable issues to holders, drawn from each tenant's own
        # first-location pool: the unit is provisioned into the local pool and
        # handed out from there. ADR-0001 phase 4 keeps this grant-free — the
        # pool at the customer location belongs to the customer even though
        # the catalogue item is MSP-owned; assigning MSP-sourced units without
        # a local pool would now (correctly) demand a TenantResourceGrant.
        assign_count = 0
        for tslug, holders in self._tenant_holders.items():
            locs = self._tenant_locations.get(tslug) or []
            if not locs or not holders:
                continue
            loc = locs[0]
            for holder in random.sample(holders, k=min(8, len(holders))):
                for acc_slug in random.sample(list(self._accessories), k=random.randint(1, 3)):
                    acc = self._accessories[acc_slug]
                    pool, _created = AccessoryStock.objects.get_or_create(
                        accessory=acc, location=loc, defaults={'qty': 0})
                    pool.qty += 1  # provision the unit locally before issuing it
                    pool.save(update_fields=['qty'])
                    AccessoryAssignment.objects.create(
                        accessory=acc, assigned_holder=holder, qty=1, from_location=loc)
                    assign_count += 1
            for holder in random.sample(holders, k=min(1, len(holders))):
                con = self._consumables['aa-batteries-24']
                pool, _created = ConsumableStock.objects.get_or_create(
                    consumable=con, location=loc, defaults={'qty': 0})
                pool.qty += 1
                pool.save(update_fields=['qty'])
                ConsumableAssignment.objects.create(
                    consumable=con, assigned_holder=holder, qty=1, from_location=loc)

        # Kits
        kits = [
            ('Developer Onboarding Kit', 'northwind-internal-it',
             [('thinkpad-x1-carbon-g12', 1)], [('mx-master-3s', 1), ('mx-keys', 1), ('tb4-dock', 1)]),
            ('Executive Onboarding Kit', 'northwind-corporate',
             [('macbook-pro-16-2024', 1), ('iphone-15-pro', 1)], [('usb-c-charger-65w', 2), ('zone-wireless-2', 1)]),
            ('Trading Desk Setup', 'meridian-investment',
             [('macbook-pro-16-2024', 1)], [('dell-p2723de', 2), ('mx-master-3s', 1), ('tb4-dock', 1)]),
            ('Field Technician Kit', 'vantage-logistics',
             [('surface-pro-10', 1)], [('usb-c-charger-65w', 1), ('usb-c-hdmi-adapter', 1)]),
        ]
        for name, tenant_slug, at_items, acc_items in kits:
            kit = Kit.objects.create(name=name, description=f'Standard provisioning bundle: {name}.',
                                     tenant=self._tenants[tenant_slug])
            for at_slug, qty in at_items:
                KitItem.objects.create(kit=kit, asset_type=self._asset_types[at_slug], qty=qty)
            for acc_slug, qty in acc_items:
                KitItem.objects.create(kit=kit, accessory=self._accessories[acc_slug], qty=qty)

        self.stdout.write(f'  {len(self._accessories)} accessories, {len(self._consumables)} consumables, '
                          f'{stock_count} accessory/consumable stock rows, {comp_count} component stock rows, '
                          f'{assign_count} accessory assignments, {len(kits)} kits.')
