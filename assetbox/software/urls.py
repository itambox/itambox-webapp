from django.urls import path
from . import views

app_name = 'software'

urlpatterns = [
    # Software
    path('software/', views.SoftwareListView.as_view(), name='software_list'),
    path('software/add/', views.SoftwareEditView.as_view(), name='software_create'),
    path('software/<int:pk>/', views.SoftwareDetailView.as_view(), name='software_detail'),
    path('software/<int:pk>/edit/', views.SoftwareEditView.as_view(), name='software_update'),
    path('software/<int:pk>/delete/', views.SoftwareDeleteView.as_view(), name='software_delete'),
] 