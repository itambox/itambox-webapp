"""Shared barcode/QR code resolver for asset scanning.

Handles the multiple input shapes that a scan can produce:
- Bare asset tag:      ITM-00042
- Bare serial number:  SN-ABC123
- itambox scheme:      itambox:ITM-00042  or  itambox://asset/7
- Full / partial URL:  https://itam.example.com/assets/7/  (last path segment used as pk)
"""
from django.db.models import Q


def strip_itambox_prefix(code: str) -> str:
    """Strip standard itambox:// or itambox: prefix from the scanned code, leaving the bare tag/serial."""
    if not code:
        return ""
    # Defensive cleaning: strip spaces, quotes, BOM, zero-width spaces, and normalize colons
    raw = code.strip().replace('\ufeff', '').replace('\u200b', '')
    raw = raw.replace('：', ':')
    raw = raw.strip('"\' ')

    if raw.lower().startswith('itambox://asset/'):
        return raw

    if raw.lower().startswith('itambox://'):
        raw = raw[len('itambox://'):].strip('/ "\'')
    elif raw.lower().startswith('itambox:'):
        raw = raw[len('itambox:'):].strip('/ "\'')
    return raw


def resolve_scanned_code(code: str):
    """Resolve a scanned code to an Asset within the current tenant scope.

    Returns the matching Asset or None if nothing is found. The query uses the
    tenant-scoped manager so cross-tenant lookups are impossible.
    """
    from assets.models import Asset

    raw = strip_itambox_prefix(code)
    if not raw:
        return None

    # itambox://asset/<pk>  — PK-based deep link
    if raw.lower().startswith('itambox://asset/'):
        pk_str = raw[len('itambox://asset/'):].strip('/ "\'')
        try:
            return Asset.objects.get(pk=int(pk_str))
        except (Asset.DoesNotExist, ValueError):
            return None

    # Full URL — extract last non-empty path segment as pk or tag/serial
    if raw.lower().startswith(('http://', 'https://')):
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
        Q(asset_tag__iexact=raw) | Q(serial_number__iexact=raw)
    ).first()



