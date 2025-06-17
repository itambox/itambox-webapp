from django.urls import path
from . import views

app_name = 'assets' # Namespace for URLs

urlpatterns = [
    # Dashboard path removed, handled in core.urls
    
    # Asset specific URLs (no 'assets/' prefix needed here anymore)
    path('', views.AssetListView.as_view(), name='asset_list'),
    path('add/', views.AssetEditView.as_view(), name='asset_create'),
    path('<int:pk>/', views.AssetDetailView.as_view(), name='asset_detail'),
    path('<int:pk>/edit/', views.AssetEditView.as_view(), name='asset_update'),
    path('<int:pk>/delete/', views.AssetDeleteView.as_view(), name='asset_delete'),
    path('<int:pk>/checkout/', views.asset_checkout_modal, name='asset_checkout_modal'),
    path('<int:pk>/checkin/', views.asset_checkin, name='asset_checkin'),

    # Organization URLs were moved to organization.urls

    # Asset Roles (AssetRole) URLs
    path('roles/', views.AssetRoleListView.as_view(), name='assetrole_list'),
    path('roles/add/', views.AssetRoleEditView.as_view(), name='assetrole_create'),
    path('roles/<int:pk>/', views.AssetRoleDetailView.as_view(), name='assetrole_detail'),
    path('roles/<int:pk>/edit/', views.AssetRoleEditView.as_view(), name='assetrole_update'),
    path('roles/<int:pk>/delete/', views.AssetRoleDeleteView.as_view(), name='assetrole_delete'),

    # Manufacturer URLs
    path('manufacturers/', views.ManufacturerListView.as_view(), name='manufacturer_list'),
    path('manufacturers/add/', views.ManufacturerEditView.as_view(), name='manufacturer_create'),
    path('manufacturers/<int:pk>/', views.ManufacturerDetailView.as_view(), name='manufacturer_detail'),
    path('manufacturers/<int:pk>/edit/', views.ManufacturerEditView.as_view(), name='manufacturer_update'),
    path('manufacturers/<int:pk>/delete/', views.ManufacturerDeleteView.as_view(), name='manufacturer_delete'),

    # Asset Types
    path('types/', views.AssetTypeListView.as_view(), name='assettype_list'),
    path('types/add/', views.AssetTypeEditView.as_view(), name='assettype_create'),
    path('types/<slug:slug>/', views.AssetTypeDetailView.as_view(), name='assettype_detail'),
    path('types/<slug:slug>/edit/', views.AssetTypeEditView.as_view(), name='assettype_update'),
    path('types/<slug:slug>/delete/', views.AssetTypeDeleteView.as_view(), name='assettype_delete'),

    # TODO: Add URLs for Customization (Tags - moved to extras?)
] 