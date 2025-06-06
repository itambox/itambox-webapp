from django.urls import path
from . import views

app_name = 'assets'

urlpatterns = [
    path('', views.asset_list, name='asset_list'),
    path('create/', views.asset_create, name='asset_create'),
    path('<int:pk>/', views.asset_detail, name='asset_detail'),
    path('<int:pk>/update/', views.asset_update, name='asset_update'),
    path('<int:pk>/delete/', views.asset_delete, name='asset_delete'),
    path('<int:pk>/checkout/', views.asset_checkout_modal, name='asset_checkout_modal'), # Modal GET/POST
    # path('<int:pk>/checkout/submit/', views.asset_checkout, name='asset_checkout'), # Commented out
    path('<int:pk>/checkin/', views.asset_checkin, name='asset_checkin'), # Checkin POST

    # Asset Roles (renamed from Categories)
    path('assetroles/', views.assetrole_list, name='assetrole_list'),
    path('assetroles/create/', views.assetrole_create, name='assetrole_create'),
    path('assetroles/<int:pk>/', views.assetrole_detail, name='assetrole_detail'),
    path('assetroles/<int:pk>/update/', views.assetrole_update, name='assetrole_update'),
    path('assetroles/<int:pk>/delete/', views.assetrole_delete, name='assetrole_delete'),

    # Manufacturers
    path('manufacturers/', views.manufacturer_list, name='manufacturer_list'),
    path('manufacturers/create/', views.manufacturer_create, name='manufacturer_create'),
    path('manufacturers/<int:pk>/', views.manufacturer_detail, name='manufacturer_detail'),
    path('manufacturers/<int:pk>/update/', views.manufacturer_update, name='manufacturer_update'),
    path('manufacturers/<int:pk>/delete/', views.manufacturer_delete, name='manufacturer_delete'),

    # Dashboard (Example - might live in core app later)
    path('dashboard/', views.dashboard, name='dashboard'),
] 