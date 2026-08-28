"""Actor-bound audit scope, classification, and frozen-report services."""

from __future__ import annotations

from typing import Any, TypedDict

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from assets.models import Asset, StatusLabel
from compliance.models import AssetAudit, AuditSession
from core import tenant_scope
from core.context import (
    SystemAuthorizationContext,
    get_current_request_id,
    get_current_tenant,
)
from organization.models import Tenant

EXPECTED_ASSET_PERMISSION = "compliance.view_auditsession"
CLOSE_PERMISSION = "compliance.change_auditsession"
ASSET_MUTATION_PERMISSION = "assets.change_asset"
SCAN_PERMISSION = "compliance.add_assetaudit"

EXPECTED_ASSET_OPERATION = "compliance.audit.expected_assets"
CLASSIFY_OPERATION = "compliance.audit.classify"
SCAN_OPERATION = "compliance.audit.scan"
REPORT_READ_OPERATION = "compliance.audit.report.read"
CLOSE_OPERATION = "compliance.audit.close"
REHOME_OPERATION = "compliance.audit.rehome"
FLAG_MISSING_OPERATION = "compliance.audit.flag_missing"


class AuditClassification(TypedDict):
    matching: list[AssetAudit]
    mismatched: list[AssetAudit]
    surprise: list[AssetAudit]
    missing: QuerySet[Asset]


class ReconciliationReportV2(TypedDict):
    schema_version: int
    total_expected: int
    total_scanned: int
    rows: list[dict[str, Any]]


def _stored_report_error() -> ValidationError:
    return ValidationError(_("The stored reconciliation report cannot be read safely."))


def _is_authenticated_user(user: Any) -> bool:
    return user is not None and bool(getattr(user, "is_authenticated", False))


def _live_permission_tenants(user: Any, permission: str) -> frozenset[int]:
    permission_map = tenant_scope.build_accessible_tenant_permissions_map(user)
    now = timezone.now()
    return frozenset(
        tenant_id
        for tenant_id, value in permission_map.items()
        if permission in value[0] and (value[1] is None or value[1] > now)
    )


def _authorize_human_session(session: AuditSession, user: Any, permission: str) -> frozenset[int]:
    if not _is_authenticated_user(user):
        raise PermissionDenied("An authenticated audit actor is required.")
    allowed = _live_permission_tenants(user, permission)
    if session.tenant_id is not None:
        if session.tenant_id not in allowed:
            raise PermissionDenied("The actor is not authorized for this audit session tenant.")
        return frozenset({session.tenant_id})
    if not allowed:
        raise PermissionDenied("The actor is not authorized for any tenant in this audit session.")
    return allowed


def _authorize_system_session(
    session: AuditSession,
    system_authorization: SystemAuthorizationContext,
    permission: str,
    operation: str,
) -> frozenset[int]:
    if not isinstance(system_authorization, SystemAuthorizationContext):
        raise PermissionDenied("A valid issued system authorization is required.")
    current_tenant = get_current_tenant()
    if session.tenant_id is None or current_tenant is None or current_tenant.pk != session.tenant_id:
        raise PermissionDenied("System authorization must match the active session tenant.")
    if not Tenant._base_manager.filter(pk=session.tenant_id, deleted_at__isnull=True).exists():
        raise PermissionDenied("System authorization requires a live session tenant.")
    if not system_authorization.is_valid_for(
        tenant_id=session.tenant_id,
        permission=permission,
        operation=operation,
        request_id=get_current_request_id(),
    ):
        raise PermissionDenied("The system authorization is not valid for this audit operation.")
    return frozenset({session.tenant_id})


def _authorize_session(
    session: AuditSession,
    *,
    user: Any,
    system_authorization: SystemAuthorizationContext | None,
    permission: str,
    operation: str,
) -> frozenset[int]:
    if (user is None) == (system_authorization is None):
        raise PermissionDenied("Audit authorization requires exactly one actor or system authorization.")
    if user is not None:
        return _authorize_human_session(session, user, permission)
    if session.tenant_id is None:
        raise PermissionDenied("Actorless global audit operations are not supported.")
    return _authorize_system_session(session, system_authorization, permission, operation)


