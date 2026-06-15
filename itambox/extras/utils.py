from itambox.dictutils import deep_merge


def resolve_generic_items(rows, toggle_url_name=None):
    """
    Resolve a list of Bookmark or ObjectWatch rows to dicts suitable for templates.
    Rows must have .user, .model (ContentType FK), .object_id, .created, .pk.
    Objects that no longer exist under the active tenant scope are silently skipped.
    """
    from django.urls import reverse

    by_ct = {}
    for row in rows:
        by_ct.setdefault(row.model, []).append(row)

    resolved = {}
    for ct, ct_rows in by_ct.items():
        model_class = ct.model_class()
        if not model_class:
            continue
        ids = [r.object_id for r in ct_rows]
        for obj in model_class.objects.filter(pk__in=ids):
            resolved[(ct.id, obj.pk)] = obj

    items = []
    for row in rows:
        obj = resolved.get((row.model.id, row.object_id))
        if not obj:
            continue
        url = '#'
        if hasattr(obj, 'get_absolute_url'):
            try:
                url = obj.get_absolute_url()
            except Exception:
                pass
        item = {
            'id': row.pk,
            'type_name': obj._meta.verbose_name.title(),
            'name': str(obj),
            'url': url,
            'created': row.created,
            'content_type_id': row.model.pk,
            'object_id': row.object_id,
        }
        if toggle_url_name:
            try:
                item['toggle_url'] = reverse(toggle_url_name, kwargs={
                    'content_type_id': row.model.pk,
                    'object_id': row.object_id,
                })
            except Exception:
                item['toggle_url'] = '#'
        items.append(item)
    return items


def get_context_for_asset(asset) -> dict:
    """
    Builds the consolidated config context for an asset by walking up
    the organizational hierarchy and merging active config contexts by weight.
    """
    from extras.models import ConfigContext
    from django.db.models import Q

    # Resolve locations hierarchy (Asset -> Location -> parent Location -> ...)
    locations = []
    loc = asset.location
    while loc is not None:
        locations.append(loc)
        loc = loc.parent

    # Resolve sites
    sites = []
    if asset.location and asset.location.site:
        sites.append(asset.location.site)

    # Resolve regions hierarchy (Site -> Region -> parent Region -> ...)
    regions = []
    if asset.location and asset.location.site and asset.location.site.region:
        reg = asset.location.site.region
        while reg is not None:
            regions.append(reg)
            reg = reg.parent

    # Resolve tenants
    tenants = []
    if asset.tenant:
        tenants.append(asset.tenant)

    q_filter = Q()
    if locations:
        q_filter |= Q(locations__in=locations)
    if sites:
        q_filter |= Q(sites__in=sites)
    if regions:
        q_filter |= Q(regions__in=regions)
    if tenants:
        q_filter |= Q(tenants__in=tenants)

    # If no filter matches, return empty dictionary immediately
    if not q_filter:
        return {}

    # Query matching contexts sorted ascending by weight so higher weight overrides lower weight
    contexts = ConfigContext.objects.filter(q_filter).distinct().order_by('weight', 'name')

    merged_data = {}
    for ctx in contexts:
        merged_data = deep_merge(merged_data, ctx.data)

    return merged_data
