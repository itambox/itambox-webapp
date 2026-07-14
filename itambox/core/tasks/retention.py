import logging

from django.core.management import call_command

logger = logging.getLogger(__name__)


def prune_changelog_task():
    """Scheduled daily task: prune aged changelog/operational-data rows.

    Registered as a daily django-q2 Schedule by
    ``CoreConfig._register_prune_schedule`` (core/apps.py). Delegates to the
    ``prune_changelog`` management command (core/management/commands/
    prune_changelog.py) so the CLI and the scheduled run share one
    implementation. Runs with the command's defaults: every configured
    ITAMBOX_*_RETENTION_DAYS setting, no --tenant filter (all tenants + global
    rows), no --dry-run, no --archive-dir.

    Exceptions are intentionally NOT swallowed here: django-q2 records an
    uncaught exception as a Failure row (visible via list_failed_tasks), which
    is the same visibility mechanism the rest of the scheduled tasks in this
    module rely on -- swallowing it would make a broken nightly prune silently
    invisible instead.
    """
    logger.info("Starting scheduled prune_changelog run.")
    call_command('prune_changelog')
    logger.info("Scheduled prune_changelog run complete.")
