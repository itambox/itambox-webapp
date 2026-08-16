"""Hourly coordination and tenant-isolated expiry tasks."""

from __future__ import annotations

import datetime
import logging

from django.apps import apps
from django.db import InterfaceError, OperationalError, transaction
from django.db.models import Case, F, Q, Value, When
from django.utils import timezone
from django.utils.module_loading import import_string
from django_q.models import Schedule
from django_q.tasks import async_task

from core.tasks.context import TaskContext
from core.tasks.utils import (
    RetryableTaskError,
    TaskResult,
    TaskStatus,
    TerminalTaskError,
    classify_task_error,
)

logger = logging.getLogger(__name__)

QUEUE_STALE_SECONDS = 60
LEASE_SECONDS = 600
RETRY_DELAYS = (60, 120, 240)
EXPIRY_TASK_PATH = "core.tasks.resource_grants.sweep_expired_resource_grants"
EXPIRY_COORDINATOR_PATH = "core.tasks.resource_grants.coordinate_resource_grant_expiry"
CODE_SUCCESS = "resource_grant_expiry_succeeded"
CODE_NO_DUE = "resource_grant_expiry_no_due"
CODE_PARTIAL = "resource_grant_expiry_partial"
CODE_DB_RETRY = "resource_grant_expiry_db_retry"
CODE_TENANT_UNRESOLVABLE = "resource_grant_expiry_tenant_unresolvable"
CODE_INVALID_GRANT = "resource_grant_expiry_invalid_grant"
CODE_TERMINAL = "resource_grant_expiry_terminal"
CODE_RETRY_EXHAUSTED = "resource_grant_expiry_retry_exhausted"
CODE_ENQUEUE_FAILED = "resource_grant_expiry_enqueue_failed"
CODE_STALE_DELIVERY = "resource_grant_expiry_stale_delivery"
SAFE_TERMINAL_CODES = frozenset(
    {
        CODE_INVALID_GRANT,
        CODE_TERMINAL,
        CODE_RETRY_EXHAUSTED,
        CODE_TENANT_UNRESOLVABLE,
    }
)
SAFE_RETRY_MESSAGE = "A transient database boundary interrupted the expiry sweep."
SAFE_TERMINAL_MESSAGE = "The expiry sweep stopped at a permanent boundary."
STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_ENQUEUE_FAILED = "enqueue_failed"
STATE_COMPLETE = "complete"
EXPIRY_PERMISSION = "organization.delete_tenantresourcegrant"
EXPIRY_OPERATION = "organization.resource_grant.expire"
EXPIRY_REASON = "Scheduled revocation of tenant resource grants whose valid_until deadline has elapsed."


def _model(name: str):
    return apps.get_model("organization", name)


def _revoke_resource_grant():
    return import_string("organization.services.resource_grants.revoke_resource_grant")


def _invalid_resource_grant_error():
    return import_string("organization.services.resource_grants.InvalidResourceGrantError")


def _schedule_slot(value: datetime.datetime) -> datetime.datetime:
    return value.astimezone(datetime.timezone.utc).replace(minute=0, second=0, microsecond=0)


def _delivery_kwargs(tenant_id: int, run_id: int, generation: int) -> dict[str, int]:
    return {"tenant_id": tenant_id, "run_id": run_id, "generation": generation}


def _mark_enqueue_failed(tenant_id: int, run_id: int, generation: int) -> None:
    run_model = _model("TenantResourceGrantExpiryRun")
    try:
        run_model._base_manager.filter(
            pk=run_id,
            tenant_id=tenant_id,
            state=STATE_QUEUED,
            generation=generation,
        ).update(
            state=STATE_ENQUEUE_FAILED,
            outcome=TaskStatus.RETRYABLE.value,
            error_code=CODE_ENQUEUE_FAILED,
            error_message="The expiry task could not be enqueued.",
            dispatch_stale_at=None,
            next_retry_at=None,
        )
    except (OperationalError, InterfaceError):
        logger.error(
            "Expiry task enqueue status could not be persisted",
            extra={
                "operation": "organization.resource_grants.expiry_sweep",
                "tenant_id": tenant_id,
                "run_id": run_id,
                "generation": generation,
                "error_code": CODE_ENQUEUE_FAILED,
            },
        )