def _expected_assets_for_tenants(session: AuditSession, tenant_ids: frozenset[int]) -> QuerySet[Asset]:
    queryset = Asset._base_manager.filter(
        deleted_at__isnull=True,
        tenant_id__in=tenant_ids,
    ).exclude(status__type=StatusLabel.TYPE_ARCHIVED)
    if session.location_id is not None:
        return queryset.filter(location_id=session.location_id)
    return queryset.filter(
        status__type__in=[
            StatusLabel.TYPE_DEPLOYABLE,
            StatusLabel.TYPE_PENDING,
            StatusLabel.TYPE_DEPLOYED,
        ]
    )


def expected_assets_queryset(
    session: AuditSession,
    *,
    user: Any,
    system_authorization: SystemAuthorizationContext | None = None,
) -> QuerySet[Asset]:
    """Return the explicitly authorized, still-lazy expected-asset queryset."""
    tenant_ids = _authorize_session(
        session,
        user=user,
        system_authorization=system_authorization,
        permission=EXPECTED_ASSET_PERMISSION,
        operation=EXPECTED_ASSET_OPERATION,
    )
    return _expected_assets_for_tenants(session, tenant_ids)


def _classify_authorized(session: AuditSession, tenant_ids: frozenset[int]) -> AuditClassification:
    expected_ids = set(_expected_assets_for_tenants(session, tenant_ids).values_list("id", flat=True))
    audits = list(
        session.audits.filter(
            asset__deleted_at__isnull=True,
            asset__tenant_id__in=tenant_ids,
        ).select_related("asset", "location", "status", "auditor")
    )
    scanned_ids = {audit.asset_id for audit in audits}
    matching: list[AssetAudit] = []
    mismatched: list[AssetAudit] = []
    surprise: list[AssetAudit] = []

    for audit in audits:
        if audit.asset_id not in expected_ids:
            surprise.append(audit)
        elif session.location_id is None or audit.location_id == session.location_id:
            matching.append(audit)
        else:
            mismatched.append(audit)

    missing_ids = expected_ids - scanned_ids
    missing = Asset._base_manager.filter(
        deleted_at__isnull=True,
        tenant_id__in=tenant_ids,
        id__in=missing_ids,
    ).select_related("location", "status")
    return {
        "matching": matching,
        "mismatched": mismatched,
        "surprise": surprise,
        "missing": missing,
    }


def classify_session_audits(
    session: AuditSession,
    *,
    user: Any,
    system_authorization: SystemAuthorizationContext | None = None,
) -> AuditClassification:
    """Classify only assets and observations in the actor's current scope."""
    tenant_ids = _authorize_session(
        session,
        user=user,
        system_authorization=system_authorization,
        permission=EXPECTED_ASSET_PERMISSION,
        operation=CLASSIFY_OPERATION,
    )
    return _classify_authorized(session, tenant_ids)


def classify_session_after_scan(session: AuditSession, *, user: Any) -> AuditClassification:
    """Classify a basket under the actor-only scan permission."""
    tenant_ids = _authorize_session(
        session,
        user=user,
        system_authorization=None,
        permission=SCAN_PERMISSION,
        operation=SCAN_OPERATION,
    )
    return _classify_authorized(session, tenant_ids)


def _authorized_asset_tenants(user: Any) -> frozenset[int]:
    if user is None or not _is_authenticated_user(user):
        raise PermissionDenied("An authenticated audit actor is required.")
    allowed = _live_permission_tenants(user, SCAN_PERMISSION)
    if not allowed:
        raise PermissionDenied("The actor is not authorized to record audit scans.")
    return allowed


