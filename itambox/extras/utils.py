def deep_merge(dict_a: dict, dict_b: dict) -> dict:
    """
    Recursively merges two dictionaries.
    Values from dict_b take precedence over dict_a in conflict resolutions.
    """
    result = dict_a.copy()
    for key, val in dict_b.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = deep_merge(result[key], val)
        else:
            result[key] = val
    return result


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
