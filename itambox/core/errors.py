"""Shared, safe error contracts for external integration boundaries.

This module is intentionally dependency-free.  Integration adapters may add
provider semantics, but callers can classify failures without importing a
provider SDK or inspecting remote exception text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class FailureDisposition(StrEnum):
    """Whether a caller may sensibly try the operation again later."""

    RETRYABLE = "retryable"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class IntegrationContext:
    """Allowlisted context safe to attach to integration log records."""

    provider: str
    operation: str
    tenant_id: int | None = None
    actor_id: int | None = None
    request_id: str | None = None


@dataclass(slots=True)
class RetryBudget:
    """Finite retry budget shared by an integration operation.

    ``requested_delay`` may come from an upstream ``Retry-After`` header.  The
    caller supplies elapsed time so this value object remains deterministic and
    has no hidden clock or sleeping side effects.
    """

    max_attempts: int = 3
    max_elapsed_seconds: float = 60.0
    max_delay_seconds: float = 30.0
    default_delay_seconds: float = 1.0
    attempts: int = 0
    started_at: float | None = None

    def next_delay(self, requested_delay: float | None, *, now: float) -> float | None:
        """Return a safe delay and consume one retry, or return ``None``."""

        if self.started_at is None:
            self.started_at = now
        elapsed_seconds = max(0.0, now - self.started_at)
        if self.attempts >= self.max_attempts or elapsed_seconds >= self.max_elapsed_seconds:
            return None
        remaining_seconds = self.max_elapsed_seconds - max(0.0, elapsed_seconds)
        delay = self.default_delay_seconds if requested_delay is None else requested_delay
        try:
            delay = float(delay)
        except (TypeError, ValueError):
            delay = self.default_delay_seconds
        delay = max(0.0, min(delay, self.max_delay_seconds, remaining_seconds))
        self.attempts += 1
        return delay


class IntegrationError(Exception):
    """Base exception whose string and log context are safe by construction."""

    code = "integration.error"
    disposition = FailureDisposition.TERMINAL
    user_message = "The external integration could not complete the operation."
    user_visible = True

    def __init__(
        self,
        *,
        context: IntegrationContext,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        self.context = context
        self.status_code = status_code
        self.retry_after = retry_after
        # The sole Exception argument is a constant safe message.  Remote
        # URLs, headers, payloads and exception strings never enter __str__.
        super().__init__(self.user_message)

    def display_message(self) -> str:
        """Return the safe message a caller may persist or show to a user."""

        return self.user_message if self.user_visible else IntegrationError.user_message

    def log_extra(
        self,
        *,
        object_id: str | None = None,
        exception_type: str | None = None,
        cause_type: str | None = None,
        source_file: str | None = None,
        source_line: int | None = None,
        retry_count: int | None = None,
        retry_delay: float | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Return only allowlisted structured fields for ``logging.extra``."""

        integration = {
            "provider": self.context.provider,
            "operation": self.context.operation,
            "tenant_id": self.context.tenant_id,
            "actor_id": self.context.actor_id,
            "request_id": self.context.request_id,
            "error_code": self.code,
            "disposition": self.disposition.value,
            "status_code": self.status_code,
        }
        for name, value in {
            "object_id": object_id,
            "exception_type": exception_type,
            "cause_type": cause_type,
            "source_file": source_file,
            "source_line": source_line,
            "retry_count": retry_count,
            "retry_delay": retry_delay,
            "retry_after": self.retry_after,
        }.items():
            if value is not None:
                integration[name] = value
        return {"integration": integration}


class IntegrationAuthenticationError(IntegrationError):
    code = "integration.authentication"
    user_message = "External integration authentication failed."


class IntegrationConfigurationError(IntegrationError):
    code = "integration.configuration"
    user_message = "External integration configuration is incomplete or invalid."


class IntegrationUnavailableError(IntegrationError):
    code = "integration.unavailable"
    disposition = FailureDisposition.RETRYABLE
    user_message = "The external integration is temporarily unavailable."


class IntegrationRateLimitedError(IntegrationError):
    code = "integration.rate_limited"
    disposition = FailureDisposition.RETRYABLE
    user_visible = False
    user_message = "The external integration rate-limited the operation."


class IntegrationRetryBudgetExceededError(IntegrationError):
    code = "integration.retry_budget_exhausted"
    user_message = "The external integration remained unavailable; retry the operation later."


class IntegrationContractError(IntegrationError):
    code = "integration.invalid_response"
    user_message = "The external integration returned an invalid response."


class IntegrationRequestError(IntegrationError):
    code = "integration.request_rejected"
    user_message = "The external integration rejected the operation."


class IntegrationNotFoundError(IntegrationRequestError):
    code = "integration.not_found"
    user_message = "The requested external resource was not found."


class IntegrationUnexpectedError(IntegrationError):
    code = "integration.unexpected"
    user_message = "The integration task failed unexpectedly; check the task logs."
