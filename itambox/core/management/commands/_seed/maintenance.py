"""Maintenance seed mixin: asset maintenance records.

Designed to be mixed into ``Command`` in seed_data.py:

    from core.management.commands._seed.maintenance import SeedMaintenanceMixin

    class Command(SeedMaintenanceMixin, BaseCommand):
        ...

``_seed_maintenance`` must run after ``_simulate_history``: it reads
``self._assets`` / ``self._suppliers`` / ``self.HW_SUPPLIERS`` from the assets
mixin and ``self._repair_windows`` from the history mixin.

The ordering is the point of the phase. #506 reported "repair" records on assets
whose own status timeline never left deployable state. Rather than inventing repair
records and hoping they look plausible, out-of-service work is derived from the
repair windows the history simulation actually produced: every ``repair`` or
``calibration`` record documents a real repair, dated inside its window, on the
asset that really went out of service. In-service work (upgrades, firmware
updates, support contracts) never takes a unit out of service, so it stays freely
seeded.

The maintenance record is the anchor of that story (#644): the seeded stand-in loan
of the window and the disposal that closed the unit out are linked to it, so the
asset timeline groups them under the repair.
"""

import datetime
import random

from assets.models import AssetAssignment, AssetDisposal, AssetMaintenance
from assets.models.choices import MaintenanceStatusChoices

TODAY = datetime.date.today()


def link_repair_records(maintenance, window) -> int:
    """Link the seeded loan and disposal of one repair window to its maintenance (#644).

    Records that are already linked, or that lie outside the window, are left
    untouched. Returns the number of links written.
    """
    start = window["start"]
    end = window["end"]
    return _link_window_loans(maintenance, window.get("holder_id"), start, end) + _link_window_disposals(
        maintenance, start, end
    )


def _link_window_loans(maintenance, holder_id, start, end) -> int:
    """Attach the stand-in loan the repaired unit's holder had during the window."""
    if not holder_id:
        return 0
    loans = (
        AssetAssignment.objects.filter(is_loan=True, maintenance__isnull=True, assigned_user_id=holder_id)
        .exclude(asset=maintenance.asset)
        .select_related("asset")
    )
    linked = 0
    for loan in loans:
        returned = loan.checked_in_at.date() if loan.checked_in_at else end
        if loan.checked_out_at.date() > end or returned < start:
            continue
        loan.maintenance = maintenance
        loan.save(update_fields=["maintenance", "updated_at"])
        linked += 1
    return linked


def _link_window_disposals(maintenance, start, end) -> int:
    """Attach the disposal that closed the repaired unit out inside the window."""
    linked = 0
    disposals = AssetDisposal.objects.filter(
        asset=maintenance.asset, maintenance__isnull=True, disposal_date__range=(start, end)
    )
    for disposal in disposals:
        disposal.maintenance = maintenance
        disposal.save(update_fields=["maintenance", "updated_at"])
        linked += 1
    return linked


#: In-service work: no status transition, so no repair window.
#: ``(maintenance_type, note, cost)``.
IN_SERVICE_KINDS = [
    ("upgrade", "RAM upgrade to 64GB", 480),
    ("hardware_support", "Redundant PSU replacement", 1200),
    ("software_support", "Firmware / BIOS update", 0),
]

#: Out-of-service work, documented against a real repair window.
#: ``(maintenance_type, note, cost)``.
OUT_OF_SERVICE_KINDS = [
    ("repair", "Keyboard replacement under warranty", 0),
    ("repair", "Display hinge repair", 220),
    ("calibration", "Annual RAID battery replacement", 450),
]


def days_ago(n):
    return TODAY - datetime.timedelta(days=n)


class SeedMaintenanceMixin:
    """Mixin for Command(BaseCommand).  Reads/writes self._ registries."""

    def _maintenance_supplier(self):
        return self._suppliers[random.choice(self.HW_SUPPLIERS)]

    def _seed_maintenance(self):
        self.stdout.write("--- Maintenance ---")
        count = self._seed_out_of_service_maintenance()
        count += self._seed_in_service_maintenance()
        self.stdout.write(f"  {count} maintenance records.")

    def _seed_out_of_service_maintenance(self):
        """Document the repair windows the history simulation produced (#506).

        One record per real repair window, dated inside that window, so the
        maintenance paperwork and the asset's status transitions tell the same
        story. The record itself is what the asset Timeline tab groups the window's
        loan and disposal under (#644). Only windows that were actually written to
        the change log are published by the history phase, so a record can never
        exist without a matching repair transition.
        """
        count = 0
        for window in getattr(self, "_repair_windows", []):
            mtype, note, cost = random.choice(OUT_OF_SERVICE_KINDS)
            start = window["start"]
            end = window["end"]
            # Keep the service window strictly inside the out-of-service period so a
            # record cannot claim work that happened after the unit returned to use.
            span = max((end - start).days, 1)
            done = start + datetime.timedelta(days=min(span - 1, random.randint(1, 5)))
            if done < start:
                done = start
            maintenance = AssetMaintenance.objects.create(
                asset=window["asset"],
                maintenance_type=mtype,
                supplier=self._maintenance_supplier(),
                cost=cost,
                start_date=start,
                completion_date=done,
                status=MaintenanceStatusChoices.COMPLETED,
                notes=f"{note} — out of service {start:%Y-%m-%d} to {end:%Y-%m-%d}.",
            )
            link_repair_records(maintenance, window)
            count += 1
        return count

    def _seed_in_service_maintenance(self):
        """Seed work that never takes the unit out of service."""
        sample = random.sample(self._assets, k=min(40, len(self._assets)))
        count = 0
        for asset in sample:
            mtype, note, cost = random.choice(IN_SERVICE_KINDS)
            start = asset.purchase_date + datetime.timedelta(days=random.randint(60, 500))
            if start > TODAY:
                start = days_ago(random.randint(10, 120))
            done = start + datetime.timedelta(days=random.randint(1, 5))
            AssetMaintenance.objects.create(
                asset=asset,
                maintenance_type=mtype,
                supplier=self._maintenance_supplier(),
                cost=cost,
                start_date=start,
                completion_date=done,
                status=MaintenanceStatusChoices.COMPLETED,
                notes=note,
            )
            count += 1
        return count
