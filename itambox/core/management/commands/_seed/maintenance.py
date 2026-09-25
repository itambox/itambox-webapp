"""Maintenance seed mixin: asset maintenance records.

Designed to be mixed into ``Command`` in seed_data.py:

    from core.management.commands._seed.maintenance import SeedMaintenanceMixin

    class Command(SeedMaintenanceMixin, BaseCommand):
        ...

``_seed_maintenance`` must run after ``_seed_assets`` (it reads
``self._assets`` / ``self._suppliers`` and the ``HW_SUPPLIERS`` class attribute
provided by ``SeedAssetsMixin``).
"""

import datetime
import random

from assets.models import AssetMaintenance
from assets.models.choices import MaintenanceStatusChoices
from assets.models.episode import RepairEpisode

TODAY = datetime.date.today()

#: ``(maintenance_type, note, cost)`` seeds. Every entry is a *closed* piece of
#: work: the seed only writes records that a service provider has finished, so a
#: record can never claim a repair that the asset's own status timeline denies.
KINDS = [
    ("repair", "Keyboard replacement under warranty", 0),
    ("repair", "Display hinge repair", 220),
    ("upgrade", "RAM upgrade to 64GB", 480),
    ("hardware_support", "Redundant PSU replacement", 1200),
    ("software_support", "Firmware / BIOS update", 0),
    ("calibration", "Annual RAID battery replacement", 450),
]

#: Maintenance types that describe work the product models as taking the asset out
#: of service. A record of one of these must be backed by a repair episode (and,
#: where one exists, by the matching status transition) so the asset's timeline
#: and its records tell one story (#506).
OUT_OF_SERVICE_TYPES = {"repair", "calibration"}


def days_ago(n):
    return TODAY - datetime.timedelta(days=n)


class SeedMaintenanceMixin:
    """Mixin for Command(BaseCommand).  Reads/writes self._ registries."""

    def _seed_maintenance(self):
        self.stdout.write("--- Maintenance ---")
        sample = random.sample(self._assets, k=min(40, len(self._assets)))
        count = 0
        for asset in sample:
            mtype, note, cost = random.choice(KINDS)
            start = asset.purchase_date + datetime.timedelta(days=random.randint(60, 500))
            if start > TODAY:
                start = days_ago(random.randint(10, 120))
            done = start + datetime.timedelta(days=random.randint(1, 5))
            kwargs = dict(
                asset=asset,
                maintenance_type=mtype,
                supplier=self._suppliers[random.choice(self.HW_SUPPLIERS)],
                cost=cost,
                start_date=start,
                completion_date=done,
                status=MaintenanceStatusChoices.COMPLETED,
                notes=note,
            )
            # A repair/calibration record is the paperwork of an out-of-service
            # episode, so it gets a RepairEpisode to belong to. Grouping the story
            # this way is what #533 added the episode for, and it lets the asset
            # timeline show the record and the status changes as one unit instead
            # of a "repair" on an asset that never left service. The episode's
            # only free-text field is ``notes``, so the story is described there.
            if mtype in OUT_OF_SERVICE_TYPES:
                episode, _created = RepairEpisode.objects.get_or_create(
                    asset=asset,
                    notes=f"{note} — service window {start:%Y-%m-%d} to {done:%Y-%m-%d}.",
                )
                kwargs["episode"] = episode
            AssetMaintenance.objects.create(**kwargs)
            count += 1
        self.stdout.write(f"  {count} maintenance records.")
