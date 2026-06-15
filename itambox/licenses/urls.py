from django.urls import path
from . import views

app_name = 'licenses'

urlpatterns = [
    path('', views.LicenseListView.as_view(), name='license_list'),
    path('add/', views.LicenseEditView.as_view(), name='license_create'),
    path('edit/', views.LicenseBulkEditView.as_view(), name='license_bulk_edit'),
    path('delete/', views.LicenseBulkDeleteView.as_view(), name='license_bulk_delete'),
    path('<int:pk>/', views.LicenseDetailView.as_view(), name='license_detail'),
    path('<int:pk>/edit/', views.LicenseEditView.as_view(), name='license_update'),
    path('<int:pk>/delete/', views.LicenseDeleteView.as_view(), name='license_delete'),
    path('<int:pk>/clone/', views.LicenseCloneView.as_view(), name='license_clone'),
    path('<int:pk>/checkout/', views.LicenseCheckoutView.as_view(), name='license_checkout'),
    path('assignments/<int:pk>/checkin/', views.LicenseCheckinView.as_view(), name='license_seat_checkin'),
]
