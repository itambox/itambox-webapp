from django.urls import path
from . import views

app_name = 'assets' # Namespace for URLs

urlpatterns = [
    # Dashboard path removed, handled in core.urls
    
    # Asset specific URLs (no 'assets/' prefix needed here anymore)
    path('', views.asset_list, name='asset_list'),
    path('create/', views.asset_create, name='asset_create'),
    path('<int:pk>/', views.asset_detail, name='asset_detail'),
    path('<int:pk>/edit/', views.asset_update, name='asset_update'),
    path('<int:pk>/delete/', views.asset_delete, name='asset_delete'),
    # path('<int:pk>/checkout/', views.asset_checkout, name='asset_checkout'), # Removed checkout URL
    path('<int:pk>/checkin/', views.asset_checkin, name='asset_checkin'),
    path('<int:pk>/checkout_modal/', views.asset_checkout_modal, name='asset_checkout_modal'),

    # Organization URLs were moved to organization.urls

    # Asset Roles (AssetRole) URLs
    path('roles/', views.assetrole_list, name='assetrole_list'),
    path('roles/create/', views.assetrole_create, name='assetrole_create'),
    path('roles/<int:pk>/', views.assetrole_detail, name='assetrole_detail'),
    path('roles/<int:pk>/edit/', views.assetrole_update, name='assetrole_update'),
    path('roles/<int:pk>/delete/', views.assetrole_delete, name='assetrole_delete'),

    # Manufacturer URLs
    path('manufacturers/', views.manufacturer_list, name='manufacturer_list'),
    path('manufacturers/create/', views.manufacturer_create, name='manufacturer_create'),
    path('manufacturers/<int:pk>/', views.manufacturer_detail, name='manufacturer_detail'),
    path('manufacturers/<int:pk>/edit/', views.manufacturer_update, name='manufacturer_update'),
    path('manufacturers/<int:pk>/delete/', views.manufacturer_delete, name='manufacturer_delete'),

    # Asset Types (using Class-Based Views)
    path('types/', views.AssetTypeListView.as_view(), name='assettype_list'),
    path('types/create/', views.AssetTypeCreateView.as_view(), name='assettype_create'),
    path('types/<slug:slug>/', views.AssetTypeDetailView.as_view(), name='assettype_detail'),
    path('types/<slug:slug>/edit/', views.AssetTypeUpdateView.as_view(), name='assettype_update'),
    path('types/<slug:slug>/delete/', views.AssetTypeDeleteView.as_view(), name='assettype_delete'),

    # Asset Roles (using function-based views, standardized names)
    path('roles/', views.assetrole_list, name='assetrole_list'),
    path('roles/create/', views.assetrole_create, name='assetrole_create'),

    # TODO: Add URLs for Customization (Tags - moved to extras?)
] 