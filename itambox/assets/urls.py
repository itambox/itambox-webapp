from django.urls import path, include
from . import views
from .views import request_views
from inventory import views as component_views
from extras import views as extras_views

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

    # Component Catalog (quantity-based)
    path('components/', component_views.ComponentListView.as_view(), name='component_list'),
    path('components/add/', component_views.ComponentEditView.as_view(), name='component_create'),
    path('components/<int:pk>/', component_views.ComponentDetailView.as_view(), name='component_detail'),
    path('components/<int:pk>/edit/', component_views.ComponentEditView.as_view(), name='component_update'),
    path('components/<int:pk>/delete/', component_views.ComponentDeleteView.as_view(), name='component_delete'),
    path('components/<int:pk>/clone/', component_views.ComponentCloneView.as_view(), name='component_clone'),

    # Component Stock
    path('component-stocks/', component_views.ComponentStockListView.as_view(), name='componentstock_list'),
    path('component-stocks/add/', component_views.ComponentStockEditView.as_view(), name='componentstock_create'),
    path('component-stocks/<int:pk>/edit/', component_views.ComponentStockEditView.as_view(), name='componentstock_update'),
    path('component-stocks/<int:pk>/delete/', component_views.ComponentStockDeleteView.as_view(), name='componentstock_delete'),

    # Component Allocations
    path('component-allocations/', component_views.ComponentAllocationListView.as_view(), name='componentallocation_list'),
    path('component-allocations/add/', component_views.ComponentAllocationEditView.as_view(), name='componentallocation_create'),
    path('component-allocations/<int:pk>/edit/', component_views.ComponentAllocationEditView.as_view(), name='componentallocation_update'),
    path('component-allocations/<int:pk>/delete/', component_views.ComponentAllocationDeleteView.as_view(), name='componentallocation_delete'),

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

    # Custom Fields
    path('custom-fields/', extras_views.CustomFieldListView.as_view(), name='customfield_list'),
    path('custom-fields/add/', extras_views.CustomFieldEditView.as_view(), name='customfield_create'),
    path('custom-fields/<int:pk>/', extras_views.CustomFieldDetailView.as_view(), name='customfield_detail'),
    path('custom-fields/<int:pk>/edit/', extras_views.CustomFieldEditView.as_view(), name='customfield_update'),
    path('custom-fields/<int:pk>/delete/', extras_views.CustomFieldDeleteView.as_view(), name='customfield_delete'),

    # Custom Fieldsets
    path('custom-fieldsets/', extras_views.CustomFieldsetListView.as_view(), name='customfieldset_list'),
    path('custom-fieldsets/add/', extras_views.CustomFieldsetEditView.as_view(), name='customfieldset_create'),
    path('custom-fieldsets/<int:pk>/', extras_views.CustomFieldsetDetailView.as_view(), name='customfieldset_detail'),
    path('custom-fieldsets/<int:pk>/edit/', extras_views.CustomFieldsetEditView.as_view(), name='customfieldset_update'),
    path('custom-fieldsets/<int:pk>/delete/', extras_views.CustomFieldsetDeleteView.as_view(), name='customfieldset_delete'),

    # Requisition System routes
    path('', include('assets.urls_requests')),
]

from .urls_audits import urlpatterns as audit_urls
urlpatterns += audit_urls
