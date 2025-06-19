from django.urls import path
from . import views

app_name = 'organization' # Namespace for this app's URLs

urlpatterns = [
    # Sites
    path('sites/', views.SiteListView.as_view(), name='site_list'),
    path('sites/add/', views.SiteEditView.as_view(), name='site_create'),
    path('sites/<int:pk>/', views.SiteDetailView.as_view(), name='site_detail'),
    path('sites/<int:pk>/edit/', views.SiteEditView.as_view(), name='site_update'),
    path('sites/<int:pk>/delete/', views.SiteDeleteView.as_view(), name='site_delete'),

    # Regions
    path('regions/', views.RegionListView.as_view(), name='region_list'),
    path('regions/add/', views.RegionEditView.as_view(), name='region_create'),
    path('regions/<int:pk>/', views.RegionDetailView.as_view(), name='region_detail'),
    path('regions/<int:pk>/edit/', views.RegionEditView.as_view(), name='region_update'),
    path('regions/<int:pk>/delete/', views.RegionDeleteView.as_view(), name='region_delete'),

    # Site Groups
    path('site-groups/', views.SiteGroupListView.as_view(), name='sitegroup_list'),
    path('site-groups/add/', views.SiteGroupEditView.as_view(), name='sitegroup_create'),
    path('site-groups/<int:pk>/', views.SiteGroupDetailView.as_view(), name='sitegroup_detail'),
    path('site-groups/<int:pk>/edit/', views.SiteGroupEditView.as_view(), name='sitegroup_update'),
    path('site-groups/<int:pk>/delete/', views.SiteGroupDeleteView.as_view(), name='sitegroup_delete'),

    # Locations
    path('locations/', views.LocationListView.as_view(), name='location_list'),
    path('locations/add/', views.LocationEditView.as_view(), name='location_create'),
    path('locations/<int:pk>/', views.LocationDetailView.as_view(), name='location_detail'),
    path('locations/<int:pk>/edit/', views.LocationEditView.as_view(), name='location_update'),
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
    path('tenants/<int:pk>/', views.TenantDetailView.as_view(), name='tenant_detail'),
    path('tenants/<int:pk>/edit/', views.TenantEditView.as_view(), name='tenant_update'),
    path('tenants/<int:pk>/delete/', views.TenantDeleteView.as_view(), name='tenant_delete'),

    # Asset Holders
    path('asset-holders/', views.AssetHolderListView.as_view(), name='assetholder_list'),
    path('asset-holders/add/', views.AssetHolderEditView.as_view(), name='assetholder_create'),
    path('asset-holders/<int:pk>/', views.AssetHolderDetailView.as_view(), name='assetholder_detail'),
    path('asset-holders/<int:pk>/edit/', views.AssetHolderEditView.as_view(), name='assetholder_update'),
    path('asset-holders/<int:pk>/delete/', views.AssetHolderDeleteView.as_view(), name='assetholder_delete'),

    # Asset Holder Assignments (List only - Refactored to CBV)
    path('asset-holder-assignments/', views.AssetHolderAssignmentListView.as_view(), name='assetholderassignment_list'),

    # TODO: Add URLs for Tag
] 