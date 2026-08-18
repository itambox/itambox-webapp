"""Inventory seed mixin: accessory/consumable catalog, stock, assignments, kits.

Designed to be mixed into ``Command`` in seed_data.py:

    from core.management.commands._seed.inventory import SeedInventoryStockMixin

    class Command(SeedInventoryStockMixin, BaseCommand):
        ...

``_seed_inventory_stock`` must run after ``_seed_catalog`` /
``_seed_organizations`` / ``_seed_assets`` (it reads ``self._accessory_defs`` /
``self._consumable_defs`` / ``self._manufacturers`` / ``self._categories`` /
``self._tenants`` / ``self._tenant_locations`` / ``self._tenant_holders`` /
``self._components`` / ``self._asset_types`` /
``self._component_allocation_plan``). It populates / overwrites
``self._accessories`` and ``self._consumables``.
"""

import random
from collections import Counter

from django.core.management.base import CommandError

from core.tasks.context import TaskContext
from inventory.models import (
    Accessory,
    AccessoryAssignment,
    AccessoryStock,
    Component,
    ComponentAllocation,
    ComponentStock,
    Consumable,
    ConsumableAssignment,
    ConsumableStock,
    Kit,
    KitItem,
)
from inventory.services import checkout_inventory_item


def _sum_quantities(queryset):
    return sum(queryset.values_list("qty", flat=True))


def _check_assignment_shape(assignment, label):
    if assignment.qty <= 0:
        raise CommandError(f"Seed inventory invariant failed: {label} {assignment.pk} has non-positive quantity.")
    target_count = sum(
        value is not None
        for value in (assignment.assigned_holder_id, assignment.assigned_location_id, assignment.assigned_asset_id)
    )
    if target_count != 1:
        raise CommandError(
            f"Seed inventory invariant failed: {label} {assignment.pk} has {target_count} assignment targets."
        )


def _check_source_stock(assignment, stock_model, item_field, label):
    if (
        assignment.from_location_id
        and not stock_model._base_manager.filter(
            **{
                f"{item_field}_id": getattr(assignment, f"{item_field}_id"),
                "location_id": assignment.from_location_id,
            }
        ).exists()
    ):
        raise CommandError(
            f"Seed inventory invariant failed: {label} {assignment.pk} references a missing source stock row."
        )


def _check_stock_rows():
    # AbstractStock is not soft-deletable; _base_manager intentionally covers
    # the complete persisted stock balance here.
    for stock_model in (ComponentStock, AccessoryStock, ConsumableStock):
        negative_stock = stock_model._base_manager.filter(qty__lt=0).first()
        if negative_stock is not None:
            raise CommandError(
                f"Seed inventory invariant failed: {stock_model._meta.verbose_name} "
                f"{negative_stock.pk} has negative stock ({negative_stock.qty})."
            )
        for stock in stock_model._base_manager.select_related("location"):
            if stock.location.tenant_id != stock.tenant_id:
                raise CommandError(
                    f"Seed inventory invariant failed: {stock_model._meta.verbose_name} {stock.pk} "
                    "tenant does not match its location."
                )


def _check_component_balances():
    for item in Component._base_manager.filter(deleted_at__isnull=True):
        allocations = ComponentAllocation._base_manager.filter(component_id=item.pk, deleted_at__isnull=True)
        for allocation in allocations:
            _check_assignment_shape(allocation, "component allocation")
            _check_source_stock(allocation, ComponentStock, "component", "component allocation")
        total_stock = _sum_quantities(ComponentStock._base_manager.filter(component_id=item.pk))
        total_allocated = _sum_quantities(allocations)
        if total_allocated > total_stock:
            raise CommandError(
                f"Seed inventory invariant failed: component {item.pk} allocates {total_allocated} "
                f"from {total_stock} total stock."
            )


