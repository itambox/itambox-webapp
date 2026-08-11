"""
Snipe-IT → ITAMbox migration importer.

Usage (via management command):
    python manage.py import_snipeit --url https://snipe.example --token-env SNIPEIT_TOKEN
                                    [--tenant <slug>] [--map-companies-to-tenants]
                                    [--dry-run] [--skip assets,licenses,...] [--update]
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit

import requests
from django.views.decorators.debug import sensitive_variables
from requests import exceptions as requests_exceptions

from core.errors import (
    MAX_RETRY_AFTER_SECONDS,
    IntegrationAuthenticationError,
    IntegrationConfigurationError,
    IntegrationContext,
    IntegrationContractError,
    IntegrationError,
    IntegrationNotFoundError,
    IntegrationRateLimitedError,
    IntegrationRequestError,
    IntegrationRetryBudgetExceededError,
    IntegrationUnavailableError,
    RetryBudget,
)

logger = logging.getLogger("core.importers.snipeit")

SnipeITError = IntegrationError


class SnipeITClient:
    """Thin HTTP client that handles auth, pagination, and 429 back-off."""

    PAGE_SIZE = 500

    @sensitive_variables("token")
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        context: IntegrationContext | None = None,
        retry_budget_factory: Callable[[], RetryBudget] | None = None,
    ):
        parsed = urlsplit(base_url)
        safe_netloc = parsed.netloc.rsplit("@", 1)[-1]
        self.base_url = urlunsplit((parsed.scheme, safe_netloc, parsed.path.rstrip("/"), "", ""))
        self.context = context or IntegrationContext(provider="snipe-it", operation="import")
        self.retry_budget_factory = retry_budget_factory or RetryBudget
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            }
        )

    def get_all(self, endpoint: str, params: dict | None = None) -> Iterator[dict]:
        """Yield every row from a paginated list endpoint."""
        offset = 0
        budget = self.retry_budget_factory()
        while True:
            data = self._get(
                endpoint,
                {**(params or {}), "limit": self.PAGE_SIZE, "offset": offset},
                budget=budget,
                operation="collection.list",
            )
            rows = data.get("rows")
            total = data.get("total")
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise IntegrationContractError(context=self._operation_context("collection.list"))
            if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                raise IntegrationContractError(context=self._operation_context("collection.list"))
            yield from rows
            offset += len(rows)
            if offset >= total or not rows:
                break

    def get_detail(self, endpoint: str) -> dict:
        """GET a single resource."""
        return self._get(endpoint, budget=self.retry_budget_factory(), operation="detail.get")

    def _operation_context(self, operation: str) -> IntegrationContext:
        return IntegrationContext(
            provider=self.context.provider,
            operation=operation,
            tenant_id=self.context.tenant_id,
            actor_id=self.context.actor_id,
            request_id=self.context.request_id,
        )

    @staticmethod
    def _parse_retry_after(headers) -> float | None:
        value = headers.get("Retry-After")
        try:
            parsed = float(value)
        # broad except: boundary-isolation: optional child rows may degrade without discarding the parent item
        except (TypeError, ValueError):
            return None
        if not math.isfinite(parsed):
            return None
        return max(0.0, min(parsed, MAX_RETRY_AFTER_SECONDS))

    @sensitive_variables()
    def _get(
        self,
        endpoint: str,
        params: dict | None = None,
        *,
        budget: RetryBudget | None = None,
        operation: str = "request.get",
    ) -> dict:
        url = f"{self.base_url}{endpoint}"
        context = self._operation_context(operation)
        response = self._request(url, params=params, context=context, budget=budget or RetryBudget())
        self._raise_response_error(response, context=context)
        return self._parse_response(response, context=context)

    @sensitive_variables()
    def _request(self, url: str, *, params: dict | None, context: IntegrationContext, budget: RetryBudget):
        while True:
            try:
                response = self._session.get(url, params=params, timeout=30, allow_redirects=False)
            except (
                requests_exceptions.SSLError,
                requests_exceptions.InvalidURL,
                requests_exceptions.MissingSchema,
                requests_exceptions.TooManyRedirects,
            ) as exc:
                raise IntegrationConfigurationError(context=context, cause_type=type(exc).__name__) from None
            except requests_exceptions.RequestException as exc:
                raise IntegrationUnavailableError(context=context, cause_type=type(exc).__name__) from None
            if response.status_code != 429:
                return response

            retry_after = self._parse_retry_after(response.headers)
            rate_limited = IntegrationRateLimitedError(
                context=context,
                status_code=response.status_code,
                retry_after=retry_after,
            )
            delay = budget.next_delay(retry_after, now=time.monotonic())
            if delay is None:
                if budget.attempts == 0:
                    raise rate_limited
                raise IntegrationRetryBudgetExceededError(
                    context=context,
                    status_code=response.status_code,
                    retry_after=rate_limited.retry_after,
                ) from rate_limited
            log_extra = rate_limited.log_extra(retry_count=budget.attempts, retry_delay=delay)
            logger.warning(
                "Snipe-IT request rate-limited; retrying integration=%s",
                log_extra["integration"],
                extra=log_extra,
            )
            time.sleep(delay)

    @staticmethod
    def _raise_response_error(response, *, context: IntegrationContext) -> None:
        status_code = response.status_code
        if status_code in (401, 403):
            raise IntegrationAuthenticationError(context=context, status_code=status_code)
        if status_code == 404:
            raise IntegrationNotFoundError(context=context, status_code=status_code)
        if status_code >= 500:
            raise IntegrationUnavailableError(context=context, status_code=status_code)
        if status_code >= 400:
            raise IntegrationRequestError(context=context, status_code=status_code)

    @staticmethod
    def _parse_response(response, *, context: IntegrationContext) -> dict:
        try:
            data = response.json()
        # broad except: boundary-isolation: optional child rows may degrade without discarding the parent item
        except (TypeError, ValueError) as exc:
            raise IntegrationContractError(
                context=context,
                status_code=response.status_code,
                cause_type=type(exc).__name__,
            ) from None
        if not isinstance(data, dict):
            raise IntegrationContractError(context=context, status_code=response.status_code)
        return data
