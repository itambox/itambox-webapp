"""SeedHistoryMixin — drives realistic, back-dated MSP change history.

Replaces the old hand-written ``_seed_changelog`` with engine-powered saves so
every ObjectChange carries a genuine diff produced by the same serializer the
live application uses.

Wire-up (in seed_data.py):
1. Import and add to Command's base classes:
       from core.management.commands._seed.history import SeedHistoryMixin
       class Command(SeedHistoryMixin, BaseCommand): ...
2. In ``_seed_all``, replace ``self._seed_changelog()`` with
       self._engine = ChangeLogEngine(stdout=self.stdout, style=self.style)
       self._simulate_history()
3. Delete (or leave inert) the old ``_seed_changelog`` method.
"""

import datetime
import random


class SeedHistoryMixin:
    """Mixin for the seed ``Command``.  Requires ``self._engine`` (a
    ``ChangeLogEngine`` instance) to be set before ``_simulate_history`` is
    called.  All other dependencies are the standard seed ``self.*`` registries.
    """

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _rand_date(self, start: datetime.date, end: datetime.date) -> datetime.date:
        """Return a random date in [start, end], clamped so start <= end."""
        if start >= end:
            return start
        delta = (end - start).days
        return start + datetime.timedelta(days=random.randint(0, delta))

    def _sl(self, slug: str):
        """Fetch a StatusLabel by slug from self._status_labels (never raises)."""
        return self._status_labels.get(slug)

    def _pick_actor(self, actors, helpdesk=None):
        pool = helpdesk if (helpdesk and random.random() < 0.35) else actors
        return random.choice(pool)

    # ──────────────────────────────────────────────────────────────────────────
    # Main entry point
    # ──────────────────────────────────────────────────────────────────────────

    def _simulate_history(self):  # noqa: C901  (complex-but-linear, keep together)
        """Generate a ~2-year MSP change history through the real change-log engine.

        The engine drives actual ``snapshot() → mutate → save()`` calls wrapped in
        ``TaskContext``, so every ObjectChange has a genuine prechange/postchange
        diff — no hand-written partial dicts.

        Steps:
        1. Aging pass — back-date *all* assets, licenses and subscriptions to their
           purchase/start dates so the whole dataset looks organically grown.
        2. Per-tenant sample — provisioning, checkout, mid-life edits, repair
           cycles, and audits for up to 20 assets per tenant.
        3. Retired assets — decommission entry for each.
        4. Licenses — create + optional seat-count bump.
        5. Subscriptions — renewal (cost + date update).
        """
        engine = self._engine

        today = datetime.date.today()

        actors = self._engineer_users or [getattr(self, '_provisioner', None)]
        actors = [a for a in actors if a is not None]
        if not actors:
            actors = list(self._users.values())[:3]

        helpdesk = [u for name, u in self._users.items()
                    if name in ('ravi.anand', 'mia.koch')]
        if not helpdesk:
            helpdesk = actors[:2]

        provisioner = getattr(self, '_provisioner', None) or actors[0]

        # ── Status label shortcuts ────────────────────────────────────────────
        sl_available = self._sl('available')
        sl_in_use = self._sl('in-use')
        sl_pending_repair = self._sl('pending-repair')
        sl_retired = self._sl('retired')

        # ── 1. Aging pass: touch created_at/updated_at for all entities ───────
        self.stdout.write('--- Change history (engine-driven) ---')

        for asset in self._assets:
            when = asset.purchase_date or today - datetime.timedelta(days=365)
            engine.touch_created(asset, when)

        for lic in self._licenses:
            when = lic.purchase_date or today - datetime.timedelta(days=365)
            engine.touch_created(lic, when)

        for sub in self._subscriptions:
            when = sub.start_date or today - datetime.timedelta(days=365)
            engine.touch_created(sub, when)

        # ── 2. Per-tenant asset lifecycle ─────────────────────────────────────
        mid_life_notes = [
            'Re-imaged and re-enrolled in MDM.',
            'BIOS/firmware updated to latest vendor release.',
            'RAM upgraded to 32 GB for performance.',
            'Relocated during office move to new floor.',
            'Warranty extended by 12 months via support contract.',
            'Enrolled in new endpoint protection policy.',
            'SSD replaced under warranty; data migration complete.',
            'Asset audited and label reprinted (old label damaged).',
            'Intune profile reapplied after OS reinstall.',
            'Network adapter replaced following intermittent faults.',
            'Bitlocker key rotated per security policy.',
            'Assigned to replacement pool after user departure.',
            'Bluetooth/NFC disabled per hardening policy.',
        ]

        for slug in self._tenants:
            assets = self._assets_by_tenant.get(slug, [])
            if not assets:
                continue

            sample = random.sample(assets, k=min(20, len(assets)))

            for asset in sample:
                p_date = asset.purchase_date or today - datetime.timedelta(days=400)

                # ── a) provisioning create entry ──────────────────────────────
                actor = self._pick_actor(actors)
                engine.log_create(asset, when=p_date, user=actor)

                # ── b) checkout ~7 days after purchase if actively assigned ───
                active = asset.assignments.filter(
                    is_active=True, assigned_user__isnull=False
                ).first()
                checkout_date = p_date + datetime.timedelta(days=random.randint(3, 14))
                if checkout_date > today:
                    checkout_date = today - datetime.timedelta(days=1)

                if active and sl_in_use and asset.status != sl_in_use:
                    engine.change(
                        asset,
                        when=checkout_date,
                        user=self._pick_actor(actors),
                        action='checkout',
                        status=sl_in_use,
                    )
                elif active and sl_in_use:
                    # status already in-use, just log a checkout action note
                    pass

                # ── c) ~30 % mid-life edit ────────────────────────────────────
                if random.random() < 0.30:
                    window_start = p_date + datetime.timedelta(days=30)
                    window_end = today - datetime.timedelta(days=15)
                    edit_date = self._rand_date(window_start, window_end)
                    new_note = random.choice(mid_life_notes)
                    # Only update if the note differs (engine skips no-ops)
                    if asset.notes != new_note:
                        engine.change(
                            asset,
                            when=edit_date,
                            user=self._pick_actor(actors, helpdesk),
                            action='update',
                            notes=new_note,
                        )

                # ── d) ~20 % repair cycle ─────────────────────────────────────
                if random.random() < 0.20 and sl_pending_repair:
                    window_start = p_date + datetime.timedelta(days=60)
                    window_end = today - datetime.timedelta(days=60)
                    repair_start = self._rand_date(window_start, window_end)
                    repair_end = repair_start + datetime.timedelta(
                        days=random.randint(7, 30)
                    )
                    if repair_end > today:
                        repair_end = today - datetime.timedelta(days=1)

                    # into repair — only if asset isn't already in that status
                    if asset.status != sl_pending_repair:
                        engine.change(
                            asset,
                            when=repair_start,
                            user=self._pick_actor(actors, helpdesk),
                            action='update',
                            status=sl_pending_repair,
                        )
                    # Returned to the deployable pool after repair. The
                    # 'pending-repair' label's type is 'pending', and
                    # pending -> deployed is an illegal transition, so the legal
                    # path back into service is via 'available' (deployable).
                    if sl_available and asset.status != sl_available:
                        engine.change(
                            asset,
                            when=repair_end,
                            user=self._pick_actor(actors),
                            action='update',
                            status=sl_available,
                        )

                # ── e) ~25 % physical audit ───────────────────────────────────
                has_last_audited = any(
                    f.name == 'last_audited'
                    for f in asset._meta.fields
                )
                if has_last_audited and random.random() < 0.25:
                    audit_date = self._rand_date(
                        today - datetime.timedelta(days=90),
                        today - datetime.timedelta(days=1),
                    )
                    from django.utils import timezone as _tz
                    audit_dt = _tz.make_aware(
                        datetime.datetime(
                            audit_date.year, audit_date.month, audit_date.day,
                            random.randint(8, 17), random.randint(0, 59),
                        ),
                        _tz.get_current_timezone(),
                    )
                    if asset.last_audited != audit_dt:
                        engine.change(
                            asset,
                            when=audit_date,
                            user=self._pick_actor(actors, helpdesk),
                            action='audit',
                            last_audited=audit_dt,
                        )

        # ── 3. Retired assets: decommission entry ─────────────────────────────
        for asset in self._retired_assets:
            if sl_retired and asset.status != sl_retired:
                window_start = (asset.purchase_date or today - datetime.timedelta(days=500)) \
                               + datetime.timedelta(days=180)
                window_end = today - datetime.timedelta(days=10)
                decom_date = self._rand_date(window_start, window_end)
                engine.change(
                    asset,
                    when=decom_date,
                    user=self._pick_actor(actors),
                    action='update',
                    status=sl_retired,
                    notes='Decommissioned — end of useful life. Disposed via certified e-waste vendor.',
                )

        # ── 4. Licenses: create log + optional seat bump ──────────────────────
        for lic in random.sample(self._licenses, k=min(30, len(self._licenses))):
            p_date = lic.purchase_date or today - datetime.timedelta(days=365)
            engine.log_create(lic, when=p_date, user=provisioner)

            if random.random() < 0.40:
                bump_date = self._rand_date(
                    p_date + datetime.timedelta(days=60),
                    today - datetime.timedelta(days=14),
                )
                bump = random.randint(5, 25)
                new_seats = lic.seats + bump
                engine.change(
                    lic,
                    when=bump_date,
                    user=self._pick_actor(actors),
                    action='update',
                    seats=new_seats,
                )

        # ── 5. Subscriptions: renewal (cost + date bump) ──────────────────────
        for sub in random.sample(
            self._subscriptions, k=min(15, len(self._subscriptions))
        ):
            if sub.renewal_cost is None or sub.renewal_date is None:
                continue

            renewal_log_date = self._rand_date(
                today - datetime.timedelta(days=60),
                today - datetime.timedelta(days=3),
            )
            # Bump cost by a realistic 3–12 % price increase
            new_cost = round(float(sub.renewal_cost) * random.uniform(1.03, 1.12), 2)
            # Advance the renewal date by one year
            try:
                new_renewal_date = sub.renewal_date.replace(
                    year=sub.renewal_date.year + 1
                )
            except ValueError:
                # Feb 29 edge case
                new_renewal_date = sub.renewal_date + datetime.timedelta(days=365)

            # Only apply if both values actually change (engine skips no-ops)
            if new_cost != float(sub.renewal_cost):
                engine.change(
                    sub,
                    when=renewal_log_date,
                    user=self._pick_actor(actors, helpdesk),
                    action='update',
                    renewal_cost=new_cost,
                    renewal_date=new_renewal_date,
                )

        # ── Summary ───────────────────────────────────────────────────────────
        self.stdout.write(
            f'  {engine.count} authentic change-history entries across '
            f'assets, licenses and subscriptions.'
        )