def _enqueue_now(tenant_id: int, run_id: int, generation: int) -> None:
    try:
        async_task(EXPIRY_TASK_PATH, **_delivery_kwargs(tenant_id, run_id, generation))
    # broad except: boundary-isolation: persist only a stable redacted enqueue outcome
    except Exception as exc:
        logger.error(
            "Expiry task enqueue failed",
            extra={
                "operation": "organization.resource_grants.expiry_sweep",
                "tenant_id": tenant_id,
                "run_id": run_id,
                "generation": generation,
                "exception_type": type(exc).__name__,
                "error_code": CODE_ENQUEUE_FAILED,
            },
        )
        _mark_enqueue_failed(tenant_id, run_id, generation)


def _enqueue_once(tenant_id: int, run_id: int, generation: int, next_run: datetime.datetime) -> None:
    try:
        Schedule.objects.create(
            name=f"Resource grant expiry retry {run_id}/{generation}",
            func=EXPIRY_TASK_PATH,
            kwargs=repr(_delivery_kwargs(tenant_id, run_id, generation)),
            schedule_type=Schedule.ONCE,
            next_run=next_run,
        )
    # broad except: boundary-isolation: persist only a stable redacted enqueue outcome
    except Exception as exc:
        logger.error(
            "Expiry retry enqueue failed",
            extra={
                "operation": "organization.resource_grants.expiry_sweep",
                "tenant_id": tenant_id,
                "run_id": run_id,
                "generation": generation,
                "exception_type": type(exc).__name__,
                "error_code": CODE_ENQUEUE_FAILED,
            },
        )
        _mark_enqueue_failed(tenant_id, run_id, generation)


def _repair_run(run, now: datetime.datetime) -> tuple[int, int] | None:
    stale_queued = (
        run.state == STATE_QUEUED
        and (run.dispatch_stale_at is None or run.dispatch_stale_at <= now)
        and (run.next_retry_at is None or run.next_retry_at <= now)
    )
    failed_enqueue = run.state == STATE_ENQUEUE_FAILED
    stale_running = run.state == STATE_RUNNING and run.lease_expires_at is not None and run.lease_expires_at <= now
    if not (stale_queued or failed_enqueue or stale_running):
        return None

    next_generation = run.generation + 1
    run_model = _model("TenantResourceGrantExpiryRun")
    changed = run_model._base_manager.filter(
        pk=run.pk,
        tenant_id=run.tenant_id,
        state=run.state,
        generation=run.generation,
    ).update(
        state=STATE_QUEUED,
        outcome=TaskStatus.RETRYABLE.value if (failed_enqueue or stale_running) else None,
        generation=F("generation") + 1,
        dispatch_stale_at=now + datetime.timedelta(seconds=QUEUE_STALE_SECONDS),
        lease_expires_at=None,
        finished_at=None,
        next_retry_at=None,
    )
    return (run.pk, next_generation) if changed else None


def coordinate_resource_grant_expiry() -> TaskResult:
    """Create one hourly run per live tenant and repair stale deliveries."""

    cutoff = timezone.now()
    slot = _schedule_slot(cutoff)
    dispatched = 0
    repaired = 0
    tenant_model = _model("Tenant")
    run_model = _model("TenantResourceGrantExpiryRun")
    for tenant_id in tenant_model._base_manager.filter(deleted_at__isnull=True).values_list("pk", flat=True):
        with transaction.atomic():
            run, created = run_model._base_manager.get_or_create(
                tenant_id=tenant_id,
                schedule_slot=slot,
                defaults={
                    "cutoff": cutoff,
                    "state": STATE_QUEUED,
                    "generation": 1,
                    "dispatch_stale_at": cutoff + datetime.timedelta(seconds=QUEUE_STALE_SECONDS),
                },
            )
            delivery = (run.pk, run.generation) if created else _repair_run(run, cutoff)
            if delivery is not None:
                repaired += int(not created)
                dispatched += 1
                transaction.on_commit(
                    lambda tenant_id=tenant_id, run_id=delivery[0], generation=delivery[1]: _enqueue_now(
                        tenant_id, run_id, generation
                    )
                )
    return TaskResult(
        TaskStatus.SUCCESS,
        "resource_grant_expiry_coordinator_completed",
        counts={"dispatched": dispatched, "repaired": repaired},
    )


