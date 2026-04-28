from django.urls import path
from . import views

urlpatterns = [
    path('', views.ConfigContextListView.as_view(), name='configcontext_list'),
    path('add/', views.ConfigContextCreateView.as_view(), name='configcontext_create'),
    path('<int:pk>/edit/', views.ConfigContextEditView.as_view(), name='configcontext_edit'),
    path('<int:pk>/delete/', views.ConfigContextDeleteView.as_view(), name='configcontext_delete'),
]
