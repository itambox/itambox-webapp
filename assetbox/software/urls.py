from django.urls import path
from . import views

app_name = 'software'

urlpatterns = [
    # Software
    path('software/', views.SoftwareListView.as_view(), name='software_list'),
    path('software/add/', views.SoftwareEditView.as_view(), name='software_create'),
    path('software/<int:pk>/', views.SoftwareDetailView.as_view(), name='software_detail'),
    path('software/<int:pk>/edit/', views.SoftwareEditView.as_view(), name='software_edit'),
    path('software/<int:pk>/delete/', views.SoftwareDeleteView.as_view(), name='software_delete'),
    # Add bulk edit/delete later if needed
    # path('software/edit/', views.SoftwareBulkEditView.as_view(), name='software_bulk_edit'),
    # path('software/delete/', views.SoftwareBulkDeleteView.as_view(), name='software_bulk_delete'),
] 