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


def resolve_scanned_target(code, user):
    """Resolve a scanned code to a navigation target for the global scanner.

    Resolution order (tenant-scoped, and permission-gated so names never leak
    across object types):
      1. Asset by tag / serial / itambox link  -> asset detail.
      2. AssetType by EAN                       -> asset list filtered to that EAN.
      3. Component / Accessory / Consumable EAN -> that item's detail.

    Returns ``{'url': ..., 'label': ...}`` or ``None``.
    """
    from django.urls import reverse
    from urllib.parse import urlencode

    if user.has_perm('assets.view_asset'):
        asset = resolve_scanned_code(code)
        if asset is not None:
            return {'url': asset.get_absolute_url(), 'label': str(asset)}

    raw = strip_itambox_prefix(code)
    if not raw:
        return None

    # AssetType EAN -> the asset list filtered to assets of that type.
    if user.has_perm('assets.view_asset'):
        from assets.models import AssetType
        atype = AssetType.objects.filter(ean__iexact=raw).first()
        if atype is not None:
            url = "%s?%s" % (reverse('assets:asset_list'), urlencode({'ean': raw}))
            return {'url': url, 'label': str(atype)}

    # Inventory item EAN -> item detail.
    # inline import: inventory imports from assets — avoid a load-time cycle.
    from inventory.models import Component, Accessory, Consumable
    for model, perm in (
        (Component, 'inventory.view_component'),
        (Accessory, 'inventory.view_accessory'),
        (Consumable, 'inventory.view_consumable'),
    ):
        if not user.has_perm(perm):
            continue
        obj = model.objects.filter(ean__iexact=raw).first()
        if obj is not None:
            return {'url': obj.get_absolute_url(), 'label': str(obj)}

    return None



