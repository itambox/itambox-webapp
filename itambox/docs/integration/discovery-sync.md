# Discovery sync

ITAMbox can pull device inventory from Microsoft Intune and is extensible to any
source (SCCM, Jamf, Lansweeper, custom scripts) via its REST API.

---

## 1  Microsoft Intune connector

### 1.1  Azure app registration

1. In the Azure portal, go to **Entra ID → App registrations → New registration**.
2. Give it a name (e.g. *ITAMbox Intune Sync*), choose **Accounts in this
   organizational directory only**, and click **Register**.
3. In the app's **Certificates & secrets** blade, create a **Client secret**.
   Copy the value now — it is shown only once.
4. In **API permissions → Add a permission → Microsoft Graph → Application
   permissions**, add:
   - `DeviceManagementManagedDevices.Read.All`
5. Click **Grant admin consent** for your organisation.
6. Note the **Application (client) ID** and **Directory (tenant) ID** from the
   app's Overview blade.

### 1.2  Environment variable

Set `ITAMBOX_TENANT_INTUNE_CONFIGS` to a JSON object keyed by ITAMbox **tenant
slug**:

```json
{
  "acme": {
    "TENANT_ID":      "<Directory tenant ID from Azure>",
    "CLIENT_ID":      "<Application client ID from Azure>",
    "CLIENT_SECRET":  "<Client secret value>",
    "CREATE_MISSING": true,
    "DEFAULT_STATUS": "deployable",
    "SYNC_SOFTWARE":  true
  }
}
```

| Key | Type | Default | Purpose |
|-----|------|---------|---------|
| `TENANT_ID` | string | required | Azure Directory (tenant) ID |
| `CLIENT_ID` | string | required | Azure Application (client) ID |
| `CLIENT_SECRET` | string | required | Azure client secret value |
| `CREATE_MISSING` | bool | `false` | Create an Asset for every device not already in ITAMbox |
| `DEFAULT_STATUS` | string | `"deployable"` | StatusLabel slug assigned to auto-created assets |
| `SYNC_SOFTWARE` | bool | `true` | Upsert InstalledSoftware records from detected apps |

Secrets stay in the environment — never store them in the database.

Multiple tenants are supported by adding additional top-level keys:

```json
{
  "acme":   { "azure_tenant_id": "...", "client_id": "...", "client_secret": "..." },
  "globex": { "azure_tenant_id": "...", "client_id": "...", "client_secret": "..." }
}
```

### 1.3  Running a sync

**Ad-hoc / one-off:**

```bash
python manage.py sync_intune --tenant acme --now
```

Add `--dry-run` to simulate without writing anything.

**Enqueue into django-q2 (returns immediately):**

```bash
python manage.py sync_intune --tenant acme
```

**Nightly schedule via django-q2:**

Run this once in a Django shell (or a data migration) to schedule nightly runs:

```python
from django_q.models import Schedule
from organization.models import Tenant
from django.contrib.auth import get_user_model
from core.models import Job

User = get_user_model()
tenant = Tenant.objects.get(slug="acme")
admin  = User.objects.filter(is_superuser=True).order_by("pk").first()

# Create a placeholder job; the task will update it on each run.
# In practice, sync_intune creates its own Job — just schedule the task directly:
Schedule.objects.get_or_create(
    name=f"intune-sync-{tenant.slug}",
    defaults=dict(
        func="core.management.commands.sync_intune.Command.handle",
        # Simpler: wrap the management command:
        func="django.core.management.call_command",
        args=repr(("sync_intune",)),
        kwargs=repr({"tenant": tenant.slug, "now": True}),
        schedule_type=Schedule.CRON,
        cron="0 3 * * *",
    ),
)
```

### 1.4  What the sync does

1. **Match** — every Intune device is matched to an `Asset` in ITAMbox by
   `serial_number` (case-insensitive), scoped to the tenant.
2. **Update** — matched assets receive discovery facts in `custom_field_data`:
   `intune_device_id`, `intune_last_sync`, `os_version`.
3. **Create** (when `create_missing: true`) — unmatched devices get a new
   `Manufacturer`, `AssetType`, and `Asset`. The asset's status is set to the
   configured `default_status`.
4. **Holder hint** — if the device's `userPrincipalName` matches an
   `AssetHolder.upn` in the tenant, that UPN is recorded as
   `intune_primary_user` in `custom_field_data`. No automatic checkout is
   performed; *discovery proposes, humans dispose*.
5. **Software** (when `sync_software: true`) — detected apps are upserted into
   `InstalledSoftware` with `discovered_by_agent="Intune"`. Re-running is safe;
   the unique constraint `(asset, software, version_detected)` is respected.

Results are stored in a `Job` record visible in the Jobs UI.

---

## 2  Generic "push from anything" recipe

Any system that can make HTTP requests can feed inventory into ITAMbox via the
REST API. The recipe below uses plain Python + `requests`; adapt it for
SCCM, Jamf, Lansweeper, or custom agents.

### 2.1  Obtain an API token

In ITAMbox, go to **Profile → API tokens → Add token** and copy the value.

### 2.2  Example script (~40 lines)

```python
"""
Minimal ITAMbox discovery push — adapt for SCCM, Jamf, Lansweeper, etc.
"""
import requests

ITAMBOX_URL   = "https://itambox.example.com"
API_TOKEN     = "your-token-here"
HEADERS = {
    "Authorization": f"Token {API_TOKEN}",
    "Content-Type":  "application/json",
}

def get_or_create_asset(serial: str, name: str, status_slug: str = "deployable") -> dict:
    """Look up an asset by serial; create it if missing."""
    r = requests.get(
        f"{ITAMBOX_URL}/api/assets/assets/",
        params={"serial_number": serial},
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    results = r.json()["results"]
    if results:
        return results[0]

    # Create
    r = requests.post(
        f"{ITAMBOX_URL}/api/assets/assets/",
        json={"name": name, "serial_number": serial, "status": status_slug},
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()

def push_installed_software(asset_id: int, software_id: int, version: str, agent: str):
    """Upsert an InstalledSoftware record."""
    r = requests.post(
        f"{ITAMBOX_URL}/api/software/installed/",
        json={
            "asset":               asset_id,
            "software":            software_id,
            "version_detected":    version,
            "discovered_by_agent": agent,
        },
        headers=HEADERS,
        timeout=30,
    )
    if r.status_code == 400 and "unique" in r.text.lower():
        return  # Already exists — safe to ignore
    r.raise_for_status()

# Example: push one device
asset = get_or_create_asset(serial="SN99999", name="MY-LAPTOP-001")
push_installed_software(
    asset_id=asset["id"],
    software_id=42,   # look up via /api/software/software/?name=...
    version="5.0.1",
    agent="MySCCMScript",
)
```

This template is the starting point for any custom discovery source.  The key
endpoints are:

| Endpoint | Purpose |
|----------|---------|
| `GET  /api/assets/assets/?serial_number=…` | Find asset by serial |
| `POST /api/assets/assets/` | Create asset |
| `PATCH /api/assets/assets/{id}/` | Update asset fields |
| `GET  /api/software/software/?name=…` | Find software catalogue entry |
| `POST /api/software/software/` | Create software catalogue entry |
| `POST /api/software/installed/` | Record installed software on an asset |
