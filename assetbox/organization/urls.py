from django.urls import path
from . import views

app_name = 'organization' # Namespace for this app's URLs

urlpatterns = [
    # Sites (Removed /organization/ prefix as it will be handled in core.urls)
    path('sites/', views.site_list, name='site_list'),
    path('sites/create/', views.site_create, name='site_create'),
    path('sites/<int:pk>/', views.site_detail, name='site_detail'),
    path('sites/<int:pk>/update/', views.site_update, name='site_update'),
    path('sites/<int:pk>/delete/', views.site_delete, name='site_delete'),

    # Regions
    path('regions/', views.region_list, name='region_list'),
    path('regions/create/', views.region_create, name='region_create'),
    path('regions/<int:pk>/', views.region_detail, name='region_detail'),
    path('regions/<int:pk>/update/', views.region_update, name='region_update'),
    path('regions/<int:pk>/delete/', views.region_delete, name='region_delete'),

    # Site Groups
    path('site-groups/', views.sitegroup_list, name='sitegroup_list'),
    path('site-groups/create/', views.sitegroup_create, name='sitegroup_create'),
    path('site-groups/<int:pk>/', views.sitegroup_detail, name='sitegroup_detail'),
    path('site-groups/<int:pk>/update/', views.sitegroup_update, name='sitegroup_update'),
    path('site-groups/<int:pk>/delete/', views.sitegroup_delete, name='sitegroup_delete'),

    # Locations
    path('locations/', views.location_list, name='location_list'),
    path('locations/create/', views.location_create, name='location_create'),
    path('locations/<int:pk>/', views.location_detail, name='location_detail'),
    path('locations/<int:pk>/update/', views.location_update, name='location_update'),
    path('locations/<int:pk>/delete/', views.location_delete, name='location_delete'),

    # Tenant Groups
    path('tenant-groups/', views.tenantgroup_list, name='tenantgroup_list'),
    path('tenant-groups/create/', views.tenantgroup_create, name='tenantgroup_create'),
    path('tenant-groups/<int:pk>/', views.tenantgroup_detail, name='tenantgroup_detail'),
    path('tenant-groups/<int:pk>/update/', views.tenantgroup_update, name='tenantgroup_update'),
    path('tenant-groups/<int:pk>/delete/', views.tenantgroup_delete, name='tenantgroup_delete'),

    # Tenants
    path('tenants/', views.tenant_list, name='tenant_list'),
    path('tenants/create/', views.tenant_create, name='tenant_create'),
    path('tenants/<int:pk>/', views.tenant_detail, name='tenant_detail'),
    path('tenants/<int:pk>/update/', views.tenant_update, name='tenant_update'),
    path('tenants/<int:pk>/delete/', views.tenant_delete, name='tenant_delete'),

    # Asset Holders
    path('asset-holders/', views.assetholder_list, name='assetholder_list'),
    path('asset-holders/create/', views.assetholder_create, name='assetholder_create'),
    path('asset-holders/<int:pk>/update/', views.assetholder_update, name='assetholder_update'),
    path('asset-holders/<int:pk>/delete/', views.assetholder_delete, name='assetholder_delete'),
    path('asset-holders/<int:pk>/', views.assetholder_detail, name='assetholder_detail'),

    # Asset Holder Assignments (List only)
    path('asset-holder-assignments/', views.assetholderassignment_list, name='assetholderassignment_list'),

    # TODO: Add URLs for Tag
] 