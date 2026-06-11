"""
Microsoft Intune Graph API client.

Placed in core/integrations/ because discovery connectors are infrastructure,
not domain logic — they feed any number of apps (assets, software) the same way
middleware feeds any number of views.

Required Azure app permission (application, admin-consented):
  DeviceManagementManagedDevices.Read.All
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)

_TOKEN_CACHE: dict = {}  # keyed by azure_tenant_id

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _get_token(azure_tenant_id: str, client_id: str, client_secret: str) -> str:
    """Return a valid access token, refreshing when within 60 s of expiry."""
    cached = _TOKEN_CACHE.get(azure_tenant_id)
    if cached and cached["expires_at"] - 60 > time.monotonic():
        return cached["token"]

    url = f"https://login.microsoftonline.com/{azure_tenant_id}/oauth2/v2.0/token"
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
    resp.raise_for_status()
    data = resp.json()
    token = data["access_token"]
    expires_in = int(data.get("expires_in", 3600))
    _TOKEN_CACHE[azure_tenant_id] = {
        "token": token,
        "expires_at": time.monotonic() + expires_in,
    }
    return token


def _graph_get_paginated(url: str, headers: dict) -> list:
    """Follow @odata.nextLink pagination and return all items."""
    items = []
    while url:
        resp = requests.get(url, headers=headers, timeout=60)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 10))
            logger.warning("Graph 429 — waiting %ss", retry_after)
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        data = resp.json()
        items.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
    return items


class IntuneClient:
    """Thin stateless wrapper around the Intune portion of the Graph API."""

    def __init__(self, azure_tenant_id: str, client_id: str, client_secret: str):
        self.azure_tenant_id = azure_tenant_id
        self.client_id = client_id
        self.client_secret = client_secret

    def _headers(self) -> dict:
        token = _get_token(self.azure_tenant_id, self.client_id, self.client_secret)
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def get_managed_devices(self) -> list:
        """Return all managed devices. Each item is the raw Graph JSON object."""
        url = (
            f"{GRAPH_BASE}/deviceManagement/managedDevices"
            "?$select=id,deviceName,serialNumber,manufacturer,model,"
            "operatingSystem,osVersion,userPrincipalName,"
            "lastSyncDateTime,totalStorageSpaceInBytes"
        )
        return _graph_get_paginated(url, self._headers())

    def get_detected_apps(self, device_id: str) -> list:
        """Return detected apps for a single managed device."""
        url = f"{GRAPH_BASE}/deviceManagement/managedDevices/{device_id}/detectedApps"
        return _graph_get_paginated(url, self._headers())