def _check_item_balances(item, assignment_model, stock_model, item_field, label):
    for inventory_item in item._base_manager.filter(deleted_at__isnull=True):
        assignments = assignment_model._base_manager.filter(
            **{f"{item_field}_id": inventory_item.pk, "deleted_at__isnull": True}
        )
        for assignment in assignments:
            _check_assignment_shape(assignment, label)
            _check_source_stock(assignment, stock_model, item_field, label)
        total_stock = _sum_quantities(stock_model._base_manager.filter(**{f"{item_field}_id": inventory_item.pk}))
        target_only_qty = _sum_quantities(assignments.filter(from_location__isnull=True))
        available = total_stock - target_only_qty
        if available < 0:
            raise CommandError(
                f"Seed inventory invariant failed: {label} for {inventory_item.pk} has raw availability {available} "
                f"from {total_stock} on-hand and {target_only_qty} target-only units."
            )


def check_seed_inventory_invariants():
    """Fail closed when seeded inventory cannot represent a coherent stock balance.

    Stock rows contain current on-hand quantities. A source-backed accessory or
    consumable checkout has already deducted its quantity from that row, while a
    target-only checkout is accounted for by the item's available calculation.
    Component allocations are target-only and therefore remain part of the
    component-level deduction. The check intentionally validates raw balances
    instead of accepting the accessory/consumable ``max(0, ...)`` display clamp.
    """
    _check_stock_rows()
    _check_component_balances()
    for item, assignment_model, stock_model, item_field, label in (
        (Accessory, AccessoryAssignment, AccessoryStock, "accessory", "accessory assignment"),
        (Consumable, ConsumableAssignment, ConsumableStock, "consumable", "consumable consumption"),
    ):
        _check_item_balances(item, assignment_model, stock_model, item_field, label)


