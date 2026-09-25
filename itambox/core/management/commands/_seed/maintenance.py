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
``calibration`` record documents a real repair episode, dated inside its repair
window, on the asset that really went out of service. In-service work (upgrades,
firmware updates, support contracts) never takes a unit out of service, so it stays
freely seeded and carries no episode.
"""

import datetime
import random

from assets.models import AssetMaintenance
from assets.models.choices import MaintenanceStatusChoices
from assets.models.episode import RepairEpisode

TODAY = datetime.date.today()

#: In-service work: no status transition, so no repair window and no episode.
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
        story. The record and the window share a RepairEpisode, which is what the
        asset Timeline tab groups them by. Only windows that were actually written
        to the change log are published by the history phase, so a record can never
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
            episode, _created = RepairEpisode.objects.get_or_create(
                asset=window["asset"],
                notes=f"{note} — out of service {start:%Y-%m-%d} to {end:%Y-%m-%d}.",
            )
            AssetMaintenance.objects.create(
                asset=window["asset"],
                episode=episode,
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