def _claim_run(tenant_id: int, run_id: int, generation: int, now: datetime.datetime) -> bool:
    lease = now + datetime.timedelta(seconds=LEASE_SECONDS)
    run_model = _model("TenantResourceGrantExpiryRun")
    return bool(
        run_model._base_manager.filter(
            pk=run_id,
            tenant_id=tenant_id,
            state=STATE_QUEUED,
            generation=generation,
        )
        .filter(Q(next_retry_at__isnull=True) | Q(next_retry_at__lte=now))
        .update(
            state=STATE_RUNNING,
            outcome=None,
            attempt_count=F("attempt_count") + 1,
            started_at=Case(
                When(started_at__isnull=True, then=Value(now)),
                default=F("started_at"),
            ),
            last_attempt_at=now,
            lease_expires_at=lease,
            error_code=None,
            error_message=None,
        )
    )


def _is_invalid_candidate(grant) -> bool:
    try:
        has_tenant = grant.grantee_tenant_id is not None
        has_group = grant.grantee_tenant_group_id is not None
        return not (has_tenant ^ has_group) or (
            grant.resource_type_id is None
            or f"{grant.resource_type.app_label}.{grant.resource_type.model}" not in grant.APPROVED_RESOURCE_MODELS
        )
    except AttributeError:
        return True


def _run_counts(run) -> dict[str, int]:
    evidence_model = _model("TenantResourceGrantExpiryRevocation")
    grant_model = _model("TenantResourceGrant")
    evidence = evidence_model._base_manager.integrity_valid().filter(run_id=run.pk)
    revoked_count = evidence.count()
    due = grant_model._base_manager.filter(
        tenant_id=run.tenant_id,
        deleted_at__isnull=True,
        valid_until__isnull=False,
        valid_until__lte=run.cutoff,
    ).select_related("resource_type")
    remaining_due_count = due.count()
    invalid_count = sum(1 for grant in due if _is_invalid_candidate(grant))
    return {
        "revoked": revoked_count,
        "remaining_due": remaining_due_count,
        "invalid": invalid_count,
    }


def _complete_run(
    run_id: int,
    generation: int,
    *,
    outcome: TaskStatus,
    code: str,
    message: str,
    counts: dict[str, int],
) -> bool:
    run_model = _model("TenantResourceGrantExpiryRun")
    changed = run_model._base_manager.filter(
        pk=run_id,
        state=STATE_RUNNING,
        generation=generation,
    ).update(
        state=STATE_COMPLETE,
        outcome=outcome.value,
        finished_at=timezone.now(),
        lease_expires_at=None,
        next_retry_at=None,
        dispatch_stale_at=None,
        revoked_count=counts["revoked"],
        remaining_due_count=counts["remaining_due"],
        invalid_count=counts["invalid"],
        error_code=code,
        error_message=message,
    )
    return bool(changed)


def _retry_or_exhaust(run_id: int, generation: int, error: BaseException, counts: dict[str, int]) -> str | None:
    run_model = _model("TenantResourceGrantExpiryRun")
    run = run_model._base_manager.filter(pk=run_id, generation=generation).first()
    if run is None or run.state != STATE_RUNNING:
        return None
    if run.attempt_count > len(RETRY_DELAYS):
        _complete_run(
            run_id,
            generation,
            outcome=TaskStatus.TERMINAL,
            code=CODE_RETRY_EXHAUSTED,
            message="The expiry sweep exhausted its finite retry policy.",
            counts=counts,
        )
        return "exhausted"

    now = timezone.now()
    next_retry = now + datetime.timedelta(seconds=RETRY_DELAYS[run.attempt_count - 1])
    with transaction.atomic():
        changed = run_model._base_manager.filter(
            pk=run_id,
            state=STATE_RUNNING,
            generation=generation,
        ).update(
            state=STATE_QUEUED,
            outcome=TaskStatus.RETRYABLE.value,
            generation=F("generation") + 1,
            next_retry_at=next_retry,
            dispatch_stale_at=next_retry + datetime.timedelta(seconds=QUEUE_STALE_SECONDS),
            lease_expires_at=None,
            error_code=CODE_DB_RETRY,
            error_message=SAFE_RETRY_MESSAGE,
            revoked_count=counts["revoked"],
            remaining_due_count=counts["remaining_due"],
            invalid_count=counts["invalid"],
        )
        if changed:
            transaction.on_commit(
                lambda: _enqueue_once(
                    run.tenant_id,
                    run_id,
                    generation + 1,
                    next_retry,
                )
            )
    return "retry" if changed else None


