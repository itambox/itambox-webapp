from django.db import transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import Asset, AuditSession, AssetAudit, StatusLabel, ActivityLog

@transaction.atomic
def audit_asset(asset: Asset, user=None, session=None, location=None, status=None, notes='', verification_method='manual', request=None, **kwargs) -> AssetAudit:
    # 1. Fallback to current asset parameters if not overridden
    location = location or asset.location
    status = status or asset.status

    if not location:
        raise ValidationError("Audit observed location must be specified.")
    if not status:
        raise ValidationError("Audit observed status must be specified.")

    # 2. Prevent auditing archived assets
    if status and status.type == StatusLabel.TYPE_ARCHIVED:
        raise ValidationError("Archived assets cannot be audited.")

    # 2.5 Prevent double scanning
    if session and AssetAudit.objects.filter(session=session, asset=asset).exists():
        raise ValidationError("This asset has already been verified in this session.")

    # 3. Create the historical AssetAudit log record
    audit_record = AssetAudit.objects.create(
        session=session,
        asset=asset,
        auditor=user,
        location=location,
        status=status,
        notes=notes,
        verification_method=verification_method
    )

    # 4. Stamp current audit dates onto the core Asset model
    asset.last_audited = timezone.now()
    asset.last_audited_by = user
    
    # If audited outside a session, or if explicit override is requested:
    # We sync the asset location and status in the database immediately
    if not session:
        asset.location = location
        asset.status = status
    
    asset.save(update_fields=['last_audited', 'last_audited_by', 'location', 'status'])

    # Log to ActivityLog
    ActivityLog.objects.create(
        asset=asset,
        action='audited',
        user=user,
        notes=f"Physically verified at location '{location.name}' with status '{status.name}'. Method: {verification_method}."
    )
    return audit_record

@transaction.atomic
def close_audit_session(session: AuditSession, user=None, request=None, notes='', **kwargs) -> dict:
    if session.status == 'completed':
        raise ValidationError("This audit campaign is already closed.")

    session.status = 'completed'
    session.completed_at = timezone.now()
    session.save()

    # Build reconciliation campaign reports
    expected_ids = set(session.expected_assets_queryset.values_list('id', flat=True))
    audited_relations = session.audits.select_related('asset', 'location')
    scanned_ids = set(audited_relations.values_list('asset_id', flat=True))

    mismatches = []
    matching = []
    
    for audit in audited_relations:
        # Detected location discrepancy (scanned physically at session location but recorded elsewhere in DB)
        if audit.asset.location != session.location:
            mismatches.append(audit.asset)
        else:
            matching.append(audit.asset)

    missing = Asset.objects.filter(id__in=(expected_ids - scanned_ids))

    return {
        'total_expected': len(expected_ids),
        'total_scanned': len(scanned_ids),
        'matching_count': len(matching),
        'mismatch_list': mismatches,
        'missing_list': list(missing)
    }

@transaction.atomic
def rehome_audit_session_mismatches(session: AuditSession, user=None, request=None, **kwargs):
    """
    Bulk reconciles and resolves location discrepancies by re-homing expected 
    database locations to match physical observed locations scanned during the campaign.
    """
    if session.status != 'completed':
        raise ValidationError("Audit sessions must be closed before bulk re-homing reconciliation.")
    
    # We re-home only those audited whose asset's registered location is different from the session's target location
    mismatched_audits = session.audits.select_related('asset').exclude(asset__location=session.location)
    for audit in mismatched_audits:
        asset = audit.asset
        original_loc = asset.location
        asset.location = session.location
        asset.save(update_fields=['location'])
        
        ActivityLog.objects.create(
            asset=asset,
            action='audited',
            user=user,
            notes=f"Reconciled location mismatch during audit session '{session.name}'. Bulk re-homed from '{original_loc.name if original_loc else 'None'}' to verified physical location '{session.location.name if session.location else 'None'}'."
        )
