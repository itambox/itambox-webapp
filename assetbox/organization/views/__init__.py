from .site_views import (
    SiteListView, SiteDetailView, SiteEditView, SiteDeleteView, SiteBulkEditView, SiteBulkDeleteView,
)
from .region_views import (
    RegionListView, RegionDetailView, RegionEditView, RegionDeleteView, RegionBulkEditView, RegionBulkDeleteView,
)
from .sitegroup_views import (
    SiteGroupListView, SiteGroupDetailView, SiteGroupEditView, SiteGroupDeleteView,
)
from .location_views import (
    LocationListView, LocationDetailView, LocationEditView, LocationDeleteView,
    LocationImportView, LocationBulkEditView, LocationBulkDeleteView,
)
from .tenantgroup_views import (
    TenantGroupListView, TenantGroupDetailView, TenantGroupEditView, TenantGroupDeleteView,
)
from .tenant_views import (
    TenantListView, TenantDetailView, TenantEditView, TenantDeleteView, TenantBulkEditView, TenantBulkDeleteView,
)
from .assetholder_views import (
    AssetHolderListView, AssetHolderDetailView, AssetHolderEditView, AssetHolderDeleteView,
    AssetHolderAssignmentListView, AssetHolderImportView, AssetHolderBulkEditView, AssetHolderBulkDeleteView,
)
from .contact_views import (
    ContactListView, ContactDetailView, ContactEditView, ContactDeleteView, ContactBulkEditView, ContactBulkDeleteView,
)
from .contactrole_views import (
    ContactRoleListView, ContactRoleDetailView, ContactRoleEditView, ContactRoleDeleteView,
    ContactAssignmentCreateView, ContactAssignmentDeleteView,
    ContactRoleBulkEditView, ContactRoleBulkDeleteView,
)
