"""Race-safe registration of django-q2 ``Schedule`` rows.

App configs register their periodic tasks from ``post_migrate`` handlers. The
obvious ``Schedule.objects.get_or_create(func=...)`` is *not* idempotent under
concurrency: django-q2's ``Schedule`` model has no unique constraint on ``func``
(only the auto ``id`` is unique), so two concurrent/repeated registrations can
both miss the lookup and each insert a row, leaving duplicate schedules that
fire the same task more than once.

``register_schedule`` collapses every call to exactly one row per ``func``: it
locks the existing rows, keeps the first, deletes any extras, and refreshes its
defaults — or creates a single row when none exist. The de-dupe makes the call
self-healing, so even a transient double-insert from a true create-race is
cleaned up on the next registration.
"""

import logging

from django.db import transaction

logger = logging.getLogger(__name__)


def register_schedule(func, *, defaults=None):
    """Idempotently register a single django-q2 ``Schedule`` keyed on ``func``.

    Safe to call repeatedly and from concurrent processes. Never raises: any
    failure (e.g. the schedule table not yet migrated) is logged and swallowed
    so a ``post_migrate`` handler can't abort ``migrate``.

    Returns the surviving ``Schedule`` instance, or ``None`` on failure.
    """
    defaults = defaults or {}
    try:
        # inline import: avoid AppRegistryNotReady at app-load time
        from django_q.models import Schedule

        with transaction.atomic():
            # Lock existing rows for this func so concurrent registrations of an
            # already-present schedule serialize rather than racing.
            existing = list(
                Schedule.objects.select_for_update()
                .filter(func=func)
                .order_by('id')
            )
            if existing:
                schedule = existing[0]
                # Collapse any duplicates left by a previous racy registration.
                duplicates = existing[1:]
                if duplicates:
                    Schedule.objects.filter(
                        pk__in=[s.pk for s in duplicates]
                    ).delete()
                    logger.warning(
                        "Removed %d duplicate schedule row(s) for func=%s",
                        len(duplicates), func,
                    )
                changed = []
                for key, value in defaults.items():
                    if getattr(schedule, key) != value:
                        setattr(schedule, key, value)
                        changed.append(key)
                if changed:
                    schedule.save(update_fields=changed)
                return schedule

            return Schedule.objects.create(func=func, **defaults)
    except Exception as exc:
        logger.warning("Failed to register schedule for func=%s: %s", func, exc)
        return None
