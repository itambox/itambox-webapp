from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from assets.models import Asset, StatusLabel
from compliance.models import AuditSession, AssetAudit


def classify_session_audits(session: AuditSession) -> dict:
    """Classify all audit records for a session into four categories.

    Uses the AUDIT records' observed data (immutable evidence), never the
    assets' current/live fields.

    Returns a dict with keys:
      matching    — list of AssetAudit (expected + observed location matches session)
      mismatched  — list of AssetAudit (expected + observed location differs from session)
      surprise    — list of AssetAudit (scanned but not in expected_ids)
      missing     — Asset queryset (expected but not scanned)
    """
    expected_ids = set(session.expected_assets_queryset.values_list('id', flat=True))
    audits = list(
        session.audits.select_related('asset', 'location', 'status', 'auditor')
    )
    scanned_ids = {a.asset_id for a in audits}

    matching = []
    mismatched = []
    surprise = []

    for audit in audits:
        if audit.asset_id not in expected_ids:
            surprise.append(audit)
        elif session.location_id is None:
            # Global stocktake: no location expectation, all scanned assets match.
            matching.append(audit)
        elif audit.location_id == session.location_id:
            matching.append(audit)
        else:
            mismatched.append(audit)

    missing = Asset.objects.filter(id__in=(expected_ids - scanned_ids)).select_related('location', 'status')

    return {
        'matching': matching,
        'mismatched': mismatched,
        'surprise': surprise,
        'missing': missing,
    }


@transaction.atomic
def audit_asset(asset: Asset, user=None, session=None, location=None, status=None, notes='', verification_method='manual', request=None, **kwargs) -> AssetAudit:
    location = location or asset.location
    status = status or asset.status

    if not location:
        raise ValidationError("Audit observed location must be specified.")
    if not status:
        raise ValidationError("Audit observed status must be specified.")

    if status and status.type == StatusLabel.TYPE_ARCHIVED:
        raise ValidationError("Archived assets cannot be audited.")

    if session and AssetAudit.objects.filter(session=session, asset=asset).exists():
        raise ValidationError("This asset has already been verified in this session.")

    audit_record = AssetAudit.objects.create(
        session=session,
        asset=asset,
        auditor=user,
        location=location,
        status=status,
        notes=notes,
        verification_method=verification_method
    )

    asset.last_audited = timezone.now()
    asset.last_audited_by = user

    if not session:
        asset.location = location
        asset.status = status

    asset.save(update_fields=['last_audited', 'last_audited_by', 'location', 'status'])

    return audit_record


def _audit_to_dict(audit, category: str, expected_location_name: str = None) -> dict:
    """Serialize one AssetAudit to a JSON-safe dict for the stored report."""
    from django.urls import reverse
    try:
        asset_url = audit.asset.get_absolute_url()
    except Exception:
        asset_url = None
    return {
        'category': category,
        'asset_id': audit.asset_id,
        'asset_tag': audit.asset.asset_tag,
        'name': audit.asset.name,
        'asset_url': asset_url,
        'observed_location_id': audit.location_id,
        'observed_location': audit.location.name if audit.location else None,
        'expected_location': expected_location_name,
        'auditor': audit.auditor.username if audit.auditor else None,
        'timestamp': audit.timestamp.isoformat(),
        'timestamp_display': audit.timestamp.strftime("%Y-%m-%d %H:%M"),
        'verification_method_display': audit.get_verification_method_display(),
    }


def _missing_asset_to_dict(asset, session_location) -> dict:
    from django.urls import reverse
    try:
        asset_url = asset.get_absolute_url()
    except Exception:
        asset_url = None
    return {
        'category': 'missing',
        'asset_id': asset.pk,
        'asset_tag': asset.asset_tag,
        'name': asset.name,
        'asset_url': asset_url,
        'observed_location_id': None,
        'observed_location': None,
        'expected_location': session_location.name if session_location else 'Global',
        'serial_number': asset.serial_number if hasattr(asset, 'serial_number') else None,
        'status_name': asset.status.name if asset.status else None,
        'status_color': asset.status.color if asset.status else None,
        'auditor': None,
        'timestamp': None,
        'verification_method_display': None,
    }


@transaction.atomic
def close_audit_session(session: AuditSession, user=None, request=None, notes='', **kwargs) -> dict:
    if session.status == 'completed':
        raise ValidationError("This audit campaign is already closed.")

    session.status = 'completed'
    session.completed_at = timezone.now()
    session.save()

    result = classify_session_audits(session)

    # Build the frozen report — denormalized so it stays readable after asset deletions.
    expected_location_name = session.location.name if session.location else 'Global'
    rows = []
    for audit in result['matching']:
        rows.append(_audit_to_dict(audit, 'matching'))
    for audit in result['mismatched']:
        rows.append(_audit_to_dict(audit, 'mismatched', expected_location_name))
    for audit in result['surprise']:
        rows.append(_audit_to_dict(audit, 'surprise'))
    for asset in result['missing']:
        rows.append(_missing_asset_to_dict(asset, session.location))

    total_scanned = len(result['matching']) + len(result['mismatched']) + len(result['surprise'])
    total_expected = len(result['matching']) + len(result['mismatched']) + len(list(result['missing']))

    report = {
        'total_expected': total_expected,
        'total_scanned': total_scanned,
        'rows': rows,
    }
    session.reconciliation_report = report
    session.save(update_fields=['reconciliation_report'])

    return {
        'total_expected': total_expected,
        'total_scanned': total_scanned,
        'matching_count': len(result['matching']),
        'mismatch_list': [a.asset for a in result['mismatched']],
        'surprise_list': [a.asset for a in result['surprise']],
        'missing_list': list(result['missing']),
    }


@transaction.atomic
def rehome_audit_session_mismatches(session: AuditSession, user=None, request=None, **kwargs):
    """Move mismatched assets to the campaign location using frozen audit evidence.

    Drives from the stored reconciliation_report when available so the set of
    assets moved matches exactly what was recorded at close time — not a
    re-evaluation of current asset state.
    """
    if session.status != 'completed':
        raise ValidationError("Audit sessions must be closed before bulk re-homing reconciliation.")

    if session.reconciliation_report:
        mismatch_ids = [
            row['asset_id']
            for row in session.reconciliation_report.get('rows', [])
            if row.get('category') == 'mismatched'
        ]
        assets = Asset.objects.filter(pk__in=mismatch_ids)
    else:
        result = classify_session_audits(session)
        assets = [audit.asset for audit in result['mismatched']]

    for asset in assets:
        asset.snapshot()
        asset.location = session.location
        asset.save(update_fields=['location'])
