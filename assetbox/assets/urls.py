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

    # Asset Roles (Category) URLs
    path('asset-roles/', views.category_list, name='category_list'),
    path('asset-roles/create/', views.category_create, name='category_create'),
    path('asset-roles/<int:pk>/', views.category_detail, name='category_detail'),
    path('asset-roles/<int:pk>/edit/', views.category_update, name='category_update'),
    path('asset-roles/<int:pk>/delete/', views.category_delete, name='category_delete'),

    # Manufacturer URLs
    path('manufacturers/', views.manufacturer_list, name='manufacturer_list'),
    path('manufacturers/create/', views.manufacturer_create, name='manufacturer_create'),
    path('manufacturers/<int:pk>/', views.manufacturer_detail, name='manufacturer_detail'),
    path('manufacturers/<int:pk>/edit/', views.manufacturer_update, name='manufacturer_update'),
    path('manufacturers/<int:pk>/delete/', views.manufacturer_delete, name='manufacturer_delete'),

    # TODO: Add URLs for Customization (Tags - moved to extras?)
] 