from .site_views import (
    SiteListView, SiteDetailView, SiteEditView, SiteDeleteView, SiteBulkEditView, SiteBulkDeleteView,
    SiteCloneView,
)
from .region_views import (
    RegionListView, RegionDetailView, RegionEditView, RegionDeleteView, RegionBulkEditView, RegionBulkDeleteView,
    RegionCloneView,
)
from .sitegroup_views import (
    SiteGroupListView, SiteGroupDetailView, SiteGroupEditView, SiteGroupDeleteView,
    SiteGroupCloneView,
)
from .location_views import (
    LocationListView, LocationDetailView, LocationEditView, LocationDeleteView,
    LocationImportView, LocationBulkEditView, LocationBulkDeleteView, LocationCloneView,
)
from .tenantgroup_views import (
    TenantGroupListView, TenantGroupDetailView, TenantGroupEditView, TenantGroupDeleteView,
)
from .tenant_views import (
    TenantListView, TenantDetailView, TenantEditView, TenantDeleteView, TenantBulkEditView, TenantBulkDeleteView,
    tenant_ldap_sync,
)
from .assetholder_views import (
    AssetHolderListView, AssetHolderDetailView, AssetHolderEditView, AssetHolderDeleteView,
    AssetHolderImportView, AssetHolderBulkEditView, AssetHolderBulkDeleteView,
)
from .contact_views import (
    ContactListView, ContactDetailView, ContactEditView, ContactDeleteView, ContactBulkEditView, ContactBulkDeleteView,
    ContactCloneView,
)
from .contactrole_views import (
    ContactRoleListView, ContactRoleDetailView, ContactRoleEditView, ContactRoleDeleteView,
    ContactAssignmentCreateView, ContactAssignmentDeleteView,
    ContactRoleBulkEditView, ContactRoleBulkDeleteView, ContactRoleCloneView,
)
from .invitation_views import InviteUserView, AcceptInvitationView
from .tenantrole_views import (
    TenantRoleListView, TenantRoleDetailView, TenantRoleEditView, TenantRoleDeleteView,
    TenantRoleCloneView,
)
from .tenantmembership_views import (
    TenantMembershipListView, TenantMembershipCreateView, TenantMembershipDeleteView,
)

