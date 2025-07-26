from django.urls import path
from . import views

app_name = 'licenses'

urlpatterns = [
    # License Entitlements
    path('', views.LicenseListView.as_view(), name='license_list'),
    path('add/', views.LicenseEditView.as_view(), name='license_create'),
    path('<int:pk>/', views.LicenseDetailView.as_view(), name='license_detail'),
    path('<int:pk>/edit/', views.LicenseEditView.as_view(), name='license_update'),
    path('<int:pk>/delete/', views.LicenseDeleteView.as_view(), name='license_delete'),
    path('<int:pk>/clone/', views.LicenseCloneView.as_view(), name='license_clone'),

    # Import
    path('import/', views.LicenseImportView.as_view(), name='license_import'),
]