@transaction.atomic
def audit_asset(
    asset: Asset,
    *,
    user: Any,
    session: AuditSession | None = None,
    location: Any = None,
    status: Any = None,
    notes: str = "",
    verification_method: str = "manual",
    request: Any = None,
    **kwargs: Any,
) -> AssetAudit:
    """Record one actor-bound audit observation."""
    if session is not None:
        allowed_tenants = _authorize_session(
            session,
            user=user,
            system_authorization=None,
            permission=SCAN_PERMISSION,
            operation=SCAN_PERMISSION,
        )
    else:
        allowed_tenants = _authorized_asset_tenants(user)
    asset = Asset._base_manager.select_for_update().get(
        pk=asset.pk, tenant_id__in=allowed_tenants, deleted_at__isnull=True
    )
    location = location or asset.location
    status = status or asset.status
    if not location:
        raise ValidationError(_("Audit observed location must be specified."))
    if not status:
        raise ValidationError(_("Audit observed status must be specified."))
    if status.type == StatusLabel.TYPE_ARCHIVED:
        raise ValidationError(_("Archived assets cannot be audited."))
    if session and AssetAudit.objects.filter(session=session, asset=asset).exists():
        raise ValidationError(_("This asset has already been verified in this session."))

    try:
        audit_record = AssetAudit.objects.create(
            session=session,
            asset=asset,
            auditor=user,
            location=location,
            status=status,
            notes=notes,
            verification_method=verification_method,
        )
    except IntegrityError as exc:
        # Lost the race on the (session, asset) unique constraint — return the friendly
        # error rather than a 500, while preserving unrelated integrity failures.
        if "unique_session_asset" not in str(exc):
            raise
        raise ValidationError(_("This asset has already been verified in this session.")) from None

    asset.last_audited = timezone.now()
    asset.last_audited_by = user
    if session is None:
        asset.location = location
        asset.status = status
    asset.save(update_fields=["last_audited", "last_audited_by", "location", "status"])
    return audit_record


def audit_asset_from_form(asset: Asset, user: Any, location: Any, status: Any, notes: str = "", **kwargs: Any) -> dict:
    """Record the standalone detail-page verification through the same boundary."""
    allowed_tenants = _authorized_asset_tenants(user)
    session_scope = Q(tenant_id__isnull=True) | Q(tenant_id__in=allowed_tenants)
    session = (
        AuditSession.objects.filter(status="active", location=location).filter(session_scope).first()
        or AuditSession.objects.filter(status="active", location__isnull=True).filter(session_scope).first()
    )
    audit_asset(
        asset,
        user=user,
        session=session,
        location=location,
        status=status,
        notes=notes,
        verification_method="manual",
    )
    if session:
        return {"message": _("Verified inside campaign '%(name)s'.") % {"name": session.name}, "session": session}
    return {"message": _("Standalone verification recorded."), "session": None}


def _audit_to_dict(
    audit: AssetAudit, category: str, expected_location_name: str | None = None
) -> dict[str, Any] | None:
    asset = audit.asset
    tenant_id = getattr(asset, "tenant_id", None)
    if type(tenant_id) is not int or tenant_id <= 0:
        return None
    try:
        asset_url = asset.get_absolute_url()
    # broad except: render-degrade: an unavailable asset URL must not prevent the report row from rendering
    except Exception:
        asset_url = None
    return {
        "tenant_id": tenant_id,
        "category": category,
        "asset_id": audit.asset_id,
        "asset_tag": asset.asset_tag,
        "name": asset.name,
        "asset_url": asset_url,
        "observed_location_id": audit.location_id,
        "observed_location": audit.location.name if audit.location else None,
        "expected_location": expected_location_name,
        "auditor": audit.auditor.username if audit.auditor else None,
        "timestamp": audit.timestamp.isoformat(),
        "timestamp_display": audit.timestamp.strftime("%Y-%m-%d %H:%M"),
        "verification_method_display": audit.get_verification_method_display(),
    }


