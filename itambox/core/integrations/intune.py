"""Microsoft Intune Graph API client.

Placed in core/integrations/ because discovery connectors are infrastructure,
not domain logic — they feed any number of apps (assets, software) the same way
middleware feeds any number of views.

Required Azure app permission (application, admin-consented):
  DeviceManagementManagedDevices.Read.All
"""

import logging
import math
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit

import requests
from django.views.decorators.debug import sensitive_variables
from requests import exceptions as requests_exceptions

from core.errors import (
    MAX_RETRY_AFTER_SECONDS,
    IntegrationAuthenticationError,
    IntegrationConfigurationError,
    IntegrationContext,
    IntegrationContractError,
    IntegrationNotFoundError,
    IntegrationRateLimitedError,
    IntegrationRequestError,
    IntegrationRetryBudgetExceededError,
    IntegrationUnavailableError,
    RetryBudget,
)

logger = logging.getLogger(__name__)

_TOKEN_CACHE: dict[tuple[str, str], dict[str, Any]] = {}  # keyed by Azure tenant and app client

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MAX_GRAPH_PAGES = 1000


def _is_graph_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.netloc == "graph.microsoft.com" and parsed.path.startswith("/v1.0/")


def _context_or_default(context: IntegrationContext | None, operation: str) -> IntegrationContext:
    if context is None:
        return IntegrationContext(provider="microsoft-graph", operation=operation)
    return IntegrationContext(
        provider=context.provider,
        operation=operation,
        tenant_id=context.tenant_id,
        actor_id=context.actor_id,
        request_id=context.request_id,
    )


def _raise_response_error(response: Any, *, context: IntegrationContext) -> None:
    status_code = response.status_code
    if status_code < 400:
        return
    if status_code in (401, 403):
        raise IntegrationAuthenticationError(context=context, status_code=status_code)
    if status_code == 429:
        raise IntegrationRateLimitedError(
            context=context,
            status_code=status_code,
            retry_after=_parse_retry_after(response.headers),
        )
    if status_code == 404:
        raise IntegrationNotFoundError(context=context, status_code=status_code)
    if status_code >= 500:
        raise IntegrationUnavailableError(context=context, status_code=status_code)
    raise IntegrationRequestError(context=context, status_code=status_code)


def _parse_retry_after(headers: Any) -> float | None:
    """Parse a numeric Retry-After without exposing the header or URL."""

    value = headers.get("Retry-After")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return max(0.0, min(parsed, MAX_RETRY_AFTER_SECONDS))


@sensitive_variables()
def _get_token(
    azure_tenant_id: str,
    client_id: str,
    client_secret: str,
    *,
    context: IntegrationContext | None = None,
) -> str:
    """Return a valid access token, refreshing when within 60 s of expiry."""

    context = _context_or_default(context, "oauth.token")
    cache_key = (azure_tenant_id, client_id)
    cached = _TOKEN_CACHE.get(cache_key)
    if cached and cached["expires_at"] - 60 > time.monotonic():
        return cached["token"]

    url = f"https://login.microsoftonline.com/{azure_tenant_id}/oauth2/v2.0/token"
    try:
        resp = requests.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "https://graph.microsoft.com/.default",
            },
            timeout=30,
        )
    except (
        requests_exceptions.SSLError,
        requests_exceptions.InvalidURL,
        requests_exceptions.MissingSchema,
        requests_exceptions.TooManyRedirects,
    ) as exc:
        raise IntegrationConfigurationError(context=context, cause_type=type(exc).__name__) from None
    except requests_exceptions.RequestException as exc:
        raise IntegrationUnavailableError(context=context, cause_type=type(exc).__name__) from None

    _raise_response_error(resp, context=context)
    try:
        data = resp.json()
    except (TypeError, ValueError) as exc:
        raise IntegrationContractError(context=context, status_code=resp.status_code) from exc
    if not isinstance(data, dict) or not isinstance(data.get("access_token"), str) or not data["access_token"]:
        raise IntegrationContractError(context=context, status_code=resp.status_code)
    try:
        expires_in = int(data.get("expires_in", 3600))
    except (TypeError, ValueError) as exc:
        raise IntegrationContractError(context=context, status_code=resp.status_code) from exc

    token = data["access_token"]
    _TOKEN_CACHE[cache_key] = {
        "token": token,
        "expires_at": time.monotonic() + max(0, expires_in),
    }
    return token


