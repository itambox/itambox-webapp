"""Drain settings: run the queue without spawning scheduled work.

Used by the issue-#445 deployment runbook's drain phase. It loads the full
production configuration and disables the qcluster scheduler so that
already-queued and already-scheduled work can be flushed without enqueueing
new schedule-driven tasks while workers are cut over.

Usage::

    DJANGO_SETTINGS_MODULE=core.settings.queue_drain manage.py qcluster
"""

from core.settings.prod import *  # noqa: F401,F403,F405 -- full production configuration

Q_CLUSTER = {**Q_CLUSTER, "scheduler": False}  # noqa: F405 -- single drain-only override
