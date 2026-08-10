import logging

from django.core.management import call_command

from core.tasks.utils import RetryableTaskError, TaskResult, TaskStatus, TerminalTaskError, classify_task_error

logger = logging.getLogger(__name__)


def prune_changelog_task() -> TaskResult:
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
    try:
        call_command("prune_changelog")
    # broad except: cleanup-reraise: classify the queue-visible failure without swallowing it
    except Exception as exc:
        error_type = RetryableTaskError if classify_task_error(exc) is TaskStatus.RETRYABLE else TerminalTaskError
        logger.error(
            "Scheduled prune_changelog failed",
            extra={"operation": "retention.prune_changelog", "exception_type": type(exc).__name__},
        )
        raise error_type(code="retention.prune_failed", message="Scheduled retention pruning failed.") from exc
    logger.info("Scheduled prune_changelog run complete.")
    return TaskResult(TaskStatus.SUCCESS, "retention.prune_completed")