@sensitive_variables("headers")
def _get_graph_response(
    url: str,
    headers: dict,
    *,
    context: IntegrationContext,
    budget: RetryBudget,
) -> requests.Response:
    while True:
        try:
            resp = requests.get(url, headers=headers, timeout=60)
        except (
            requests_exceptions.SSLError,
            requests_exceptions.InvalidURL,
            requests_exceptions.MissingSchema,
            requests_exceptions.TooManyRedirects,
        ) as exc:
            raise IntegrationConfigurationError(context=context, cause_type=type(exc).__name__) from None
        except requests_exceptions.RequestException as exc:
            raise IntegrationUnavailableError(context=context, cause_type=type(exc).__name__) from None
        if resp.status_code != 429:
            return resp

        retry_after = _parse_retry_after(resp.headers)
        rate_limited = IntegrationRateLimitedError(
            context=context,
            status_code=resp.status_code,
            retry_after=retry_after,
        )
        delay = budget.next_delay(retry_after, now=time.monotonic())
        if delay is None:
            if budget.attempts == 0:
                raise rate_limited
            raise IntegrationRetryBudgetExceededError(
                context=context,
                status_code=resp.status_code,
                retry_after=rate_limited.retry_after,
            ) from rate_limited
        log_extra = rate_limited.log_extra(
            retry_count=budget.attempts,
            retry_delay=delay,
        )
        logger.warning(
            "External integration request is rate-limited; retrying integration=%s",
            log_extra["integration"],
            extra=log_extra,
        )
        time.sleep(delay)


def _parse_graph_page(resp: requests.Response, *, context: IntegrationContext) -> tuple[list, str | None]:
    try:
        data = resp.json()
    except (TypeError, ValueError) as exc:
        raise IntegrationContractError(context=context, status_code=resp.status_code) from exc
    if not isinstance(data, dict) or not isinstance(data.get("value"), list):
        raise IntegrationContractError(context=context, status_code=resp.status_code)
    next_url = data.get("@odata.nextLink")
    if next_url is not None and not isinstance(next_url, str):
        raise IntegrationContractError(context=context, status_code=resp.status_code)
    return data["value"], next_url


@sensitive_variables("headers")
def _graph_get_paginated(
    url: str,
    headers: dict,
    *,
    context: IntegrationContext | None = None,
    budget: RetryBudget | None = None,
    max_pages: int = MAX_GRAPH_PAGES,
) -> list:
    """Follow @odata.nextLink pagination with a finite, safe retry budget."""

    context = _context_or_default(context, "graph.collection.get")
    budget = budget or RetryBudget()
    items = []
    pages = 0
    while url:
        pages += 1
        if pages > max_pages:
            raise IntegrationContractError(context=context)
        if not _is_graph_url(url):
            raise IntegrationContractError(context=context)
        resp = _get_graph_response(url, headers, context=context, budget=budget)
        _raise_response_error(resp, context=context)
        page_items, url = _parse_graph_page(resp, context=context)
        items.extend(page_items)
    return items


class IntuneClient:
    """Thin stateless wrapper around the Intune portion of the Graph API."""

    def __init__(
        self,
        azure_tenant_id: str,
        client_id: str,
        client_secret: str,
        *,
        context: IntegrationContext | None = None,
        retry_budget_factory: Callable[[], RetryBudget] | None = None,
    ):
        self.azure_tenant_id = azure_tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.context = context or IntegrationContext(provider="microsoft-graph", operation="sync")
        self.retry_budget_factory = retry_budget_factory or RetryBudget

    def _operation_context(self, operation: str) -> IntegrationContext:
        return _context_or_default(self.context, operation)

    def _headers(self) -> dict:
        token = _get_token(
            self.azure_tenant_id,
            self.client_id,
            self.client_secret,
            context=self._operation_context("oauth.token"),
        )
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def get_managed_devices(self) -> list:
        """Return all managed devices. Each item is the raw Graph JSON object."""
        url = (
            f"{GRAPH_BASE}/deviceManagement/managedDevices"
            "?$select=id,deviceName,serialNumber,manufacturer,model,"
            "operatingSystem,osVersion,userPrincipalName,"
            "lastSyncDateTime,totalStorageSpaceInBytes"
        )
        try:
            return _graph_get_paginated(
                url,
                self._headers(),
                context=self._operation_context("devices.list"),
                budget=self.retry_budget_factory(),
            )
        except IntegrationAuthenticationError as exc:
            if exc.status_code == 401:
                _TOKEN_CACHE.pop((self.azure_tenant_id, self.client_id), None)
            raise

    def get_detected_apps(self, device_id: str) -> list:
        """Return detected apps for a single managed device."""
        url = f"{GRAPH_BASE}/deviceManagement/managedDevices/{quote(device_id, safe='')}/detectedApps"
        try:
            return _graph_get_paginated(
                url,
                self._headers(),
                context=self._operation_context("device_apps.list"),
                budget=self.retry_budget_factory(),
            )
        except IntegrationAuthenticationError as exc:
            if exc.status_code == 401:
                _TOKEN_CACHE.pop((self.azure_tenant_id, self.client_id), None)
            raise