def _missing_asset_to_dict(asset: Asset, session_location: Any) -> dict[str, Any] | None:
    tenant_id = getattr(asset, "tenant_id", None)
    if type(tenant_id) is not int or tenant_id <= 0:
        return None
    try:
        asset_url = asset.get_absolute_url()
    # broad except: render-degrade: an unavailable asset URL must not prevent the report row from rendering
    except Exception:
        asset_url = None
    return {
        "tenant_id": tenant_id,
        "category": "missing",
        "asset_id": asset.pk,
        "asset_tag": asset.asset_tag,
        "name": asset.name,
        "asset_url": asset_url,
        "observed_location_id": None,
        "observed_location": None,
        "expected_location": session_location.name if session_location else "Global",
        "serial_number": asset.serial_number,
        "status_id": asset.status_id,
        "status_name": asset.status.name if asset.status else None,
        "status_color": asset.status.color if asset.status else None,
        "auditor": None,
        "timestamp": None,
        "verification_method_display": None,
    }


def _empty_report() -> ReconciliationReportV2:
    return {"schema_version": 2, "total_expected": 0, "total_scanned": 0, "rows": []}


def _validated_report(report: Any) -> tuple[int, list[Any]]:
    if not isinstance(report, dict) or not isinstance(report.get("rows"), list):
        raise _stored_report_error()
    version = report.get("schema_version", 1)
    if type(version) is not int or version not in (1, 2):
        raise _stored_report_error()
    return version, report["rows"]


def _read_v1_report(stored_rows: list[Any], tenant_ids: frozenset[int]) -> list[dict[str, Any]]:
    asset_ids = {
        row.get("asset_id")
        for row in stored_rows
        if isinstance(row, dict) and type(row.get("asset_id")) is int and row["asset_id"] > 0
    }
    assets = {
        asset.pk: asset
        for asset in Asset._base_manager.filter(
            pk__in=asset_ids,
            tenant_id__in=tenant_ids,
            deleted_at__isnull=True,
        )
    }
    rows: list[dict[str, Any]] = []
    for stored_row in stored_rows:
        if not isinstance(stored_row, dict):
            continue
        category = stored_row.get("category")
        if category not in {"matching", "mismatched", "missing", "surprise"}:
            raise _stored_report_error()
        asset = assets.get(stored_row.get("asset_id"))
        if asset is None:
            continue
        row = dict(stored_row)
        row["tenant_id"] = asset.tenant_id
        rows.append(row)
    return rows


