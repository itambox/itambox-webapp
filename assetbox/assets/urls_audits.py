from django.urls import path
from . import views

urlpatterns = [
    path('audit-sessions/', views.AuditSessionListView.as_view(), name='auditsession_list'),
    path('audit-sessions/add/', views.AuditSessionCreateView.as_view(), name='auditsession_create'),
    path('audit-sessions/<int:pk>/', views.AuditSessionDetailView.as_view(), name='auditsession_detail'),
    path('audit-sessions/<int:pk>/scan/', views.AssetAuditScanView.as_view(), name='auditsession_scan'),
    path('audit-sessions/<int:pk>/close/', views.AuditSessionCloseView.as_view(), name='auditsession_close'),
    path('audit-sessions/<int:pk>/rehome/', views.AuditSessionRehomeView.as_view(), name='auditsession_rehome'),
    path('audit-sessions/<int:pk>/delete/', views.AuditSessionDeleteView.as_view(), name='auditsession_delete'),
]
