from .assetholder_views import (
    AssetHolderBulkDeleteView,
    AssetHolderBulkEditView,
    AssetHolderDeleteView,
    AssetHolderDetailView,
    AssetHolderEditView,
    AssetHolderListView,
)
from .contact_views import (
    ContactBulkDeleteView,
    ContactBulkEditView,
    ContactCloneView,
    ContactDeleteView,
    ContactDetailView,
    ContactEditView,
    ContactListView,
)
from .contactrole_views import (
    ContactAssignmentCreateView,
    ContactAssignmentDeleteView,
    ContactRoleBulkDeleteView,
    ContactRoleBulkEditView,
    ContactRoleCloneView,
    ContactRoleDeleteView,
    ContactRoleDetailView,
    ContactRoleEditView,
    ContactRoleListView,
)
from .costcenter_views import (
    CostCenterBulkDeleteView,
    CostCenterBulkEditView,
    CostCenterCloneView,
    CostCenterDeleteView,
    CostCenterDetailView,
    CostCenterEditView,
    CostCenterListView,
)
from .location_views import (
    LocationBulkDeleteView,
    LocationBulkEditView,
    LocationCloneView,
    LocationDeleteView,
    LocationDetailView,
    LocationEditView,
    LocationListView,
)
from .membership_views import (
    MembershipBulkDeleteView,
    MembershipBulkEditView,
    MembershipCreateView,
    MembershipDeleteView,
    MembershipDetailView,
    MembershipEditView,
    MembershipListView,
    MembershipSendResetView,
)
from .provider_views import (
    TechnicianQuickAddView,
)
from .region_views import (
    RegionBulkDeleteView,
    RegionBulkEditView,
    RegionCloneView,
    RegionDeleteView,
    RegionDetailView,
    RegionEditView,
    RegionListView,
)
from .resource_grant_views import (  # noqa: F401
    TenantResourceGrantCreateView,
    TenantResourceGrantExpiryRunDetailView,
    TenantResourceGrantExpiryRunListView,
    TenantResourceGrantListView,
    TenantResourceGrantRevokeView,
)
from .role_views import (
    RoleAssignUsersView,
    RoleBulkDeleteView,
    RoleCloneView,
    RoleDeleteView,
    RoleDetailView,
    RoleEditView,
    RoleListView,
)
from .site_views import (
    SiteBulkDeleteView,
    SiteBulkEditView,
    SiteCloneView,
    SiteDeleteView,
    SiteDetailView,
    SiteEditView,
    SiteListView,
)
from .sitegroup_views import (
    SiteGroupCloneView,
    SiteGroupDeleteView,
    SiteGroupDetailView,
    SiteGroupEditView,
    SiteGroupListView,
)
from .tenant_views import (
    TenantAccessView,
    TenantBulkDeleteView,
    TenantBulkEditView,
    TenantDeleteView,
    TenantDetailView,
    TenantEditView,
    TenantListView,
    TenantManagedTenantsTabView,
    tenant_ldap_sync,
)
from .tenantgroup_views import (
    TenantGroupDeleteView,
    TenantGroupDetailView,
    TenantGroupEditView,
    TenantGroupListView,
)
