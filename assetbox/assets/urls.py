from django.urls import path
from . import views

app_name = 'assets' # Namespace for URLs

urlpatterns = [
    # Dashboard path removed, handled in core.urls
    path('assets/bulk-assign/', views.bulk_assign_assets, name='asset_bulk_assign'),

    # Asset specific URLs (no 'assets/' prefix needed here anymore)
    path('assets/', views.AssetListView.as_view(), name='asset_list'),
    path('assets/add/', views.AssetEditView.as_view(), name='asset_create'),
    path('assets/<int:pk>/', views.AssetDetailView.as_view(), name='asset_detail'),
    path('assets/<int:pk>/edit/', views.AssetEditView.as_view(), name='asset_update'),
    path('assets/<int:pk>/delete/', views.AssetDeleteView.as_view(), name='asset_delete'),
    path('assets/<int:pk>/checkout/', views.asset_checkout_modal, name='asset_checkout_modal'),
    path('assets/<int:pk>/checkin/', views.asset_checkin, name='asset_checkin'),

    # Organization URLs were moved to organization.urls

    # Asset Roles (AssetRole) URLs
    path('roles/', views.AssetRoleListView.as_view(), name='assetrole_list'),
    path('roles/add/', views.AssetRoleEditView.as_view(), name='assetrole_create'),
    path('roles/<int:pk>/', views.AssetRoleDetailView.as_view(), name='assetrole_detail'),
    path('roles/<int:pk>/edit/', views.AssetRoleEditView.as_view(), name='assetrole_update'),
    path('roles/<int:pk>/delete/', views.AssetRoleDeleteView.as_view(), name='assetrole_delete'),

    # Status Labels (StatusLabel) URLs
    path('status-labels/', views.StatusLabelListView.as_view(), name='statuslabel_list'),
    path('status-labels/add/', views.StatusLabelEditView.as_view(), name='statuslabel_create'),
    path('status-labels/<int:pk>/', views.StatusLabelDetailView.as_view(), name='statuslabel_detail'),
    path('status-labels/<int:pk>/edit/', views.StatusLabelEditView.as_view(), name='statuslabel_update'),
    path('status-labels/<int:pk>/delete/', views.StatusLabelDeleteView.as_view(), name='statuslabel_delete'),

    # Manufacturer URLs
    path('manufacturers/', views.ManufacturerListView.as_view(), name='manufacturer_list'),
    path('manufacturers/add/', views.ManufacturerEditView.as_view(), name='manufacturer_create'),
    path('manufacturers/<int:pk>/', views.ManufacturerDetailView.as_view(), name='manufacturer_detail'),
    path('manufacturers/<int:pk>/edit/', views.ManufacturerEditView.as_view(), name='manufacturer_update'),
    path('manufacturers/<int:pk>/delete/', views.ManufacturerDeleteView.as_view(), name='manufacturer_delete'),

    # Asset Types
    path('types/', views.AssetTypeListView.as_view(), name='assettype_list'),
    path('types/add/', views.AssetTypeEditView.as_view(), name='assettype_create'),
    path('types/<slug:slug>/', views.AssetTypeDetailView.as_view(), name='assettype_detail'),
    path('types/<slug:slug>/edit/', views.AssetTypeEditView.as_view(), name='assettype_update'),
    path('types/<slug:slug>/delete/', views.AssetTypeDeleteView.as_view(), name='assettype_delete'),

    # Component Types
    path('component-types/', views.ComponentTypeListView.as_view(), name='componenttype_list'),
    path('component-types/add/', views.ComponentTypeEditView.as_view(), name='componenttype_create'),
    path('component-types/<int:pk>/', views.ComponentTypeDetailView.as_view(), name='componenttype_detail'),
    path('component-types/<int:pk>/edit/', views.ComponentTypeEditView.as_view(), name='componenttype_update'),
    path('component-types/<int:pk>/delete/', views.ComponentTypeDeleteView.as_view(), name='componenttype_delete'),

    # Component Instances
    path('components/', views.ComponentInstanceListView.as_view(), name='componentinstance_list'),
    path('components/add/', views.ComponentInstanceEditView.as_view(), name='componentinstance_create'),
    path('components/<int:pk>/', views.ComponentInstanceDetailView.as_view(), name='componentinstance_detail'),
    path('components/<int:pk>/edit/', views.ComponentInstanceEditView.as_view(), name='componentinstance_update'),
    path('components/<int:pk>/delete/', views.ComponentInstanceDeleteView.as_view(), name='componentinstance_delete'),

    # Accessories
    path('accessories/', views.AccessoryListView.as_view(), name='accessory_list'),
    path('accessories/add/', views.AccessoryEditView.as_view(), name='accessory_create'),
    path('accessories/<int:pk>/', views.AccessoryDetailView.as_view(), name='accessory_detail'),
    path('accessories/<int:pk>/edit/', views.AccessoryEditView.as_view(), name='accessory_update'),
    path('accessories/<int:pk>/delete/', views.AccessoryDeleteView.as_view(), name='accessory_delete'),
    path('accessories/<int:pk>/checkout/', views.accessory_checkout, name='accessory_checkout'),
    path('accessories/assignments/<int:pk>/checkin/', views.accessory_checkin, name='accessory_checkin'),

    # Consumables
    path('consumables/', views.ConsumableListView.as_view(), name='consumable_list'),
    path('consumables/add/', views.ConsumableEditView.as_view(), name='consumable_create'),
    path('consumables/<int:pk>/', views.ConsumableDetailView.as_view(), name='consumable_detail'),
    path('consumables/<int:pk>/edit/', views.ConsumableEditView.as_view(), name='consumable_update'),
    path('consumables/<int:pk>/delete/', views.ConsumableDeleteView.as_view(), name='consumable_delete'),
    path('consumables/<int:pk>/checkout/', views.consumable_checkout, name='consumable_checkout'),
    
    # Asset Maintenances
    path('maintenances/', views.AssetMaintenanceListView.as_view(), name='assetmaintenance_list'),
    path('maintenances/add/', views.AssetMaintenanceEditView.as_view(), name='assetmaintenance_create'),
    path('maintenances/<int:pk>/', views.AssetMaintenanceDetailView.as_view(), name='assetmaintenance_detail'),
    path('maintenances/<int:pk>/edit/', views.AssetMaintenanceEditView.as_view(), name='assetmaintenance_update'),
    path('maintenances/<int:pk>/delete/', views.AssetMaintenanceDeleteView.as_view(), name='assetmaintenance_delete'),

    # Phase 4 Audits & Barcoding
    path('<int:pk>/audit/', views.asset_audit, name='asset_audit'),
    path('<int:pk>/print/', views.asset_label_print, name='asset_label_print'),
    path('custody/sign/<str:token>/', views.custody_eula_sign, name='custody_eula_sign'),

    # Custom Fields
    path('custom-fields/', views.CustomFieldListView.as_view(), name='customfield_list'),
    path('custom-fields/add/', views.CustomFieldEditView.as_view(), name='customfield_create'),
    path('custom-fields/<int:pk>/', views.CustomFieldDetailView.as_view(), name='customfield_detail'),
    path('custom-fields/<int:pk>/edit/', views.CustomFieldEditView.as_view(), name='customfield_update'),
    path('custom-fields/<int:pk>/delete/', views.CustomFieldDeleteView.as_view(), name='customfield_delete'),

    # Custom Fieldsets
    path('custom-fieldsets/', views.CustomFieldsetListView.as_view(), name='customfieldset_list'),
    path('custom-fieldsets/add/', views.CustomFieldsetEditView.as_view(), name='customfieldset_create'),
    path('custom-fieldsets/<int:pk>/', views.CustomFieldsetDetailView.as_view(), name='customfieldset_detail'),
    path('custom-fieldsets/<int:pk>/edit/', views.CustomFieldsetEditView.as_view(), name='customfieldset_update'),
    path('custom-fieldsets/<int:pk>/delete/', views.CustomFieldsetDeleteView.as_view(), name='customfieldset_delete'),

    # Depreciation
    path('depreciations/', views.DepreciationListView.as_view(), name='depreciation_list'),
    path('depreciations/add/', views.DepreciationEditView.as_view(), name='depreciation_create'),
    path('depreciations/<int:pk>/', views.DepreciationDetailView.as_view(), name='depreciation_detail'),
    path('depreciations/<int:pk>/edit/', views.DepreciationEditView.as_view(), name='depreciation_update'),
    path('depreciations/<int:pk>/delete/', views.DepreciationDeleteView.as_view(), name='depreciation_delete'),

    # Kits
    path('kits/', views.KitListView.as_view(), name='kit_list'),
    path('kits/add/', views.KitEditView.as_view(), name='kit_create'),
    path('kits/<int:pk>/', views.KitDetailView.as_view(), name='kit_detail'),
    path('kits/<int:pk>/edit/', views.KitEditView.as_view(), name='kit_update'),
    path('kits/<int:pk>/delete/', views.KitDeleteView.as_view(), name='kit_delete'),
    path('kits/<int:pk>/checkout/', views.kit_checkout_modal, name='kit_checkout_modal'),

    # Kit Items
    path('kit-items/add/', views.KitItemEditView.as_view(), name='kititem_create'),
    path('kit-items/<int:pk>/edit/', views.KitItemEditView.as_view(), name='kititem_update'),
    path('kit-items/<int:pk>/delete/', views.KitItemDeleteView.as_view(), name='kititem_delete'),

    # Suppliers
    path('suppliers/', views.SupplierListView.as_view(), name='supplier_list'),
    path('suppliers/add/', views.SupplierEditView.as_view(), name='supplier_create'),
    path('suppliers/<int:pk>/', views.SupplierDetailView.as_view(), name='supplier_detail'),
    path('suppliers/<int:pk>/edit/', views.SupplierEditView.as_view(), name='supplier_update'),
    path('suppliers/<int:pk>/delete/', views.SupplierDeleteView.as_view(), name='supplier_delete'),

    # Categories
    path('categories/', views.CategoryListView.as_view(), name='category_list'),
    path('categories/add/', views.CategoryEditView.as_view(), name='category_create'),
    path('categories/<int:pk>/', views.CategoryDetailView.as_view(), name='category_detail'),
    path('categories/<int:pk>/edit/', views.CategoryEditView.as_view(), name='category_update'),
    path('categories/<int:pk>/delete/', views.CategoryDeleteView.as_view(), name='category_delete'),

    # Asset Requests
    path('requests/', views.AssetRequestListView.as_view(), name='assetrequest_list'),
    path('requests/add/', views.AssetRequestCreateView.as_view(), name='assetrequest_create'),
    path('requests/<int:pk>/', views.AssetRequestDetailView.as_view(), name='assetrequest_detail'),
    path('requests/<int:pk>/edit/', views.AssetRequestEditView.as_view(), name='assetrequest_update'),
    path('requests/<int:pk>/delete/', views.AssetRequestDeleteView.as_view(), name='assetrequest_delete'),
] 