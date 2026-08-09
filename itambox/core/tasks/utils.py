from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping

from django.db import InterfaceError, OperationalError
from django.urls import NoReverseMatch, reverse


class TaskStatus(StrEnum):
    SUCCESS = "success"
    PARTIAL = "partial"
    SKIPPED = "skipped"
    RETRYABLE = "retryable"
    TERMINAL = "terminal"


@dataclass(frozen=True)
class TaskResult:
    """Immutable, payload-free outcome returned by background-task boundaries."""

    status: TaskStatus
    code: str
    counts: Mapping[str, int] = field(default_factory=dict)
    message: str = ""
    user_visible: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "counts", MappingProxyType(dict(self.counts)))

    def __bool__(self) -> bool:
        """Keep legacy truth-value callers compatible with typed outcomes."""
        return self.status in {TaskStatus.SUCCESS, TaskStatus.PARTIAL}


class TaskBoundaryError(Exception):
    status = TaskStatus.TERMINAL

    def __init__(self, *, code: str, message: str, user_visible: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.user_visible = user_visible


class RetryableTaskError(TaskBoundaryError):
    status = TaskStatus.RETRYABLE


class TerminalTaskError(TaskBoundaryError):
    status = TaskStatus.TERMINAL


_TRANSIENT_ERRORS = (OperationalError, InterfaceError, TimeoutError, ConnectionError)


def classify_task_error(error: BaseException) -> TaskStatus:
    """Classify known transient boundaries; this deliberately defines no retry policy."""
    if isinstance(error, TaskBoundaryError):
        return error.status
    if isinstance(error, _TRANSIENT_ERRORS):
        return TaskStatus.RETRYABLE
    return TaskStatus.TERMINAL


def reverse_job_detail(job_id: int) -> str:
    """Resolve the optional UI capability, falling back only when its route is absent."""
    try:
        return reverse("job_detail", kwargs={"pk": job_id})
    except NoReverseMatch:
        return f"/jobs/{job_id}/"