def _read_v2_report(session: AuditSession, stored_rows: list[Any], tenant_ids: frozenset[int]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stored_row in stored_rows:
        if not isinstance(stored_row, dict):
            raise _stored_report_error()
        tenant_id = stored_row.get("tenant_id")
        asset_id = stored_row.get("asset_id")
        category = stored_row.get("category")
        if type(tenant_id) is not int or tenant_id <= 0 or type(asset_id) is not int or asset_id <= 0:
            raise _stored_report_error()
        if category not in {"matching", "mismatched", "missing", "surprise"}:
            raise _stored_report_error()
        if tenant_id not in tenant_ids or (session.tenant_id is not None and tenant_id != session.tenant_id):
            continue
        rows.append(dict(stored_row))
    return rows


def _report_with_derived_totals(rows: list[dict[str, Any]]) -> ReconciliationReportV2:
    return {
        "schema_version": 2,
        "total_expected": sum(row.get("category") in {"matching", "mismatched", "missing"} for row in rows),
        "total_scanned": sum(row.get("category") in {"matching", "mismatched", "surprise"} for row in rows),
        "rows": rows,
    }


def _read_report_for_tenants(session: AuditSession, tenant_ids: frozenset[int]) -> ReconciliationReportV2:
    if session.reconciliation_report is None:
        return _empty_report()
    version, stored_rows = _validated_report(session.reconciliation_report)
    rows = (
        _read_v1_report(stored_rows, tenant_ids) if version == 1 else _read_v2_report(session, stored_rows, tenant_ids)
    )
    return _report_with_derived_totals(rows)


def read_reconciliation_report(
    session: AuditSession,
    *,
    user: Any,
    system_authorization: SystemAuthorizationContext | None = None,
) -> ReconciliationReportV2:
    """Read and scope a stored v1/v2 report for the current actor."""
    tenant_ids = _authorize_session(
        session,
        user=user,
        system_authorization=system_authorization,
        permission=EXPECTED_ASSET_PERMISSION,
        operation=REPORT_READ_OPERATION,
    )
    return _read_report_for_tenants(session, tenant_ids)


def _all_expected_assets(session: AuditSession) -> QuerySet[Asset]:
    queryset = Asset._base_manager.filter(deleted_at__isnull=True).exclude(status__type=StatusLabel.TYPE_ARCHIVED)
    if session.location_id is not None:
        return queryset.filter(location_id=session.location_id)
    return queryset.filter(
        status__type__in=[
            StatusLabel.TYPE_DEPLOYABLE,
            StatusLabel.TYPE_PENDING,
            StatusLabel.TYPE_DEPLOYED,
        ]
    )


def _close_represented_tenants(session: AuditSession) -> frozenset[int]:
    if session.tenant_id is None:
        expected_queryset = _all_expected_assets(session)
    else:
        expected_queryset = _expected_assets_for_tenants(session, frozenset({session.tenant_id}))
    expected_tenants = set(expected_queryset.values_list("tenant_id", flat=True).distinct())
    observed_tenants = set(
        AssetAudit.objects.filter(session=session, asset__isnull=False)
        .values_list("asset__tenant_id", flat=True)
        .distinct()
    )
    return frozenset(expected_tenants | observed_tenants)


def _build_close_report(
    session: AuditSession, classified: AuditClassification
) -> tuple[ReconciliationReportV2, list[dict[str, Any]], list[Asset]]:
    expected_location_name = session.location.name if session.location_id else "Global"
    rows: list[dict[str, Any]] = []
    for audit in classified["matching"]:
        row = _audit_to_dict(audit, "matching")
        if row is not None:
            rows.append(row)
    for audit in classified["mismatched"]:
        row = _audit_to_dict(audit, "mismatched", expected_location_name)
        if row is not None:
            rows.append(row)
    for audit in classified["surprise"]:
        row = _audit_to_dict(audit, "surprise")
        if row is not None:
            rows.append(row)
    missing_assets = list(classified["missing"])
    for asset in missing_assets:
        row = _missing_asset_to_dict(asset, session.location if session.location_id else None)
        if row is not None:
            rows.append(row)
    return _report_with_derived_totals(rows), rows, missing_assets


def _close_result(
    classified: AuditClassification,
    report: ReconciliationReportV2,
    missing_assets: list[Asset],
) -> dict[str, Any]:
    return {
        "total_expected": report["total_expected"],
        "total_scanned": report["total_scanned"],
        "matching_count": sum(row["category"] == "matching" for row in report["rows"]),
        "mismatch_list": [audit.asset for audit in classified["mismatched"] if audit.asset is not None],
        "surprise_list": [audit.asset for audit in classified["surprise"] if audit.asset is not None],
        "missing_list": missing_assets,
    }


@transaction.atomic
def close_audit_session(
    session: AuditSession,
    *,
    user: Any,
    system_authorization: SystemAuthorizationContext | None = None,
    request: Any = None,
    notes: str = "",
    **kwargs: Any,
) -> dict[str, Any]:
    """Close a session only after full represented-tenant authorization."""
    tenant_ids = _authorize_session(
        session,
        user=user,
        system_authorization=system_authorization,
        permission=CLOSE_PERMISSION,
        operation=CLOSE_OPERATION,
    )
    if session.status == "completed":
        raise ValidationError(_("This audit campaign is already closed."))

    represented = _close_represented_tenants(session)
    if session.tenant_id is not None and (represented - {session.tenant_id}):
        raise PermissionDenied("A tenant-bound audit contains an observation outside its tenant.")
    if not represented.issubset(tenant_ids):
        raise PermissionDenied("The actor is not authorized for every tenant represented by this audit.")

    classified = _classify_authorized(session, tenant_ids)
    report, _rows, missing_assets = _build_close_report(session, classified)
    session.status = "completed"
    session.completed_at = timezone.now()
    session.reconciliation_report = report
    session.save(update_fields=["status", "completed_at", "reconciliation_report"])
    return _close_result(classified, report, missing_assets)


def _authorize_asset_mutation(
    session: AuditSession,
    *,
    user: Any,
    system_authorization: SystemAuthorizationContext | None,
    operation: str,
) -> frozenset[int]:
    return _authorize_session(
        session,
        user=user,
        system_authorization=system_authorization,
        permission=ASSET_MUTATION_PERMISSION,
        operation=operation,
    )


@transaction.atomic
def rehome_audit_session_mismatches(
    session: AuditSession,
    *,
    user: Any,
    system_authorization: SystemAuthorizationContext | None = None,
    request: Any = None,
    **kwargs: Any,
) -> None:
    """Rehome only filtered, live report rows within the actor's scope."""
    tenant_ids = _authorize_asset_mutation(
        session,
        user=user,
        system_authorization=system_authorization,
        operation=REHOME_OPERATION,
    )
    if session.status != "completed":
        raise ValidationError(_("Audit sessions must be closed before bulk re-homing reconciliation."))
    report = _read_report_for_tenants(session, tenant_ids)
    mismatch_ids = [row["asset_id"] for row in report["rows"] if row["category"] == "mismatched"]
    assets = list(
        Asset._base_manager.select_for_update().filter(
            deleted_at__isnull=True,
            pk__in=mismatch_ids,
            tenant_id__in=tenant_ids,
        )
    )
    _authorize_asset_mutation(
        session,
        user=user,
        system_authorization=system_authorization,
        operation=REHOME_OPERATION,
    )
    for asset in assets:
        asset.snapshot()
        asset.location = session.location
        asset.save(update_fields=["location"])


@transaction.atomic
def flag_missing_assets(
    session: AuditSession,
    *,
    user: Any,
    system_authorization: SystemAuthorizationContext | None = None,
    request: Any = None,
    **kwargs: Any,
) -> dict[str, int]:
    """Flag filtered missing report rows while preserving changed targets."""
    tenant_ids = _authorize_asset_mutation(
        session,
        user=user,
        system_authorization=system_authorization,
        operation=FLAG_MISSING_OPERATION,
    )
    if session.status != "completed":
        raise ValidationError(_("Audit session must be closed before flagging missing assets."))
    report = _read_report_for_tenants(session, tenant_ids)
    missing_rows = [row for row in report["rows"] if row["category"] == "missing"]
    if not missing_rows:
        return {"flagged": 0, "skipped": 0}

    missing_status, _created = StatusLabel.objects.get_or_create(
        name="Missing",
        defaults={"type": StatusLabel.TYPE_UNDEPLOYABLE, "color": "dc3545"},
    )
    if missing_status.type != StatusLabel.TYPE_UNDEPLOYABLE:
        missing_status.type = StatusLabel.TYPE_UNDEPLOYABLE
        missing_status.save(update_fields=["type"])
    assets = {
        asset.pk: asset
        for asset in Asset._base_manager.filter(
            deleted_at__isnull=True,
            pk__in=[row["asset_id"] for row in missing_rows],
            tenant_id__in=tenant_ids,
        ).select_related("status")
    }
    _authorize_asset_mutation(
        session,
        user=user,
        system_authorization=system_authorization,
        operation=FLAG_MISSING_OPERATION,
    )
    flagged = 0
    skipped = 0
    for row in missing_rows:
        asset = assets.get(row["asset_id"])
        if asset is None or (row.get("status_id") is not None and asset.status_id != row["status_id"]):
            skipped += 1
            continue
        asset.snapshot()
        asset.status = missing_status
        asset.save(update_fields=["status"])
        flagged += 1
    return {"flagged": flagged, "skipped": skipped}