def _scope_failure(tenant_id: int, run_id: int, generation: int, code: str) -> TaskResult:
    _model("TenantResourceGrantExpiryRun")._base_manager.filter(
        pk=run_id,
        generation=generation,
    ).exclude(state=STATE_COMPLETE).update(
        state=STATE_COMPLETE,
        outcome=TaskStatus.TERMINAL.value,
        finished_at=timezone.now(),
        lease_expires_at=None,
        next_retry_at=None,
        dispatch_stale_at=None,
        error_code=code,
        error_message="The expiry task could not prove its tenant/run scope.",
    )
    logger.error(
        "Resource grant expiry task scope failure",
        extra={
            "operation": "organization.resource_grants.expiry_sweep",
            "tenant_id": tenant_id,
            "run_id": run_id,
            "generation": generation,
            "error_code": code,
        },
    )
    return TaskResult(TaskStatus.TERMINAL, code, message="The expiry task scope was not resolvable.")


def sweep_expired_resource_grants(tenant_id: int, run_id: int, generation: int) -> TaskResult:  # noqa: C901
    """Claim and sweep one owner tenant for one exact run generation."""

    run_model = _model("TenantResourceGrantExpiryRun")
    tenant_model = _model("Tenant")
    grant_model = _model("TenantResourceGrant")
    revoke_resource_grant = _revoke_resource_grant()
    invalid_resource_grant_error = _invalid_resource_grant_error()
    run = run_model._base_manager.select_related("tenant").filter(pk=run_id).first()
    if run is None:
        logger.error(
            "Resource grant expiry task run is missing",
            extra={
                "operation": "organization.resource_grants.expiry_sweep",
                "tenant_id": tenant_id,
                "run_id": run_id,
                "generation": generation,
                "error_code": CODE_TENANT_UNRESOLVABLE,
            },
        )
        return TaskResult(TaskStatus.TERMINAL, CODE_TENANT_UNRESOLVABLE)
    if run.tenant_id != tenant_id:
        return _scope_failure(tenant_id, run_id, generation, CODE_TENANT_UNRESOLVABLE)
    tenant = tenant_model._base_manager.filter(pk=tenant_id, deleted_at__isnull=True).first()
    if tenant is None:
        return _scope_failure(tenant_id, run_id, generation, CODE_TENANT_UNRESOLVABLE)

    if not _claim_run(tenant_id, run_id, generation, timezone.now()):
        return TaskResult(TaskStatus.SKIPPED, CODE_STALE_DELIVERY)

    claimed_generation = generation
    counts = {"revoked": 0, "remaining_due": 0, "invalid": 0}
    try:
        with TaskContext(
            tenant_id=tenant_id,
            user_id=None,
            operation="organization.resource_grants.expiry_sweep",
        ) as task_context:
            authorization = task_context.authorize_system(
                permission=EXPIRY_PERMISSION,
                operation=EXPIRY_OPERATION,
                reason=EXPIRY_REASON,
            )
            run = run_model._base_manager.get(pk=run_id)
            candidates = list(
                grant_model._base_manager.filter(
                    tenant_id=tenant_id,
                    deleted_at__isnull=True,
                    valid_until__isnull=False,
                    valid_until__lte=run.cutoff,
                ).values_list("pk", flat=True)
            )
            for grant_id in candidates:
                try:
                    result = revoke_resource_grant(
                        grant_id,
                        user=None,
                        active_tenant=tenant,
                        system_authorization=authorization,
                        cutoff=run.cutoff,
                        expiry_run=run,
                    )
                except invalid_resource_grant_error:
                    continue
                if result is not None:
                    counts["revoked"] += 1
            counts = _run_counts(run)

        if counts["invalid"] and not counts["revoked"]:
            _complete_run(
                run_id,
                claimed_generation,
                outcome=TaskStatus.TERMINAL,
                code=CODE_INVALID_GRANT,
                message="One or more due grants require reviewed remediation.",
                counts=counts,
            )
            raise TerminalTaskError(
                code=CODE_INVALID_GRANT,
                message="One or more due grants require reviewed remediation.",
            )
        if counts["invalid"]:
            outcome, code = TaskStatus.PARTIAL, CODE_PARTIAL
            message = "Some due grants require reviewed remediation."
        elif counts["revoked"]:
            outcome, code = TaskStatus.SUCCESS, CODE_SUCCESS
            message = ""
        else:
            outcome, code = TaskStatus.SKIPPED, CODE_NO_DUE
            message = ""
        _complete_run(
            run_id,
            claimed_generation,
            outcome=outcome,
            code=code,
            message=message,
            counts=counts,
        )
        return TaskResult(
            outcome,
            code,
            counts={
                "revoked": counts["revoked"],
                "remaining_due": counts["remaining_due"],
                "invalid": counts["invalid"],
            },
            message=message,
        )
    # broad except: task-isolation: classify and persist only a redacted outcome
    except Exception as exc:
        status = classify_task_error(exc)
        try:
            counts = _run_counts(
                run_model._base_manager.get(pk=run_id),
            )
        except (OperationalError, InterfaceError):
            counts = {"revoked": 0, "remaining_due": 0, "invalid": 0}
        candidate_error_code = getattr(exc, "code", None)
        error_code = (
            candidate_error_code
            if isinstance(candidate_error_code, str) and candidate_error_code in SAFE_TERMINAL_CODES
            else CODE_TERMINAL
        )
        if status is TaskStatus.RETRYABLE:
            try:
                retry_state = _retry_or_exhaust(run_id, claimed_generation, exc, counts)
                if retry_state == "exhausted":
                    raise TerminalTaskError(
                        code=CODE_RETRY_EXHAUSTED,
                        message="The expiry sweep exhausted its finite retry policy.",
                    ) from exc
                if retry_state == "retry":
                    logger.error(
                        "Resource grant expiry task retry boundary",
                        extra={
                            "operation": "organization.resource_grants.expiry_sweep",
                            "tenant_id": tenant_id,
                            "run_id": run_id,
                            "generation": claimed_generation,
                            "exception_type": type(exc).__name__,
                            "error_code": CODE_DB_RETRY,
                        },
                    )
            except (OperationalError, InterfaceError):
                logger.error(
                    "Resource grant expiry retry status could not be persisted",
                    extra={
                        "operation": "organization.resource_grants.expiry_sweep",
                        "tenant_id": tenant_id,
                        "run_id": run_id,
                        "generation": claimed_generation,
                        "exception_type": type(exc).__name__,
                        "error_code": CODE_DB_RETRY,
                    },
                )
            raise RetryableTaskError(code=CODE_DB_RETRY, message=SAFE_RETRY_MESSAGE) from exc

        try:
            _complete_run(
                run_id,
                claimed_generation,
                outcome=TaskStatus.TERMINAL,
                code=error_code if error_code.startswith("resource_grant_expiry_") else CODE_TERMINAL,
                message=SAFE_TERMINAL_MESSAGE,
                counts=counts,
            )
        except (OperationalError, InterfaceError):
            logger.error(
                "Resource grant expiry terminal status could not be persisted",
                extra={
                    "operation": "organization.resource_grants.expiry_sweep",
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                    "generation": claimed_generation,
                    "exception_type": type(exc).__name__,
                    "error_code": CODE_TERMINAL,
                },
            )
        logger.error(
            "Resource grant expiry task terminal boundary",
            extra={
                "operation": "organization.resource_grants.expiry_sweep",
                "tenant_id": tenant_id,
                "run_id": run_id,
                "generation": claimed_generation,
                "exception_type": type(exc).__name__,
                "error_code": error_code,
            },
        )
        raise TerminalTaskError(code=error_code, message=SAFE_TERMINAL_MESSAGE) from exc


sweep_resource_grant_expiry = coordinate_resource_grant_expiry
