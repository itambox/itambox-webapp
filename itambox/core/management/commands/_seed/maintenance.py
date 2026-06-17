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

TODAY = datetime.date.today()


def days_ago(n):
    return TODAY - datetime.timedelta(days=n)


class SeedMaintenanceMixin:
    """Mixin for Command(BaseCommand).  Reads/writes self._ registries."""

    def _seed_maintenance(self):
        from assets.models import AssetMaintenance
        self.stdout.write('--- Maintenance ---')
        sample = random.sample(self._assets, k=min(40, len(self._assets)))
        kinds = [('repair', 'Keyboard replacement under warranty', 0),
                 ('repair', 'Display hinge repair', 220),
                 ('upgrade', 'RAM upgrade to 64GB', 480),
                 ('hardware_support', 'Redundant PSU replacement', 1200),
                 ('software_support', 'Firmware / BIOS update', 0),
                 ('calibration', 'Annual RAID battery replacement', 450)]
        count = 0
        for asset in sample:
            mtype, note, cost = random.choice(kinds)
            start = asset.purchase_date + datetime.timedelta(days=random.randint(60, 500))
            if start > TODAY:
                start = days_ago(random.randint(10, 120))
            done = start + datetime.timedelta(days=random.randint(1, 5)) if random.random() < 0.7 else None
            AssetMaintenance.objects.create(
                asset=asset, title=f"{mtype.replace('_', ' ').title()} — {asset.name}",
                maintenance_type=mtype, supplier=self._suppliers[random.choice(self.HW_SUPPLIERS)],
                cost=cost, start_date=start, completion_date=done, notes=note)
            count += 1
        self.stdout.write(f'  {count} maintenance records.')
