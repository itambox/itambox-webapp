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


@transaction.atomic
def close_audit_session(session: AuditSession, user=None, request=None, notes='', **kwargs) -> dict:
    if session.status == 'completed':
        raise ValidationError("This audit campaign is already closed.")

    session.status = 'completed'
    session.completed_at = timezone.now()
    session.save()

    result = classify_session_audits(session)

    return {
        'total_expected': len(set(session.expected_assets_queryset.values_list('id', flat=True))),
        'total_scanned': len(result['matching']) + len(result['mismatched']) + len(result['surprise']),
        'matching_count': len(result['matching']),
        'mismatch_list': [a.asset for a in result['mismatched']],
        'surprise_list': [a.asset for a in result['surprise']],
        'missing_list': list(result['missing']),
    }


@transaction.atomic
def rehome_audit_session_mismatches(session: AuditSession, user=None, request=None, **kwargs):
    """Move mismatched assets to the campaign location using frozen audit evidence."""
    if session.status != 'completed':
        raise ValidationError("Audit sessions must be closed before bulk re-homing reconciliation.")

    result = classify_session_audits(session)
    for audit in result['mismatched']:
        asset = audit.asset
        asset.snapshot()
        asset.location = session.location
        asset.save(update_fields=['location'])
