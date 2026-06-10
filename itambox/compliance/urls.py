from django.urls import path
from . import views
from . import views_audit

app_name = 'compliance'

urlpatterns = [
    # Audit Sessions
    path('audit-sessions/', views_audit.AuditSessionListView.as_view(), name='auditsession_list'),
    path('audit-sessions/add/', views_audit.AuditSessionCreateView.as_view(), name='auditsession_create'),
    path('audit-sessions/<int:pk>/', views_audit.AuditSessionDetailView.as_view(), name='auditsession_detail'),
    path('audit-sessions/<int:pk>/scan/', views_audit.AssetAuditScanView.as_view(), name='auditsession_scan'),
    path('audit-sessions/<int:pk>/close/', views_audit.AuditSessionCloseView.as_view(), name='auditsession_close'),
    path('audit-sessions/<int:pk>/rehome/', views_audit.AuditSessionRehomeView.as_view(), name='auditsession_rehome'),
    path('audit-sessions/<int:pk>/delete/', views_audit.AuditSessionDeleteView.as_view(), name='auditsession_delete'),

    # Asset Maintenances
    path('maintenances/', views.AssetMaintenanceListView.as_view(), name='assetmaintenance_list'),
    path('maintenances/add/', views.AssetMaintenanceEditView.as_view(), name='assetmaintenance_create'),
    path('maintenances/<int:pk>/', views.AssetMaintenanceDetailView.as_view(), name='assetmaintenance_detail'),
    path('maintenances/<int:pk>/edit/', views.AssetMaintenanceEditView.as_view(), name='assetmaintenance_update'),
    path('maintenances/<int:pk>/clone/', views.AssetMaintenanceCloneView.as_view(), name='assetmaintenance_clone'),
    path('maintenances/<int:pk>/delete/', views.AssetMaintenanceDeleteView.as_view(), name='assetmaintenance_delete'),

    # Custody Templates
    path('custody-templates/', views.CustodyTemplateListView.as_view(), name='custodytemplate_list'),
    path('custody-templates/add/', views.CustodyTemplateEditView.as_view(), name='custodytemplate_create'),
    path('custody-templates/<int:pk>/', views.CustodyTemplateDetailView.as_view(), name='custodytemplate_detail'),
    path('custody-templates/<int:pk>/edit/', views.CustodyTemplateEditView.as_view(), name='custodytemplate_update'),
    path('custody-templates/<int:pk>/clone/', views.CustodyTemplateCloneView.as_view(), name='custodytemplate_clone'),
    path('custody-templates/<int:pk>/delete/', views.CustodyTemplateDeleteView.as_view(), name='custodytemplate_delete'),

    # Custody
    path('custody/sign/<str:token>/', views.custody_eula_sign, name='custody_eula_sign'),
    path('custody-templates/<int:pk>/preview/', views.custody_template_preview, name='custodytemplate_preview'),
]
