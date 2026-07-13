from django.urls import path
from . import views

app_name = 'organization'

urlpatterns = [
    # Sites
    path('sites/', views.SiteListView.as_view(), name='site_list'),
    path('sites/add/', views.SiteEditView.as_view(), name='site_create'),
    path('sites/edit/', views.SiteBulkEditView.as_view(), name='site_bulk_edit'),
    path('sites/delete/', views.SiteBulkDeleteView.as_view(), name='site_bulk_delete'),
    path('sites/<int:pk>/', views.SiteDetailView.as_view(), name='site_detail'),
    path('sites/<int:pk>/edit/', views.SiteEditView.as_view(), name='site_update'),
    path('sites/<int:pk>/clone/', views.SiteCloneView.as_view(), name='site_clone'),
    path('sites/<int:pk>/delete/', views.SiteDeleteView.as_view(), name='site_delete'),

    # Regions
    path('regions/', views.RegionListView.as_view(), name='region_list'),
    path('regions/add/', views.RegionEditView.as_view(), name='region_create'),
    path('regions/edit/', views.RegionBulkEditView.as_view(), name='region_bulk_edit'),
    path('regions/delete/', views.RegionBulkDeleteView.as_view(), name='region_bulk_delete'),
    path('regions/<int:pk>/', views.RegionDetailView.as_view(), name='region_detail'),
    path('regions/<int:pk>/edit/', views.RegionEditView.as_view(), name='region_update'),
    path('regions/<int:pk>/clone/', views.RegionCloneView.as_view(), name='region_clone'),
    path('regions/<int:pk>/delete/', views.RegionDeleteView.as_view(), name='region_delete'),

    # Site Groups
    path('site-groups/', views.SiteGroupListView.as_view(), name='sitegroup_list'),
    path('site-groups/add/', views.SiteGroupEditView.as_view(), name='sitegroup_create'),
    path('site-groups/<int:pk>/', views.SiteGroupDetailView.as_view(), name='sitegroup_detail'),
    path('site-groups/<int:pk>/edit/', views.SiteGroupEditView.as_view(), name='sitegroup_update'),
    path('site-groups/<int:pk>/clone/', views.SiteGroupCloneView.as_view(), name='sitegroup_clone'),
    path('site-groups/<int:pk>/delete/', views.SiteGroupDeleteView.as_view(), name='sitegroup_delete'),

    # Locations
    path('locations/', views.LocationListView.as_view(), name='location_list'),
    path('locations/add/', views.LocationEditView.as_view(), name='location_create'),
    path('locations/edit/', views.LocationBulkEditView.as_view(), name='location_bulk_edit'),
    path('locations/delete/', views.LocationBulkDeleteView.as_view(), name='location_bulk_delete'),
    path('locations/<int:pk>/', views.LocationDetailView.as_view(), name='location_detail'),
    path('locations/<int:pk>/edit/', views.LocationEditView.as_view(), name='location_update'),
    path('locations/<int:pk>/clone/', views.LocationCloneView.as_view(), name='location_clone'),
    path('locations/<int:pk>/delete/', views.LocationDeleteView.as_view(), name='location_delete'),

    # Tenant Groups
    path('tenant-groups/', views.TenantGroupListView.as_view(), name='tenantgroup_list'),
    path('tenant-groups/add/', views.TenantGroupEditView.as_view(), name='tenantgroup_create'),
    path('tenant-groups/<int:pk>/', views.TenantGroupDetailView.as_view(), name='tenantgroup_detail'),
    path('tenant-groups/<int:pk>/edit/', views.TenantGroupEditView.as_view(), name='tenantgroup_update'),
    path('tenant-groups/<int:pk>/delete/', views.TenantGroupDeleteView.as_view(), name='tenantgroup_delete'),

    # Tenants
    path('tenants/', views.TenantListView.as_view(), name='tenant_list'),
    path('tenants/add/', views.TenantEditView.as_view(), name='tenant_create'),
    path('tenants/edit/', views.TenantBulkEditView.as_view(), name='tenant_bulk_edit'),
    path('tenants/delete/', views.TenantBulkDeleteView.as_view(), name='tenant_bulk_delete'),
    path('tenants/<int:pk>/', views.TenantDetailView.as_view(), name='tenant_detail'),
    path('tenants/<int:pk>/edit/', views.TenantEditView.as_view(), name='tenant_update'),
    path('tenants/<int:pk>/delete/', views.TenantDeleteView.as_view(), name='tenant_delete'),
    path('tenants/<int:pk>/access/', views.TenantAccessView.as_view(), name='tenant_access'),
    path('tenants/<int:pk>/managed/', views.TenantManagedTenantsTabView.as_view(), name='tenant_managed_tenants_tab'),
    path('tenants/<int:pk>/ldap-sync/', views.tenant_ldap_sync, name='tenant_ldap_sync'),

    # Asset Holders
    path('asset-holders/', views.AssetHolderListView.as_view(), name='assetholder_list'),
    path('asset-holders/add/', views.AssetHolderEditView.as_view(), name='assetholder_create'),
    path('asset-holders/edit/', views.AssetHolderBulkEditView.as_view(), name='assetholder_bulk_edit'),
    path('asset-holders/delete/', views.AssetHolderBulkDeleteView.as_view(), name='assetholder_bulk_delete'),
    path('asset-holders/<int:pk>/', views.AssetHolderDetailView.as_view(), name='assetholder_detail'),
    path('asset-holders/<int:pk>/edit/', views.AssetHolderEditView.as_view(), name='assetholder_update'),
    path('asset-holders/<int:pk>/delete/', views.AssetHolderDeleteView.as_view(), name='assetholder_delete'),

    # Contacts
    path('contacts/', views.ContactListView.as_view(), name='contact_list'),
    path('contacts/add/', views.ContactEditView.as_view(), name='contact_create'),
    path('contacts/edit/', views.ContactBulkEditView.as_view(), name='contact_bulk_edit'),
    path('contacts/delete/', views.ContactBulkDeleteView.as_view(), name='contact_bulk_delete'),
    path('contacts/<int:pk>/', views.ContactDetailView.as_view(), name='contact_detail'),
    path('contacts/<int:pk>/edit/', views.ContactEditView.as_view(), name='contact_update'),
    path('contacts/<int:pk>/clone/', views.ContactCloneView.as_view(), name='contact_clone'),
    path('contacts/<int:pk>/delete/', views.ContactDeleteView.as_view(), name='contact_delete'),

    # Contact Roles
    path('contact-roles/', views.ContactRoleListView.as_view(), name='contactrole_list'),
    path('contact-roles/add/', views.ContactRoleEditView.as_view(), name='contactrole_create'),
    path('contact-roles/edit/', views.ContactRoleBulkEditView.as_view(), name='contactrole_bulk_edit'),
    path('contact-roles/delete/', views.ContactRoleBulkDeleteView.as_view(), name='contactrole_bulk_delete'),
    path('contact-roles/<int:pk>/', views.ContactRoleDetailView.as_view(), name='contactrole_detail'),
    path('contact-roles/<int:pk>/edit/', views.ContactRoleEditView.as_view(), name='contactrole_update'),
    path('contact-roles/<int:pk>/clone/', views.ContactRoleCloneView.as_view(), name='contactrole_clone'),
    path('contact-roles/<int:pk>/delete/', views.ContactRoleDeleteView.as_view(), name='contactrole_delete'),

    # Contact Assignments
    path('contact-assignments/add/', views.ContactAssignmentCreateView.as_view(), name='contactassignment_create'),
    path('contact-assignments/<int:pk>/delete/', views.ContactAssignmentDeleteView.as_view(), name='contactassignment_delete'),

    # Roles (tenant-owned permission sets)
    path('roles/', views.RoleListView.as_view(), name='role_list'),
    path('roles/add/', views.RoleEditView.as_view(), name='role_create'),
    path('roles/delete/', views.RoleBulkDeleteView.as_view(), name='role_bulk_delete'),
    path('roles/<int:pk>/', views.RoleDetailView.as_view(), name='role_detail'),
    path('roles/<int:pk>/edit/', views.RoleEditView.as_view(), name='role_update'),
    path('roles/<int:pk>/clone/', views.RoleCloneView.as_view(), name='role_clone'),
    path('roles/<int:pk>/delete/', views.RoleDeleteView.as_view(), name='role_delete'),
    path('roles/<int:pk>/assign/', views.RoleAssignUsersView.as_view(), name='role_assign_users'),

    # Cost Centers
    path('resource-grants/', views.TenantResourceGrantListView.as_view(), name='tenantresourcegrant_list'),
    path('resource-grants/add/<int:content_type_id>/<int:resource_id>/',
         views.TenantResourceGrantCreateView.as_view(), name='tenantresourcegrant_add'),
    path('resource-grants/<int:pk>/delete/', views.TenantResourceGrantRevokeView.as_view(),
         name='tenantresourcegrant_delete'),

    path('cost-centers/', views.CostCenterListView.as_view(), name='costcenter_list'),
    path('cost-centers/add/', views.CostCenterEditView.as_view(), name='costcenter_create'),
    path('cost-centers/edit/', views.CostCenterBulkEditView.as_view(), name='costcenter_bulk_edit'),
    path('cost-centers/delete/', views.CostCenterBulkDeleteView.as_view(), name='costcenter_bulk_delete'),
    path('cost-centers/<int:pk>/', views.CostCenterDetailView.as_view(), name='costcenter_detail'),
    path('cost-centers/<int:pk>/edit/', views.CostCenterEditView.as_view(), name='costcenter_update'),
    path('cost-centers/<int:pk>/clone/', views.CostCenterCloneView.as_view(), name='costcenter_clone'),
    path('cost-centers/<int:pk>/delete/', views.CostCenterDeleteView.as_view(), name='costcenter_delete'),

    # Quick onboarding
    path('onboard/technician/', views.TechnicianQuickAddView.as_view(), name='technician_quick_add'),


    # Memberships (unified)
    path('memberships/', views.MembershipListView.as_view(), name='membership_list'),
    path('memberships/add/', views.MembershipCreateView.as_view(), name='membership_create'),
    path('memberships/edit/', views.MembershipBulkEditView.as_view(), name='membership_bulk_edit'),
    path('memberships/delete/', views.MembershipBulkDeleteView.as_view(), name='membership_bulk_delete'),
    path('memberships/<int:pk>/', views.MembershipDetailView.as_view(), name='membership_detail'),
    path('memberships/<int:pk>/edit/', views.MembershipEditView.as_view(), name='membership_update'),
    path('memberships/<int:pk>/send-reset/', views.MembershipSendResetView.as_view(), name='membership_send_reset'),
    path('memberships/<int:pk>/delete/', views.MembershipDeleteView.as_view(), name='membership_delete'),
]
