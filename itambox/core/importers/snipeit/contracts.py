from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Callable, ClassVar, Literal, Protocol

from core.context import get_current_request_id
from core.errors import IntegrationContext, IntegrationError, IntegrationUnexpectedError

if TYPE_CHECKING:
    from .client import SnipeITClient

Outcome = Literal["created", "updated", "skipped"]
Severity = Literal["warning", "failure"]

logger = logging.getLogger("core.importers.snipeit")


@dataclass(frozen=True)
class ImportContext:
    client: SnipeITClient
    default_tenant: object | None
    user: object
    dry_run: bool
    update: bool
    map_companies: bool
    reporter: StageReporter


@dataclass
class ImportCounts:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0

    def record(self, outcome: Outcome) -> None:
        setattr(self, outcome, getattr(self, outcome) + 1)

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class StageIssue:
    severity: Severity
    operation: str
    error_code: str
    disposition: str


@dataclass
class StageResult:
    key: str
    counts: ImportCounts = field(default_factory=ImportCounts)
    issues: Counter[StageIssue] = field(default_factory=Counter)

    @property
    def warning_count(self) -> int:
        return sum(count for issue, count in self.issues.items() if issue.severity == "warning")


class ImportStage(Protocol):
    key: ClassVar[str]

    def run(self) -> StageResult:
        """Run the stage and return its result."""
        ...


class StageReporter:
    def __init__(
        self,
        stdout: Callable[[str], Any] | object | None = None,
        job: object | None = None,
        *,
        default_tenant: object | None,
        user: object,
    ) -> None:
        if stdout is not None and not callable(stdout):
            stdout = stdout.write
        self._stdout = stdout
        self._job = job
        self.default_tenant = default_tenant
        self.user = user

    def start(self, result: StageResult) -> None:
        self._emit(f"\n[{result.key}]")

    def row_failure(self, result: StageResult, operation: str, exc: Exception) -> IntegrationError:
        return self._issue(result, operation, exc, severity="failure", increments_failed=True)

    def warning(self, result: StageResult, operation: str, exc: Exception) -> IntegrationError:
        return self._issue(result, operation, exc, severity="warning", increments_failed=False)

    def finish(self, result: StageResult) -> None:
        counts = result.counts
        message = (
            f"  {result.key}: {counts.created} created, {counts.updated} updated, "
            f"{counts.skipped} skipped, {counts.failed} failed"
        )
        if result.warning_count:
            message += f", {result.warning_count} warnings"
        self._emit(message)

    def _issue(
        self,
        result: StageResult,
        operation: str,
        exc: Exception,
        *,
        severity: Severity,
        increments_failed: bool,
    ) -> IntegrationError:
        request_id = get_current_request_id()
        context = IntegrationContext(
            provider="snipe-it",
            operation=operation,
            tenant_id=getattr(self.default_tenant, "pk", None),
            actor_id=getattr(self.user, "pk", None),
            request_id=str(request_id) if request_id else None,
        )
        error = (
            exc
            if isinstance(exc, IntegrationError)
            else IntegrationUnexpectedError(context=context, cause_type=type(exc).__name__)
        )
        logger.warning("Snipe-IT import item degraded", extra=error.log_extra())
        message = f"  ! {operation}: one item could not be imported"
        self._write(message)
        if increments_failed:
            result.counts.failed += 1
        issue = StageIssue(
            severity=severity,
            operation=operation,
            error_code=error.cause_type or type(error).__name__,
            disposition=getattr(error.disposition, "value", str(error.disposition)),
        )
        result.issues[issue] += 1
        return error

    def _write(self, message: str) -> None:
        if self._stdout is not None:
            self._stdout(message)
        if self._job is not None:
            self._job.append_log(message)

    def _emit(self, message: str) -> None:
        self._write(message)
        logger.info(message)
