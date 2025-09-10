from django.urls import path
from . import views

app_name = 'compliance'

urlpatterns = [
    # Asset Maintenances
    path('maintenances/', views.AssetMaintenanceListView.as_view(), name='assetmaintenance_list'),
    path('maintenances/add/', views.AssetMaintenanceEditView.as_view(), name='assetmaintenance_create'),
    path('maintenances/<int:pk>/', views.AssetMaintenanceDetailView.as_view(), name='assetmaintenance_detail'),
    path('maintenances/<int:pk>/edit/', views.AssetMaintenanceEditView.as_view(), name='assetmaintenance_update'),
    path('maintenances/<int:pk>/delete/', views.AssetMaintenanceDeleteView.as_view(), name='assetmaintenance_delete'),

    # Custody
    path('custody/sign/<str:token>/', views.custody_eula_sign, name='custody_eula_sign'),
]
