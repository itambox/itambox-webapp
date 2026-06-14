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
    path('audit-sessions/<int:pk>/report.csv', views_audit.AuditSessionReportCsvView.as_view(), name='auditsession_report_csv'),
    path('audit-sessions/<int:pk>/flag-missing/', views_audit.AuditSessionFlagMissingView.as_view(), name='auditsession_flag_missing'),
    path('audit-sessions/<int:pk>/start/', views_audit.AuditSessionStartView.as_view(), name='auditsession_start'),
    path('audit-sessions/<int:pk>/delete/', views_audit.AuditSessionDeleteView.as_view(), name='auditsession_delete'),

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
