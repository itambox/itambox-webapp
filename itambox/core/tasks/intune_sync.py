"""
Intune discovery sync task.

Matches Graph API managed devices to Assets by serial number, stamps
discovery facts into custom_field_data, optionally creates new assets,
and upserts InstalledSoftware records.

Discovery proposes, humans dispose: a matched userPrincipalName is recorded
in custom_field_data as intune_primary_user rather than triggering an
automatic checkout, because assignment carries compliance side-effects.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any, Protocol, TypedDict

from django.conf import settings
from django.utils import timezone

from core.context import get_current_request_id
from core.errors import (
    IntegrationAuthenticationError,
    IntegrationConfigurationError,
    IntegrationContext,
    IntegrationError,
    IntegrationUnexpectedError,
)
from core.integrations.intune import IntuneClient
from core.models import Job
from core.tasks.context import TaskContext

logger = logging.getLogger(__name__)


def _record_integration_failure(job: Job, error: IntegrationError) -> None:
    context = error.context
    job.append_log(
        "Integration failure: "
        f"code={error.code}; disposition={error.disposition.value}; "
        f"provider={context.provider}; operation={context.operation}; "
        f"tenant_id={context.tenant_id}; actor_id={context.actor_id}; request_id={context.request_id}; "
        f"status_code={error.status_code}"
    )


class _IntuneTenant(Protocol):
    slug: str


class _IntuneAsset(Protocol):
    custom_field_data: dict[str, object] | None

    def save(self, *, update_fields: list[str]) -> None:
        pass


class IntuneDevicePayload(TypedDict, total=False):
    """Selected string fields returned by the Graph managed-device endpoint."""

    id: str
    deviceName: str
    serialNumber: str
    manufacturer: str
    model: str
    osVersion: str
    userPrincipalName: str
    lastSyncDateTime: str


class IntuneAppPayload(TypedDict, total=False):
    """Selected string fields returned by the Graph detected-app endpoint."""

    displayName: str
    publisher: str
    version: str


class IntuneSyncResult(TypedDict):
    """Counts persisted as the result of one Intune discovery run."""

    devices_total: int
    matched: int
    updated: int
    created: int
    skipped: int
    apps_upserted: int
    software_degraded: int


def sync_tenant_intune(
    tenant_id: int,
    user_id: int,
    job_id: int,
    dry_run: bool = False,
) -> None:
    """Enqueued entry-point for the nightly Intune sync."""
    with TaskContext(tenant_id=tenant_id, user_id=user_id) as ctx:
        try:
            job = Job.objects.get(pk=job_id)
        except Job.DoesNotExist:
            logger.error("Intune sync job %s not found.", job_id)
            return

        if not job.mark_running():
            logger.info("Job %s is no longer pending (cancelled?); skipping Intune sync.", job_id)
            return
        if dry_run:
            job.append_log("[dry-run] No writes will be performed.")

        try:
            request_id = get_current_request_id()
            integration_context = IntegrationContext(
                provider="microsoft-graph",
                operation="sync",
                tenant_id=tenant_id,
                actor_id=user_id,
                request_id=str(request_id) if request_id else None,
            )
            counts = _run_sync(ctx.tenant, dry_run, job, integration_context)
            job.mark_completed(result=counts)
        except IntegrationError as exc:
            log_extra = exc.log_extra(cause_type=type(exc.__cause__).__name__ if exc.__cause__ else None)
            logger.error(
                "Intune sync failed at an external integration boundary integration=%s",
                log_extra["integration"],
                extra=log_extra,
            )
            _record_integration_failure(job, exc)
            job.mark_failed(exc.display_message())
        # broad except: task-isolation: unknown task failures must leave a safe recorded failure
        except Exception as exc:
            request_id = get_current_request_id()
            unexpected = IntegrationUnexpectedError(
                context=IntegrationContext(
                    provider="microsoft-graph",
                    operation="sync",
                    tenant_id=tenant_id,
                    actor_id=user_id,
                    request_id=str(request_id) if request_id else None,
                )
            )
            traceback = exc.__traceback__
            while traceback and traceback.tb_next:
                traceback = traceback.tb_next
            extra = unexpected.log_extra(
                exception_type=type(exc).__name__,
                source_file=traceback.tb_frame.f_code.co_filename if traceback else None,
                source_line=traceback.tb_lineno if traceback else None,
            )
            logger.error(
                "Intune sync failed unexpectedly at the task boundary integration=%s",
                extra["integration"],
                extra=extra,
            )
            _record_integration_failure(job, unexpected)
            job.mark_failed(unexpected.display_message())


def _read_intune_config(
    config: Mapping[str, Any], *, context: IntegrationContext
) -> tuple[str, str, str, bool, str, bool]:
    try:
        azure_tenant_id = config["azure_tenant_id"]
        client_id = config["client_id"]
        client_secret = config["client_secret"]
        create_missing = bool(config.get("create_missing", False))
        default_status_slug = config.get("default_status", "deployable")
        sync_software = bool(config.get("sync_software", True))
    except (AttributeError, KeyError, TypeError) as exc:
        raise IntegrationConfigurationError(context=context) from exc
    if any(not isinstance(value, str) or not value.strip() for value in (azure_tenant_id, client_id, client_secret)):
        raise IntegrationConfigurationError(context=context) from None
    if not isinstance(default_status_slug, str) or not default_status_slug.strip():
        raise IntegrationConfigurationError(context=context) from None
    return azure_tenant_id, client_id, client_secret, create_missing, default_status_slug, sync_software


def _run_sync(
    tenant: _IntuneTenant,
    dry_run: bool,
    job: Job,
    integration_context: IntegrationContext | None = None,
) -> IntuneSyncResult:
    from django.conf import settings as _settings

    from assets.models import Asset, AssetType, Manufacturer, StatusLabel
    from organization.models import AssetHolder, Tenant

    integration_context = integration_context or IntegrationContext(
        provider="microsoft-graph",
        operation="sync",
        tenant_id=getattr(tenant, "pk", None),
    )
    tenant_configs = getattr(_settings, "ITAMBOX_TENANT_INTUNE_CONFIGS", {})
    config = tenant_configs.get(tenant.slug)
    if not config:
        raise IntegrationConfigurationError(context=integration_context) from None

    (
        azure_tenant_id,
        client_id,
        client_secret,
        create_missing,
        default_status_slug,
        sync_software,
    ) = _read_intune_config(config, context=integration_context)

    client = IntuneClient(
        azure_tenant_id,
        client_id,
        client_secret,
        context=integration_context,
    )

    job.append_log("Fetching managed devices from Graph API…")
    devices: list[IntuneDevicePayload] = client.get_managed_devices()
    job.append_log(f"Retrieved {len(devices)} managed device(s).")

    counts: IntuneSyncResult = {
        "devices_total": len(devices),
        "matched": 0,
        "updated": 0,
        "created": 0,
        "skipped": 0,
        "apps_upserted": 0,
        "software_degraded": 0,
    }

    for device in devices:
        serial = (device.get("serialNumber") or "").strip()

        if not serial:
            counts["skipped"] += 1
            continue

        asset = (
            Asset.objects.filter(tenant=tenant, serial_number__iexact=serial)
            .select_related("asset_type__manufacturer")
            .first()
        )

        if asset:
            counts["matched"] += 1
            _stamp_discovery_facts(asset, device, tenant, dry_run)
            counts["updated"] += 1
        elif create_missing:
            asset = _create_asset(device, tenant, default_status_slug, dry_run)
            if asset:
                counts["created"] += 1
            else:
                counts["skipped"] += 1
        else:
            counts["skipped"] += 1
            continue

        if asset and sync_software:
            n, degraded = _sync_device_software(client, device, asset, dry_run)
            counts["apps_upserted"] += n
            counts["software_degraded"] += int(degraded)

    job.append_log(
        f"Done. matched={counts['matched']} updated={counts['updated']} "
        f"created={counts['created']} skipped={counts['skipped']} "
        f"apps={counts['apps_upserted']} software_degraded={counts['software_degraded']}"
    )
    return counts


def _stamp_discovery_facts(
    asset: _IntuneAsset,
    device: IntuneDevicePayload,
    tenant: _IntuneTenant,
    dry_run: bool,
) -> None:
    """Write Intune discovery metadata into custom_field_data."""
    from organization.models import AssetHolder

    facts = {
        "intune_device_id": device.get("id", ""),
        "intune_last_sync": device.get("lastSyncDateTime", ""),
        "os_version": device.get("osVersion", ""),
    }

    upn = (device.get("userPrincipalName") or "").strip()
    if upn:
        holder = AssetHolder.objects.filter(tenant=tenant, upn__iexact=upn).first()
        facts["intune_primary_user"] = upn
        facts["intune_primary_user_matched"] = holder is not None

    data = dict(asset.custom_field_data or {})
    data.update(facts)

    if not dry_run:
        asset.custom_field_data = data
        asset.save(update_fields=["custom_field_data"])


def _create_asset(
    device: IntuneDevicePayload,
    tenant: _IntuneTenant,
    default_status_slug: str,
    dry_run: bool,
) -> _IntuneAsset | None:
    """Create a Manufacturer, AssetType (get_or_create), and Asset for a new device."""
    from assets.models import Asset, AssetType, Manufacturer, StatusLabel

    serial = (device.get("serialNumber") or "").strip()
    device_name = (device.get("deviceName") or serial or "Unknown").strip()
    manufacturer_name = (device.get("manufacturer") or "Unknown").strip()
    model_name = (device.get("model") or "Unknown").strip()

    if dry_run:
        return None

    manufacturer, _ = Manufacturer.objects.get_or_create(
        name=manufacturer_name,
        defaults={"slug": _slugify(manufacturer_name)},
    )
    asset_type, _ = AssetType.objects.get_or_create(
        manufacturer=manufacturer,
        model=model_name,
    )

    status = StatusLabel.objects.filter(slug=default_status_slug).first()

    discovery_facts = {
        "intune_device_id": device.get("id", ""),
        "intune_last_sync": device.get("lastSyncDateTime", ""),
        "os_version": device.get("osVersion", ""),
    }
    upn = (device.get("userPrincipalName") or "").strip()
    if upn:
        discovery_facts["intune_primary_user"] = upn

    asset = Asset.objects.create(
        name=device_name,
        serial_number=serial,
        asset_type=asset_type,
        status=status,
        tenant=tenant,
        custom_field_data=discovery_facts,
    )
    return asset


def _get_detected_apps_or_degrade(client: IntuneClient, device_id: str) -> tuple[list[IntuneAppPayload] | None, bool]:
    try:
        return client.get_detected_apps(device_id), False
    except (IntegrationAuthenticationError, IntegrationConfigurationError):
        raise
    except IntegrationError as exc:
        extra = exc.log_extra(object_id=device_id)
        logger.warning(
            "Optional detected-app integration degraded integration=%s",
            extra["integration"],
            extra=extra,
        )
        return None, True
    # broad except: boundary-isolation: optional detected-app discovery may fail without invalidating asset sync
    except Exception as exc:
        client_context = getattr(client, "context", None)
        base_context = (
            client_context
            if isinstance(client_context, IntegrationContext)
            else IntegrationContext(
                provider="microsoft-graph",
                operation="device_apps.list",
            )
        )
        optional_context = IntegrationContext(
            provider=base_context.provider,
            operation="device_apps.list",
            tenant_id=base_context.tenant_id,
            actor_id=base_context.actor_id,
            request_id=base_context.request_id,
        )
        unexpected = IntegrationUnexpectedError(context=optional_context)
        extra = unexpected.log_extra(object_id=device_id, exception_type=type(exc).__name__)
        logger.warning(
            "Optional detected-app integration degraded unexpectedly integration=%s",
            extra["integration"],
            extra=extra,
        )
        return None, True


def _log_software_persistence_failure(client: IntuneClient, device_id: str, exc: Exception) -> None:
    client_context = getattr(client, "context", None)
    base_context = (
        client_context
        if isinstance(client_context, IntegrationContext)
        else IntegrationContext(provider="microsoft-graph", operation="device_apps.persist")
    )
    context = IntegrationContext(
        provider=base_context.provider,
        operation="device_apps.persist",
        tenant_id=base_context.tenant_id,
        actor_id=base_context.actor_id,
        request_id=base_context.request_id,
    )
    unexpected = IntegrationUnexpectedError(context=context, cause_type=type(exc).__name__)
    extra = unexpected.log_extra(object_id=device_id)
    logger.warning(
        "Optional detected-app persistence degraded integration=%s",
        extra["integration"],
        extra=extra,
    )


def _sync_device_software(
    client: IntuneClient,
    device: IntuneDevicePayload,
    asset: _IntuneAsset,
    dry_run: bool,
) -> tuple[int, bool]:
    """Upsert InstalledSoftware records for all detected apps on a device."""
    from assets.models import Manufacturer
    from software.models import InstalledSoftware, Software

    device_id = device.get("id")
    if not device_id:
        return 0, False

    apps, degraded = _get_detected_apps_or_degrade(client, device_id)
    if apps is None:
        return 0, degraded

    count = 0
    now = timezone.now()

    for app in apps:
        app_name = (app.get("displayName") or "").strip()
        publisher = (app.get("publisher") or "").strip()
        version = (app.get("version") or "").strip()

        if not app_name:
            continue

        if dry_run:
            count += 1
            continue

        manufacturer = None
        if publisher:
            manufacturer, _ = Manufacturer.objects.get_or_create(
                name=publisher,
                defaults={"slug": _slugify(publisher)},
            )

        if manufacturer:
            software, _ = Software.objects.get_or_create(
                name=app_name,
                manufacturer=manufacturer,
            )
        else:
            # Without a publisher we can't satisfy the Software.manufacturer FK;
            # use/create an "Unknown" placeholder manufacturer.
            unknown_mfr, _ = Manufacturer.objects.get_or_create(
                name="Unknown",
                defaults={"slug": "unknown"},
            )
            software, _ = Software.objects.get_or_create(
                name=app_name,
                manufacturer=unknown_mfr,
            )

        try:
            installed, created = InstalledSoftware.objects.update_or_create(
                asset=asset,
                software=software,
                version_detected=version,
                defaults={
                    "discovered_by_agent": "Intune",
                    "last_seen_date": now,
                },
            )
            count += 1
        # broad except: boundary-isolation: optional software persistence may degrade without invalidating asset sync
        except Exception as exc:
            _log_software_persistence_failure(client, device_id, exc)
            degraded = True

    return count, degraded


def _slugify(value: str) -> str:
    """Minimal slug generation matching Django's default slugify output."""
    value = value.lower().strip()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_-]+", "-", value)
    value = value.strip("-")
    return value or "unknown"
