from django.urls import path
from . import views

app_name = 'assets' # Namespace for URLs

urlpatterns = [
    # Dashboard path removed, handled in core.urls
    
    # Asset specific URLs (no 'assets/' prefix needed here anymore)
    path('', views.AssetListView.as_view(), name='asset_list'),
    path('add/', views.asset_create, name='asset_create'),
    path('<int:pk>/', views.asset_detail, name='asset_detail'),
    path('<int:pk>/edit/', views.asset_update, name='asset_update'),
    path('<int:pk>/delete/', views.asset_delete, name='asset_delete'),
    path('<int:pk>/checkout/', views.asset_checkout_modal, name='asset_checkout_modal'),
    path('<int:pk>/checkin/', views.asset_checkin, name='asset_checkin'),

    # Organization URLs were moved to organization.urls

    # Asset Roles (AssetRole) URLs
    path('roles/', views.AssetRoleListView.as_view(), name='assetrole_list'),
    path('roles/add/', views.assetrole_create, name='assetrole_create'),
    path('roles/<int:pk>/', views.assetrole_detail, name='assetrole_detail'),
    path('roles/<int:pk>/edit/', views.assetrole_update, name='assetrole_update'),
    path('roles/<int:pk>/delete/', views.assetrole_delete, name='assetrole_delete'),

    # Manufacturer URLs
    path('manufacturers/', views.ManufacturerListView.as_view(), name='manufacturer_list'),
    path('manufacturers/add/', views.manufacturer_create, name='manufacturer_create'),
    path('manufacturers/<int:pk>/', views.manufacturer_detail, name='manufacturer_detail'),
    path('manufacturers/<int:pk>/edit/', views.manufacturer_update, name='manufacturer_update'),
    path('manufacturers/<int:pk>/delete/', views.manufacturer_delete, name='manufacturer_delete'),

    # Asset Types (using CBV)
    path('types/', views.AssetTypeListView.as_view(), name='assettype_list'),
    path('types/add/', views.AssetTypeCreateView.as_view(), name='assettype_create'),
    path('types/<slug:slug>/', views.AssetTypeDetailView.as_view(), name='assettype_detail'),
    path('types/<slug:slug>/edit/', views.AssetTypeUpdateView.as_view(), name='assettype_edit'),
    path('types/<slug:slug>/delete/', views.AssetTypeDeleteView.as_view(), name='assettype_delete'),

    # TODO: Add URLs for Customization (Tags - moved to extras?)
] 