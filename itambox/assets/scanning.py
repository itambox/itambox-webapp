"""Shared barcode/QR code resolver for asset scanning.

Handles the multiple input shapes that a scan can produce:
- Bare asset tag:      ITM-00042
- Bare serial number:  SN-ABC123
- itambox scheme:      itambox:ITM-00042  or  itambox://asset/7
- Full / partial URL:  https://itam.example.com/assets/7/  (last path segment used as pk)
"""
from django.db.models import Q


def resolve_scanned_code(code: str):
    """Resolve a scanned code to an Asset within the current tenant scope.

    Returns the matching Asset or None if nothing is found. The query uses the
    tenant-scoped manager so cross-tenant lookups are impossible.
    """
    from assets.models import Asset

    raw = code.strip()
    if not raw:
        return None

    # itambox://asset/<pk>  — PK-based deep link
    if raw.lower().startswith('itambox://asset/'):
        pk_str = raw[len('itambox://asset/'):].strip('/')
        try:
            return Asset.objects.get(pk=int(pk_str))
        except (Asset.DoesNotExist, ValueError):
            return None

    # itambox:<tag>  — bare-tag scheme (emitted by generate_base64_barcode)
    if raw.lower().startswith('itambox:'):
        raw = raw[len('itambox:'):].strip('/')

    # Full URL — extract last non-empty path segment as pk or tag/serial
    elif raw.lower().startswith(('http://', 'https://')):
        path_part = raw.split('?')[0].split('#')[0]
        segments = [s for s in path_part.split('/') if s]
        if segments:
            candidate = segments[-1]
            # If the segment is purely numeric it may be a PK
            if candidate.isdigit():
                try:
                    return Asset.objects.get(pk=int(candidate))
                except Asset.DoesNotExist:
                    pass
            raw = candidate

    return Asset.objects.filter(
        Q(asset_tag=raw) | Q(serial_number=raw)
    ).first()
