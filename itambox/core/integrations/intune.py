"""Microsoft Intune Graph API client.

Placed in core/integrations/ because discovery connectors are infrastructure,
not domain logic — they feed any number of apps (assets, software) the same way
middleware feeds any number of views.

Required Azure app permission (application, admin-consented):
  DeviceManagementManagedDevices.Read.All
"""

import logging
import time
from typing import Any

import requests
from django.views.decorators.debug import sensitive_variables

from core.errors import (
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

_TOKEN_CACHE: dict = {}  # keyed by azure_tenant_id

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


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
    if status_code == 404:
        raise IntegrationNotFoundError(context=context, status_code=status_code)
    if status_code >= 500:
        raise IntegrationUnavailableError(context=context, status_code=status_code)
    raise IntegrationRequestError(context=context, status_code=status_code)


def _parse_retry_after(headers: Any) -> float | None:
    """Parse a numeric Retry-After without exposing the header or URL."""

    value = headers.get("Retry-After")
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


@sensitive_variables("client_secret")
def _get_token(
    azure_tenant_id: str,
    client_id: str,
    client_secret: str,
    *,
    context: IntegrationContext | None = None,
) -> str:
    """Return a valid access token, refreshing when within 60 s of expiry."""

    context = _context_or_default(context, "oauth.token")
    cached = _TOKEN_CACHE.get(azure_tenant_id)
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
    except requests.RequestException as exc:
        raise IntegrationUnavailableError(context=context) from exc

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
    _TOKEN_CACHE[azure_tenant_id] = {
        "token": token,
        "expires_at": time.monotonic() + max(0, expires_in),
    }
    return token


@sensitive_variables("headers")
def _graph_get_paginated(
    url: str,
    headers: dict,
    *,
    context: IntegrationContext | None = None,
    budget: RetryBudget | None = None,
) -> list:
    """Follow @odata.nextLink pagination with a finite, safe retry budget."""

    context = _context_or_default(context, "graph.collection.get")
    budget = budget or RetryBudget()
    items = []
    started_at = time.monotonic()
    while url:
        try:
            resp = requests.get(url, headers=headers, timeout=60)
        except requests.RequestException as exc:
            raise IntegrationUnavailableError(context=context) from exc
        if resp.status_code == 429:
            retry_after = _parse_retry_after(resp.headers)
            rate_limited = IntegrationRateLimitedError(
                context=context,
                status_code=resp.status_code,
                retry_after=retry_after,
            )
            delay = budget.next_delay(
                retry_after,
                elapsed_seconds=time.monotonic() - started_at,
            )
            if delay is None:
                if budget.attempts == 0:
                    raise rate_limited
                raise IntegrationRetryBudgetExceededError(
                    context=context,
                    status_code=resp.status_code,
                ) from rate_limited
            log_extra = rate_limited.log_extra()
            log_extra["integration"]["retry_count"] = budget.attempts
            logger.warning("External integration request is rate-limited; retrying", extra=log_extra)
            time.sleep(delay)
            continue

        _raise_response_error(resp, context=context)
        try:
            data = resp.json()
        except (TypeError, ValueError) as exc:
            raise IntegrationContractError(context=context, status_code=resp.status_code) from exc
        if not isinstance(data, dict) or not isinstance(data.get("value"), list):
            raise IntegrationContractError(context=context, status_code=resp.status_code)
        items.extend(data["value"])
        next_url = data.get("@odata.nextLink")
        if next_url is not None and not isinstance(next_url, str):
            raise IntegrationContractError(context=context, status_code=resp.status_code)
        url = next_url
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
    ):
        self.azure_tenant_id = azure_tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.context = context or IntegrationContext(provider="microsoft-graph", operation="sync")

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
        return _graph_get_paginated(
            url,
            self._headers(),
            context=self._operation_context("devices.list"),
        )

    def get_detected_apps(self, device_id: str) -> list:
        """Return detected apps for a single managed device."""
        url = f"{GRAPH_BASE}/deviceManagement/managedDevices/{device_id}/detectedApps"
        return _graph_get_paginated(
            url,
            self._headers(),
            context=self._operation_context("device_apps.list"),
        )
