"""
URL configuration for core project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from assets import views as asset_views # Import the assets views
from assetbox.views.generic import (
    ObjectBulkDeleteView, ObjectBulkEditView, table_config,
)
from assetbox.views.features import (
    ObjectChangeListView, ObjectChangeView, ObjectExportView,
    ExportTemplateListView, ExportTemplateEditView, ExportTemplateDetailView,
    ExportTemplateDeleteView, WebhookEndpointListView, WebhookEndpointEditView,
    WebhookEndpointDetailView, WebhookEndpointDeleteView, EventRuleListView,
    EventRuleEditView, EventRuleDetailView, EventRuleDeleteView,
    LabelTemplateListView, LabelTemplateEditView, LabelTemplateDetailView,
    LabelTemplateDeleteView, ImageAttachmentUploadView, ImageAttachmentDeleteView,
    FileAttachmentUploadView, FileAttachmentDeleteView, JournalEntryCreateView,
    LabelSelectView, LabelPrintView,
)
from assetbox.views.utility import SearchView, health # Import core views, aliased to avoid clash
from django.conf import settings # Import settings
from django.conf.urls.static import static # Import static
# from django.contrib.auth import views as auth_views # Already imported

# Main URL Patterns
urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('django.contrib.auth.urls')),
    path('', asset_views.DashboardView.as_view(), name='dashboard'), # Root path for dashboard

    # Search Path
    path('search/', SearchView.as_view(), name='search'),

    # UI Paths
    path('assets/', include('assets.urls', namespace='assets')),
    path('inventory/', include('inventory.urls', namespace='inventory')),
    path('compliance/', include('compliance.urls', namespace='compliance')),
    path('organization/', include('organization.urls', namespace='organization')),
    path('extras/', include('extras.urls')),
    path('software/', include('software.urls', namespace='software')),
    path('licenses/', include('licenses.urls', namespace='licenses')),
    path('subscriptions/', include('subscriptions.urls', namespace='subscriptions')),
    path('tables/config/<str:model_name>/', table_config, name='table_config'),
    path('user/', include('users.urls')), # Include the users app URLs

    # API Paths (prefixed with /api/) - Point directly to the main api.urls
    path('api/', include('assetbox.api.urls', namespace='api')), # Added namespace='api'

    # Path for core app non-API views if any (e.g., User Preferences UI view)
    # path('core/', include('core.urls')), # Example if core had UI views

    # Changelog
    path('changelog/', ObjectChangeListView.as_view(), name='objectchange_list'),
    path('changelog/<int:pk>/', ObjectChangeView.as_view(), name='objectchange'),

    # Bulk Actions
    path('bulk-delete/', ObjectBulkDeleteView.as_view(), name='bulk_delete'),
    path('bulk-edit/', ObjectBulkEditView.as_view(), name='bulk_edit'),

    # Export Templates
    path('export-templates/', ExportTemplateListView.as_view(), name='export_template_list'),
    path('export-templates/add/', ExportTemplateEditView.as_view(), name='export_template_add'),
    path('export-templates/<int:pk>/', ExportTemplateDetailView.as_view(), name='export_template_detail'),
    path('export-templates/<int:pk>/edit/', ExportTemplateEditView.as_view(), name='export_template_edit'),
    path('export-templates/<int:pk>/delete/', ExportTemplateDeleteView.as_view(), name='export_template_delete'),

    # Webhook Endpoints
    path('webhooks/', WebhookEndpointListView.as_view(), name='webhookendpoint_list'),
    path('webhooks/add/', WebhookEndpointEditView.as_view(), name='webhookendpoint_add'),
    path('webhooks/<int:pk>/', WebhookEndpointDetailView.as_view(), name='webhookendpoint_detail'),
    path('webhooks/<int:pk>/edit/', WebhookEndpointEditView.as_view(), name='webhookendpoint_edit'),
    path('webhooks/<int:pk>/delete/', WebhookEndpointDeleteView.as_view(), name='webhookendpoint_delete'),

    # Event Rules
    path('event-rules/', EventRuleListView.as_view(), name='eventrule_list'),
    path('event-rules/add/', EventRuleEditView.as_view(), name='eventrule_add'),
    path('event-rules/<int:pk>/', EventRuleDetailView.as_view(), name='eventrule_detail'),
    path('event-rules/<int:pk>/edit/', EventRuleEditView.as_view(), name='eventrule_edit'),
    path('event-rules/<int:pk>/delete/', EventRuleDeleteView.as_view(), name='eventrule_delete'),

    # Label Templates
    path('label-templates/', LabelTemplateListView.as_view(), name='labeltemplate_list'),
    path('label-templates/add/', LabelTemplateEditView.as_view(), name='labeltemplate_add'),
    path('label-templates/<int:pk>/', LabelTemplateDetailView.as_view(), name='labeltemplate_detail'),
    path('label-templates/<int:pk>/edit/', LabelTemplateEditView.as_view(), name='labeltemplate_edit'),
    path('label-templates/<int:pk>/delete/', LabelTemplateDeleteView.as_view(), name='labeltemplate_delete'),

    # Export View
    path('export/<str:app_label>/<str:model_name>/<int:template_id>/', ObjectExportView.as_view(), name='object_export'),

    # i18n
    path('i18n/', include('django.conf.urls.i18n')),

    # Attachments
    path('attachments/image/upload/<str:app_label>/<str:model_name>/<int:object_id>/', ImageAttachmentUploadView.as_view(), name='image_attachment_upload'),
    path('attachments/image/delete/<int:pk>/', ImageAttachmentDeleteView.as_view(), name='image_attachment_delete'),
    path('attachments/file/upload/<str:app_label>/<str:model_name>/<int:object_id>/', FileAttachmentUploadView.as_view(), name='file_attachment_upload'),
    path('attachments/file/delete/<int:pk>/', FileAttachmentDeleteView.as_view(), name='file_attachment_delete'),

    # Journal Entries
    path('journal/add/<str:app_label>/<str:model_name>/<int:object_id>/', JournalEntryCreateView.as_view(), name='journal_entry_add'),

    # Label Printing
    path('labels/select/<str:app_label>/<str:model_name>/<int:object_id>/', LabelSelectView.as_view(), name='label_select'),
    path('labels/print/<int:template_id>/<int:object_id>/', LabelPrintView.as_view(), name='label_print'),

    # Health Check
    path('health/', health, name='health'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
