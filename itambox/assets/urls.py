from django.urls import path, include
from django.views.generic import RedirectView
from . import views
from .views import request_views

app_name = 'assets'

urlpatterns = [
    # Dashboard path removed, handled in core.urls
    path('assets/bulk-assign/', views.bulk_assign_assets, name='asset_bulk_assign'),
    path('assets/bulk-print-labels/', views.bulk_print_labels, name='asset_bulk_print_labels'),

    # Asset bulk operations
    path('assets/edit/', views.AssetBulkEditView.as_view(), name='asset_bulk_edit'),
    path('assets/delete/', views.AssetBulkDeleteView.as_view(), name='asset_bulk_delete'),

    # Asset specific URLs
    path('assets/', views.AssetListView.as_view(), name='asset_list'),
    path('assets/add/', views.AssetEditView.as_view(), name='asset_create'),
    path('assets/<int:pk>/', views.AssetDetailView.as_view(), name='asset_detail'),
    path('assets/<int:pk>/edit/', views.AssetEditView.as_view(), name='asset_update'),
    path('assets/<int:pk>/delete/', views.AssetDeleteView.as_view(), name='asset_delete'),
    path('assets/<int:pk>/clone/', views.AssetCloneView.as_view(), name='asset_clone'),
    path('assets/<int:pk>/checkout/', views.AssetCheckoutView.as_view(), name='asset_checkout_modal'),
    path('assets/<int:pk>/checkin/', views.AssetCheckinView.as_view(), name='asset_checkin'),

    # Asset Roles (AssetRole) URLs
    path('roles/', views.AssetRoleListView.as_view(), name='assetrole_list'),
    path('roles/add/', views.AssetRoleEditView.as_view(), name='assetrole_create'),
    path('roles/<int:pk>/', views.AssetRoleDetailView.as_view(), name='assetrole_detail'),
    path('roles/<int:pk>/edit/', views.AssetRoleEditView.as_view(), name='assetrole_update'),
    path('roles/<int:pk>/clone/', views.AssetRoleCloneView.as_view(), name='assetrole_clone'),
    path('roles/<int:pk>/delete/', views.AssetRoleDeleteView.as_view(), name='assetrole_delete'),

    # Status Labels (StatusLabel) URLs
    path('status-labels/', views.StatusLabelListView.as_view(), name='statuslabel_list'),
    path('status-labels/add/', views.StatusLabelEditView.as_view(), name='statuslabel_create'),
    path('status-labels/<int:pk>/', views.StatusLabelDetailView.as_view(), name='statuslabel_detail'),
    path('status-labels/<int:pk>/edit/', views.StatusLabelEditView.as_view(), name='statuslabel_update'),
    path('status-labels/<int:pk>/clone/', views.StatusLabelCloneView.as_view(), name='statuslabel_clone'),
    path('status-labels/<int:pk>/delete/', views.StatusLabelDeleteView.as_view(), name='statuslabel_delete'),

    # Manufacturer URLs
    path('manufacturers/', views.ManufacturerListView.as_view(), name='manufacturer_list'),
    path('manufacturers/add/', views.ManufacturerEditView.as_view(), name='manufacturer_create'),
    path('manufacturers/<int:pk>/', views.ManufacturerDetailView.as_view(), name='manufacturer_detail'),
    path('manufacturers/<int:pk>/edit/', views.ManufacturerEditView.as_view(), name='manufacturer_update'),
    path('manufacturers/<int:pk>/clone/', views.ManufacturerCloneView.as_view(), name='manufacturer_clone'),
    path('manufacturers/<int:pk>/delete/', views.ManufacturerDeleteView.as_view(), name='manufacturer_delete'),

    # Asset Types
    path('types/', views.AssetTypeListView.as_view(), name='assettype_list'),
    path('types/add/', views.AssetTypeEditView.as_view(), name='assettype_create'),
    path('types/<int:pk>/clone/', views.AssetTypeCloneView.as_view(), name='assettype_clone'),
    path('types/import/', views.AssetTypeImportView.as_view(), name='assettype_import'),
    path('types/<int:pk>/', views.AssetTypeDetailView.as_view(), name='assettype_detail'),
    path('types/<int:pk>/edit/', views.AssetTypeEditView.as_view(), name='assettype_update'),
    path('types/<int:pk>/delete/', views.AssetTypeDeleteView.as_view(), name='assettype_delete'),

    # Components live in the inventory app/namespace (inventory:component_*).
    # The duplicate routes that used to live here are gone — one canonical URL
    # per object, the NetBox way.

    # Phase 4 Audits & Barcoding
    path('<int:pk>/audit/', views.AssetAuditView.as_view(), name='asset_audit'),
    path('<int:pk>/print/', views.asset_label_print, name='asset_label_print'),
    path('<int:pk>/print/<int:template_id>/', views.asset_label_print, name='asset_label_print_template'),

    # Depreciation
    path('depreciations/', views.DepreciationListView.as_view(), name='depreciation_list'),
    path('depreciations/add/', views.DepreciationEditView.as_view(), name='depreciation_create'),
    path('depreciations/<int:pk>/', views.DepreciationDetailView.as_view(), name='depreciation_detail'),
    path('depreciations/<int:pk>/edit/', views.DepreciationEditView.as_view(), name='depreciation_update'),
    path('depreciations/<int:pk>/clone/', views.DepreciationCloneView.as_view(), name='depreciation_clone'),
    path('depreciations/<int:pk>/delete/', views.DepreciationDeleteView.as_view(), name='depreciation_delete'),

    # Suppliers
    path('suppliers/', views.SupplierListView.as_view(), name='supplier_list'),
    path('suppliers/add/', views.SupplierEditView.as_view(), name='supplier_create'),
    path('suppliers/<int:pk>/clone/', views.SupplierCloneView.as_view(), name='supplier_clone'),
    path('suppliers/<int:pk>/', views.SupplierDetailView.as_view(), name='supplier_detail'),
    path('suppliers/<int:pk>/edit/', views.SupplierEditView.as_view(), name='supplier_update'),
    path('suppliers/<int:pk>/delete/', views.SupplierDeleteView.as_view(), name='supplier_delete'),

    # Categories
    path('categories/', views.CategoryListView.as_view(), name='category_list'),
    path('categories/add/', views.CategoryEditView.as_view(), name='category_create'),
    path('categories/<int:pk>/clone/', views.CategoryCloneView.as_view(), name='category_clone'),
    path('categories/<int:pk>/', views.CategoryDetailView.as_view(), name='category_detail'),
    path('categories/<int:pk>/edit/', views.CategoryEditView.as_view(), name='category_update'),
    path('categories/<int:pk>/delete/', views.CategoryDeleteView.as_view(), name='category_delete'),

    # Asset Requests
    path('requests/', views.AssetRequestListView.as_view(), name='assetrequest_list'),

    path('requests/add/', views.AssetRequestCreateView.as_view(), name='assetrequest_create'),
    path('requests/<int:pk>/', request_views.RequestDetailView.as_view(), name='assetrequest_detail'),
    path('requests/<int:pk>/edit/', views.AssetRequestEditView.as_view(), name='assetrequest_update'),
    path('requests/<int:pk>/delete/', views.AssetRequestDeleteView.as_view(), name='assetrequest_delete'),

    # Import URLs
    path('assets/import/', views.AssetImportView.as_view(), name='asset_import'),
    path('manufacturers/import/', views.ManufacturerImportView.as_view(), name='manufacturer_import'),

    path('asset-tag-sequences/', views.AssetTagSequenceListView.as_view(), name='assettagsequence_list'),
    path('asset-tag-sequences/add/', views.AssetTagSequenceEditView.as_view(), name='assettagsequence_create'),
    path('asset-tag-sequences/<int:pk>/', views.AssetTagSequenceDetailView.as_view(), name='assettagsequence_detail'),
    path('asset-tag-sequences/<int:pk>/edit/', views.AssetTagSequenceEditView.as_view(), name='assettagsequence_update'),
    path('asset-tag-sequences/<int:pk>/delete/', views.AssetTagSequenceDeleteView.as_view(), name='assettagsequence_delete'),

    # Custom fields moved to the extras app (extras:customfield_*).
    # Redirects keep old bookmarks alive.
    path('custom-fields/', RedirectView.as_view(pattern_name='extras:customfield_list', permanent=True)),
    path('custom-fields/<int:pk>/', RedirectView.as_view(pattern_name='extras:customfield_detail', permanent=True)),
    path('custom-fieldsets/', RedirectView.as_view(pattern_name='extras:customfieldset_list', permanent=True)),
    path('custom-fieldsets/<int:pk>/', RedirectView.as_view(pattern_name='extras:customfieldset_detail', permanent=True)),

    # Requisition System routes
    path('', include('assets.urls_requests')),
]

from .urls_audits import urlpatterns as audit_urls
urlpatterns += audit_urls

urlpatterns += [
    path('maintenances/', views.AssetMaintenanceListView.as_view(), name='assetmaintenance_list'),
    path('maintenances/add/', views.AssetMaintenanceEditView.as_view(), name='assetmaintenance_create'),
    path('maintenances/<int:pk>/', views.AssetMaintenanceDetailView.as_view(), name='assetmaintenance_detail'),
    path('maintenances/<int:pk>/edit/', views.AssetMaintenanceEditView.as_view(), name='assetmaintenance_update'),
    path('maintenances/<int:pk>/clone/', views.AssetMaintenanceCloneView.as_view(), name='assetmaintenance_clone'),
    path('maintenances/<int:pk>/delete/', views.AssetMaintenanceDeleteView.as_view(), name='assetmaintenance_delete'),
]
