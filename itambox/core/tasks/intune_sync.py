"""
Intune discovery sync task.

Matches Graph API managed devices to Assets by serial number, stamps
discovery facts into custom_field_data, optionally creates new assets,
and upserts InstalledSoftware records.

Discovery proposes, humans dispose: a matched userPrincipalName is recorded
in custom_field_data as intune_primary_user rather than triggering an
automatic checkout, because assignment carries compliance side-effects.
"""

import logging
from django.conf import settings
from django.utils import timezone

from core.models import Job
from core.tasks.context import TaskContext
from core.integrations.intune import IntuneClient

logger = logging.getLogger(__name__)


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

        job.mark_running()
        if dry_run:
            job.append_log("[dry-run] No writes will be performed.")

        try:
            counts = _run_sync(ctx.tenant, dry_run, job)
            job.mark_completed(result=counts)
        except Exception as exc:
            logger.exception("Intune sync failed for tenant %s", tenant_id)
            job.mark_failed(str(exc))


def _run_sync(tenant, dry_run: bool, job: Job) -> dict:
    from django.conf import settings as _settings
    from organization.models import Tenant, AssetHolder
    from assets.models import Asset, Manufacturer, AssetType, StatusLabel

    tenant_configs = getattr(_settings, "ITAMBOX_TENANT_INTUNE_CONFIGS", {})
    config = tenant_configs.get(tenant.slug)
    if not config:
        raise ValueError(f"No ITAMBOX_TENANT_INTUNE_CONFIGS entry for tenant '{tenant.slug}'.")

    azure_tenant_id = config["azure_tenant_id"]
    client_id = config["client_id"]
    client_secret = config["client_secret"]
    create_missing = bool(config.get("create_missing", False))
    default_status_slug = config.get("default_status", "deployable")
    sync_software = bool(config.get("sync_software", True))

    client = IntuneClient(azure_tenant_id, client_id, client_secret)

    job.append_log("Fetching managed devices from Graph API…")
    devices = client.get_managed_devices()
    job.append_log(f"Retrieved {len(devices)} managed device(s).")

    counts = {
        "devices_total": len(devices),
        "matched": 0,
        "updated": 0,
        "created": 0,
        "skipped": 0,
        "apps_upserted": 0,
    }

    for device in devices:
        serial = (device.get("serialNumber") or "").strip()
        device_name = (device.get("deviceName") or "").strip()

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
            n = _sync_device_software(client, device, asset, dry_run)
            counts["apps_upserted"] += n

    job.append_log(
        f"Done. matched={counts['matched']} updated={counts['updated']} "
        f"created={counts['created']} skipped={counts['skipped']} "
        f"apps={counts['apps_upserted']}"
    )
    return counts


def _stamp_discovery_facts(asset, device: dict, tenant, dry_run: bool) -> None:
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


def _create_asset(device: dict, tenant, default_status_slug: str, dry_run: bool):
    """Create a Manufacturer, AssetType (get_or_create), and Asset for a new device."""
    from assets.models import Asset, Manufacturer, AssetType, StatusLabel

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


def _sync_device_software(client: IntuneClient, device: dict, asset, dry_run: bool) -> int:
    """Upsert InstalledSoftware records for all detected apps on a device."""
    from assets.models import Manufacturer
    from software.models import Software, InstalledSoftware

    device_id = device.get("id")
    if not device_id:
        return 0

    try:
        apps = client.get_detected_apps(device_id)
    except Exception as exc:
        logger.warning("Could not fetch apps for device %s: %s", device_id, exc)
        return 0

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
        except Exception as exc:
            logger.warning("InstalledSoftware upsert failed (%s, %s, %s): %s", asset, software, version, exc)

    return count


def _slugify(value: str) -> str:
    """Minimal slug generation matching Django's default slugify output."""
    import re
    value = value.lower().strip()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_-]+", "-", value)
    value = value.strip("-")
    return value or "unknown"