class SeedInventoryStockMixin:
    """Mixin for Command(BaseCommand).  Reads/writes self._ registries."""

    @staticmethod
    def _ensure_seed_stock(stock_model, lookup, minimum_qty):
        minimum_qty = max(0, minimum_qty)
        stock, _created = stock_model.objects.get_or_create(**lookup, defaults={"qty": minimum_qty})
        if stock.qty < minimum_qty:
            stock.qty = minimum_qty
            stock.save(update_fields=["qty"])
        return stock

    def _seed_inventory_catalog(self, catalog_tenant):
        self._accessories = {}
        for name, slug, mfr, cat, part, min_qty in self._accessory_defs:
            self._accessories[slug] = Accessory.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "manufacturer": self._manufacturers[mfr],
                    "category": self._categories[cat],
                    "part_number": part,
                    "min_qty": min_qty,
                    "tenant": catalog_tenant,
                },
            )[0]
        self._consumables = {}
        for name, slug, mfr, cat, part, min_qty in self._consumable_defs:
            self._consumables[slug] = Consumable.objects.get_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "manufacturer": self._manufacturers[mfr],
                    "category": self._categories[cat],
                    "part_number": part,
                    "min_qty": min_qty,
                    "tenant": catalog_tenant,
                },
            )[0]

    def _seed_accessory_consumable_stock(self):
        stock_count = 0
        for slug in self._tenants:
            locs = self._tenant_locations[slug]
            if not locs:
                continue
            loc = locs[0]
            for acc_slug in random.sample(list(self._accessories), k=4):
                # Deliberately leave a couple below min_qty to trigger low-stock alerts.
                qty = random.choice([0, 2, 3, 8, 12, 20])
                self._ensure_seed_stock(
                    AccessoryStock,
                    {"accessory": self._accessories[acc_slug], "location": loc},
                    qty,
                )
                stock_count += 1
            for con_slug in random.sample(list(self._consumables), k=2):
                self._ensure_seed_stock(
                    ConsumableStock,
                    {"consumable": self._consumables[con_slug], "location": loc},
                    random.choice([1, 4, 10, 25]),
                )
                stock_count += 1
        return stock_count

    def _infra_location(self, tenant_slug):
        for keyword in ("srv", "rack", "dc", "server", "network", "closet", "cabinet", "farm"):
            for location in self._tenant_locations.get(tenant_slug, []):
                if keyword in location.slug:
                    return location
        return None

    def _seed_component_allocation_pools(self, planned_component_qty):
        comp_count = 0
        # Components are global catalogue rows, so a tenant-scoped
        # ``component.available`` sees every allocation of the shared component
        # while only seeing the active tenant's stock. Every tenant-facing pool
        # therefore needs the global planned total; otherwise a tenant with no
        # allocation of its own could still render a negative availability.
        for tenant_slug, _tenant in self._tenants.items():
            location = self._infra_location(tenant_slug) or (self._tenant_locations.get(tenant_slug) or [None])[0]
            if location is None:
                raise CommandError(
                    f"Seed inventory invariant failed: tenant {tenant_slug} has no component stock location."
                )
            for comp_slug, planned_qty in planned_component_qty.items():
                comp = self._components[comp_slug]
                existing_allocated_qty = _sum_quantities(
                    ComponentAllocation._base_manager.filter(component=comp, deleted_at__isnull=True)
                )
                self._ensure_seed_stock(
                    ComponentStock,
                    {"component": comp, "location": location},
                    2 + max(planned_qty, existing_allocated_qty),
                )
                comp_count += 1
        return comp_count

    def _create_component_allocations(self):
        alloc_count = 0
        for comp_slug, qty, asset in getattr(self, "_component_allocation_plan", ()):
            alloc_count += int(
                self._seed_component_allocation(ComponentAllocation, self._components[comp_slug], qty, asset)
            )
        return alloc_count

    def _seed_component_stock(self):
        # Spare-parts (component) stock held at server rooms / DC racks. The MSP holds
        # the deepest spares pool; tenants with their own server location keep a few.
        comp_count = 0
        msp_loc = self._infra_location("northwind-internal-it") or self._tenant_locations["northwind-internal-it"][0]
        planned_component_qty = Counter()
        for comp_slug, qty, _asset in getattr(self, "_component_allocation_plan", ()):
            planned_component_qty[comp_slug] += qty
        for comp_slug, comp in self._components.items():
            existing_allocated_qty = _sum_quantities(
                ComponentAllocation._base_manager.filter(component=comp, deleted_at__isnull=True)
            )
            # Keep zero as a low-stock signal for components without allocations;
            # planned allocations are floored above zero by the required quantity.
            available_seed_qty = random.choice([2, 4, 5, 8, 12, 0])
            self._ensure_seed_stock(
                ComponentStock,
                {"component": comp, "location": msp_loc},
                available_seed_qty + max(planned_component_qty[comp_slug], existing_allocated_qty),
            )
            comp_count += 1

        # A shallower spares pool at customer server rooms.
        for tenant_slug in self._tenants:
            location = self._infra_location(tenant_slug)
            if not location or tenant_slug == "northwind-internal-it":
                continue
            for comp_slug in random.sample(list(self._components), k=random.randint(2, 4)):
                self._ensure_seed_stock(
                    ComponentStock,
                    {"component": self._components[comp_slug], "location": location},
                    random.choice([1, 2, 3, 4]),
                )
                comp_count += 1

        comp_count += self._seed_component_allocation_pools(planned_component_qty)
        return comp_count, self._create_component_allocations()

    def _seed_inventory_assignments(self):
        # Accessory/consumable issues to holders, drawn from each tenant's own
        # first-location pool. ADR-0001 phase 4 keeps this grant-free: the pool
        # at the customer location belongs to the customer even though the
        # catalogue item is MSP-owned.
        assign_count = 0
        for tenant_slug, holders in self._tenant_holders.items():
            locations = self._tenant_locations.get(tenant_slug) or []
            if not locations or not holders:
                continue
            location = locations[0]
            with TaskContext(tenant_id=location.tenant_id) as task_context:
                accessory_authorization = task_context.authorize_system(
                    permission="inventory.add_accessoryassignment",
                    operation="inventory.checkout",
                    reason="Seed approved same-tenant accessory assignments",
                )
                consumable_authorization = task_context.authorize_system(
                    permission="inventory.add_consumableassignment",
                    operation="inventory.checkout",
                    reason="Seed approved same-tenant consumable assignments",
                )
                for holder in random.sample(holders, k=min(8, len(holders))):
                    for acc_slug in random.sample(list(self._accessories), k=random.randint(1, 3)):
                        accessory = self._accessories[acc_slug]
                        if AccessoryAssignment._base_manager.filter(
                            accessory=accessory,
                            assigned_holder=holder,
                            from_location=location,
                            deleted_at__isnull=True,
                        ).exists():
                            continue
                        self._ensure_seed_stock(
                            AccessoryStock,
                            {"accessory": accessory, "location": location},
                            1,
                        )
                        checkout_inventory_item(
                            accessory,
                            1,
                            holder=holder,
                            source_location=location,
                            system_authorization=accessory_authorization,
                        )
                        assign_count += 1
                for holder in random.sample(holders, k=min(1, len(holders))):
                    consumable = self._consumables["aa-batteries-24"]
                    if ConsumableAssignment._base_manager.filter(
                        consumable=consumable,
                        assigned_holder=holder,
                        from_location=location,
                        deleted_at__isnull=True,
                    ).exists():
                        continue
                    self._ensure_seed_stock(
                        ConsumableStock,
                        {"consumable": consumable, "location": location},
                        1,
                    )
                    checkout_inventory_item(
                        consumable,
                        1,
                        holder=holder,
                        source_location=location,
                        system_authorization=consumable_authorization,
                    )
                    assign_count += 1
        return assign_count

    def _seed_inventory_kits(self):
        kits = [
            (
                "Developer Onboarding Kit",
                "northwind-internal-it",
                [("thinkpad-x1-carbon-g12", 1)],
                [("mx-master-3s", 1), ("mx-keys", 1), ("tb4-dock", 1)],
            ),
            (
                "Executive Onboarding Kit",
                "northwind-corporate",
                [("macbook-pro-16-2024", 1), ("iphone-15-pro", 1)],
                [("usb-c-charger-65w", 2), ("zone-wireless-2", 1)],
            ),
            (
                "Trading Desk Setup",
                "meridian-investment",
                [("macbook-pro-16-2024", 1)],
                [("dell-p2723de", 2), ("mx-master-3s", 1), ("tb4-dock", 1)],
            ),
            (
                "Field Technician Kit",
                "vantage-logistics",
                [("surface-pro-10", 1)],
                [("usb-c-charger-65w", 1), ("usb-c-hdmi-adapter", 1)],
            ),
        ]
        for name, tenant_slug, asset_type_items, accessory_items in kits:
            kit = Kit.objects.create(
                name=name, description=f"Standard provisioning bundle: {name}.", tenant=self._tenants[tenant_slug]
            )
            for asset_type_slug, qty in asset_type_items:
                KitItem.objects.create(kit=kit, asset_type=self._asset_types[asset_type_slug], qty=qty)
            for accessory_slug, qty in accessory_items:
                KitItem.objects.create(kit=kit, accessory=self._accessories[accessory_slug], qty=qty)
        return kits

    def _seed_inventory_stock(self):
        self.stdout.write("--- Inventory: stock & kits ---")
        catalog_tenant = self._tenants["northwind-internal-it"]
        self._seed_inventory_catalog(catalog_tenant)
        stock_count = self._seed_accessory_consumable_stock()
        comp_count, alloc_count = self._seed_component_stock()
        assign_count = self._seed_inventory_assignments()
        kits = self._seed_inventory_kits()
        self.stdout.write(
            f"  {len(self._accessories)} accessories, {len(self._consumables)} consumables, "
            f"{stock_count} accessory/consumable stock rows, {comp_count} component stock rows, "
            f"{alloc_count} component allocations, {assign_count} accessory/consumable assignments, "
            f"{len(kits)} kits."
        )
