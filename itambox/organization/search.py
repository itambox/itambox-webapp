# itambox/organization/search.py
from core.search import SearchIndex, register_search
from .models import Site, Location, Region, SiteGroup, Tenant, TenantGroup, AssetHolder

@register_search()
class SiteIndex(SearchIndex):
    model = Site
    fields = (
        'name', 'slug', 'description', 'physical_address', 'comments',
        'region__name', 'group__name', 'tenant__name' # Search related names
    )
    order_by = ('name',)

@register_search()
class LocationIndex(SearchIndex):
    model = Location
    fields = (
        'name', 'slug', 'description',
        'site__name', 'tenant__name' # Search related names
    )
    order_by = ('site__name', 'name')

@register_search()
class RegionIndex(SearchIndex):
    model = Region
    fields = ('name', 'slug', 'description',)
    order_by = ('name',)

@register_search()
class SiteGroupIndex(SearchIndex):
    model = SiteGroup
    fields = ('name', 'slug', 'description',)
    order_by = ('name',)

@register_search()
class TenantIndex(SearchIndex):
    model = Tenant
    fields = ('name', 'slug', 'description',
              'group__name' # Search related names
             )
    order_by = ('name',)

@register_search()
class TenantGroupIndex(SearchIndex):
    model = TenantGroup
    fields = ('name', 'slug', 'description',)
    order_by = ('name',)

@register_search()
class AssetHolderIndex(SearchIndex):
    model = AssetHolder
    fields = ('upn', 'first_name', 'last_name', 'email', 'description',
              'tenant__name' # Search related names
             )
    order_by = ('upn',) 