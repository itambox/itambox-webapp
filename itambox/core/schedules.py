"""Race-safe registration of django-q2 ``Schedule`` rows.

App configs register their periodic tasks from ``post_migrate`` handlers and
the scheduled-report flow registers one row per saved schedule. The obvious
``Schedule.objects.get_or_create(...)`` is *not* idempotent under concurrency:
django-q2's ``Schedule`` model has no unique constraint on ``func`` or on
``name`` (only the auto ``id`` is unique), so two concurrent/repeated
registrations can both miss the lookup and each insert a row, leaving
duplicate schedules that fire the same task more than once.

``register_schedule`` collapses every call to exactly one row per
``func`` (optionally narrowed to one ``name`` — scheduled reports reuse a
single task path with one row per schedule): it acquires a PostgreSQL
transaction-level advisory lock keyed on the ``func``/``name`` pair (so no two
concurrent transactions can enter the critical section simultaneously), locks
the existing rows, keeps the first, deletes any extras, and refreshes its
defaults — or creates a single row when none exist. The de-dupe makes the call
self-healing, so even a transient double-insert from a true create-race is
cleaned up on the next registration.

``remove_schedule`` takes the same advisory lock for the delete side, so a
concurrent registration and de-registration of the same identity serialize
instead of racing into a lost or duplicated row.
"""

import logging
import zlib

from django.db import DEFAULT_DB_ALIAS, connections, transaction

logger = logging.getLogger(__name__)

#: Identity of the scheduled-report task. Registration, de-registration, and
#: the ScheduledReport model all key off this one path so a rename cannot
#: leave one caller pointing at a stale task.
SCHEDULED_REPORT_TASK_PATH = "extras.tasks.reports.generate_scheduled_report_task"

#: Schedule kwarg the worker reads to claim each intended occurrence.
SCHEDULED_REPORT_FIRE_KWARG = "intended_fire_at"


def _advisory_lock_key(func, name):
    """Deterministic signed 64-bit advisory-lock key for a schedule identity.

    zlib.crc32 is deterministic across processes (unlike hash()); the result is
    masked into the signed 64-bit range required by pg_advisory_xact_lock.
    """
    lock_key = zlib.crc32(f"{func}\x00{name or ''}".encode()) & 0xFFFFFFFF
    if lock_key > 0x7FFFFFFF:
        lock_key -= 0x100000000
    return lock_key


def register_schedule(func, *, name=None, defaults=None, using=DEFAULT_DB_ALIAS):
    """Idempotently register a single django-q2 ``Schedule``.

    Keyed on ``func``, optionally narrowed to a single ``name`` (the per-row
    identity of scheduled reports). Safe to call repeatedly and from
    concurrent processes. Never raises: any failure (e.g. the schedule table
    not yet migrated) is logged and swallowed so a ``post_migrate`` handler
    can't abort ``migrate``.

    ``using`` names the database alias to register on; it defaults to the
    default alias so existing callers keep their behavior, and a ``post_migrate``
    handler can pass the alias the migration actually ran against.

    Returns the surviving ``Schedule`` instance, or ``None`` on failure.
    """
    defaults = defaults or {}
    try:
        # inline import: app-registry: avoid AppRegistryNotReady at app-load time
        from django_q.models import Schedule

        connection = connections[using]
        schedules = Schedule.objects.using(using)
        with transaction.atomic(using=using):
            # Acquire a transaction-level advisory lock keyed on the schedule
            # identity (func + optional name). select_for_update().filter()
            # only locks *existing* rows, so two concurrent first-inserts would
            # both see an empty queryset and both proceed to create — the
            # advisory lock prevents that by serializing the entire
            # check-and-create block on a per-identity basis.
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", [_advisory_lock_key(func, name)])

            # Lock existing rows for this identity so concurrent registrations
            # of an already-present schedule serialize rather than racing.
            query = schedules.select_for_update().filter(func=func)
            if name is not None:
                query = query.filter(name=name)
            existing = list(query.order_by("id"))
            if existing:
                schedule = existing[0]
                # Collapse any duplicates left by a previous racy registration.
                duplicates = existing[1:]
                if duplicates:
                    schedules.filter(pk__in=[s.pk for s in duplicates]).delete()
                    logger.warning(
                        "Removed %d duplicate schedule row(s) for func=%s name=%s",
                        len(duplicates),
                        func,
                        name,
                    )
                changed = []
                for key, value in defaults.items():
                    if getattr(schedule, key) != value:
                        setattr(schedule, key, value)
                        changed.append(key)
                if changed:
                    schedule.save(update_fields=changed, using=using)
                return schedule

            return schedules.create(func=func, **({"name": name} if name is not None else {}), **defaults)
    # broad except: availability-tradeoff: a failed registration must never abort the
    # caller's save or a post_migrate run; the next save re-registers the schedule
    except Exception as exc:
        logger.warning("Failed to register schedule for func=%s name=%s: %s", func, name, exc)
        return None


def remove_schedule(func, *, name=None, using=DEFAULT_DB_ALIAS):
    """Idempotently delete the ``Schedule`` row(s) for an identity.

    Takes the same advisory lock as ``register_schedule`` so a concurrent
    registration and removal of the same identity serialize. Never raises:
    failures are logged and reported as 0 removed rows.
    """
    try:
        # inline import: app-registry: avoid AppRegistryNotReady at app-load time
        from django_q.models import Schedule

        connection = connections[using]
        schedules = Schedule.objects.using(using)
        with transaction.atomic(using=using):
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", [_advisory_lock_key(func, name)])
            query = schedules.filter(func=func)
            if name is not None:
                query = query.filter(name=name)
            removed = query.count()
            if removed:
                query.delete()
            return removed
    # broad except: availability-tradeoff: a failed removal must never abort a delete flow;
    # a surviving orphaned row is collapsed by the next registration
    except Exception as exc:
        logger.warning("Failed to remove schedule for func=%s name=%s: %s", func, name, exc)
        return 0
