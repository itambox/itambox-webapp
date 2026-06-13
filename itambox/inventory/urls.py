from django.urls import path
from . import views

app_name = 'inventory'

urlpatterns = [
    # Unified Inventory
    path('inventory/', views.InventoryListView.as_view(), name='inventory_list'),

    # Accessories
    path('accessories/', views.AccessoryListView.as_view(), name='accessory_list'),
    path('accessories/add/', views.AccessoryEditView.as_view(), name='accessory_create'),
    path('accessories/edit/', views.AccessoryBulkEditView.as_view(), name='accessory_bulk_edit'),
    path('accessories/delete/', views.AccessoryBulkDeleteView.as_view(), name='accessory_bulk_delete'),
    path('accessories/assignments/', views.AccessoryAssignmentListView.as_view(), name='accessoryassignment_list'),
    path('accessories/<int:pk>/', views.AccessoryDetailView.as_view(), name='accessory_detail'),
    path('accessories/<int:pk>/edit/', views.AccessoryEditView.as_view(), name='accessory_update'),
    path('accessories/<int:pk>/delete/', views.AccessoryDeleteView.as_view(), name='accessory_delete'),
    path('accessories/<int:pk>/clone/', views.AccessoryCloneView.as_view(), name='accessory_clone'),
    path('accessories/<int:pk>/checkout/', views.AccessoryCheckoutView.as_view(), name='accessory_checkout'),
    path('accessories/<int:pk>/add-stock/', views.AccessoryStockCreateModalView.as_view(), name='accessory_add_stock'),
    path('accessories/assignments/<int:pk>/checkin/', views.AccessoryCheckinView.as_view(), name='accessory_checkin'),
    path('accessories/import/', views.AccessoryImportView.as_view(), name='accessory_import'),
    path('accessory-stocks/', views.AccessoryStockListView.as_view(), name='accessorystock_list'),
    path('accessory-stocks/add/', views.AccessoryStockEditView.as_view(), name='accessorystock_create'),
    path('accessory-stocks/<int:pk>/edit/', views.AccessoryStockEditView.as_view(), name='accessorystock_update'),
    path('accessory-stocks/<int:pk>/delete/', views.AccessoryStockDeleteView.as_view(), name='accessorystock_delete'),
    path('accessory-stocks/<int:pk>/adjust/', views.AccessoryStockAdjustView.as_view(), name='accessorystock_adjust'),

    # Consumables
    path('consumables/', views.ConsumableListView.as_view(), name='consumable_list'),
    path('consumables/add/', views.ConsumableEditView.as_view(), name='consumable_create'),
    path('consumables/edit/', views.ConsumableBulkEditView.as_view(), name='consumable_bulk_edit'),
    path('consumables/delete/', views.ConsumableBulkDeleteView.as_view(), name='consumable_bulk_delete'),
    path('inventory/bulk-checkout/', views.bulk_checkout_inventory, name='inventory_bulk_checkout'),
    path('consumables/consumptions/', views.ConsumableAssignmentListView.as_view(), name='consumableassignment_list'),
    path('consumables/<int:pk>/', views.ConsumableDetailView.as_view(), name='consumable_detail'),
    path('consumables/<int:pk>/edit/', views.ConsumableEditView.as_view(), name='consumable_update'),
    path('consumables/<int:pk>/delete/', views.ConsumableDeleteView.as_view(), name='consumable_delete'),
    path('consumables/<int:pk>/clone/', views.ConsumableCloneView.as_view(), name='consumable_clone'),
    path('consumables/<int:pk>/checkout/', views.ConsumableCheckoutView.as_view(), name='consumable_checkout'),
    path('consumables/<int:pk>/add-stock/', views.ConsumableStockCreateModalView.as_view(), name='consumable_add_stock'),
    path('consumables/import/', views.ConsumableImportView.as_view(), name='consumable_import'),
    path('consumable-stocks/', views.ConsumableStockListView.as_view(), name='consumablestock_list'),
    path('consumable-stocks/add/', views.ConsumableStockEditView.as_view(), name='consumablestock_create'),
    path('consumable-stocks/<int:pk>/edit/', views.ConsumableStockEditView.as_view(), name='consumablestock_update'),
    path('consumable-stocks/<int:pk>/delete/', views.ConsumableStockDeleteView.as_view(), name='consumablestock_delete'),
    path('consumable-stocks/<int:pk>/adjust/', views.ConsumableStockAdjustView.as_view(), name='consumablestock_adjust'),

    # Kits
    path('kits/', views.KitListView.as_view(), name='kit_list'),
    path('kits/add/', views.KitEditView.as_view(), name='kit_create'),
    path('kits/<int:pk>/', views.KitDetailView.as_view(), name='kit_detail'),
    path('kits/<int:pk>/edit/', views.KitEditView.as_view(), name='kit_update'),
    path('kits/<int:pk>/clone/', views.KitCloneView.as_view(), name='kit_clone'),
    path('kits/<int:pk>/delete/', views.KitDeleteView.as_view(), name='kit_delete'),
    path('kits/<int:pk>/checkout/', views.KitCheckoutView.as_view(), name='kit_checkout_modal'),
    # Kit Items
    path('kit-items/add/', views.KitItemEditView.as_view(), name='kititem_create'),
    path('kit-items/<int:pk>/edit/', views.KitItemEditView.as_view(), name='kititem_update'),
    path('kit-items/<int:pk>/delete/', views.KitItemDeleteView.as_view(), name='kititem_delete'),

    # Components
    path('components/', views.ComponentListView.as_view(), name='component_list'),
    path('components/add/', views.ComponentEditView.as_view(), name='component_create'),
    path('components/<int:pk>/', views.ComponentDetailView.as_view(), name='component_detail'),
    path('components/<int:pk>/edit/', views.ComponentEditView.as_view(), name='component_update'),
    path('components/<int:pk>/delete/', views.ComponentDeleteView.as_view(), name='component_delete'),
    path('components/<int:pk>/clone/', views.ComponentCloneView.as_view(), name='component_clone'),
    path('components/<int:pk>/checkout/', views.ComponentCheckoutView.as_view(), name='component_checkout'),
    path('components/<int:pk>/add-stock/', views.ComponentStockCreateModalView.as_view(), name='component_add_stock'),
    path('components/allocations/<int:pk>/checkin/', views.ComponentCheckinView.as_view(), name='component_checkin'),

    # Component Stocks
    path('component-stocks/', views.ComponentStockListView.as_view(), name='componentstock_list'),
    path('component-stocks/add/', views.ComponentStockEditView.as_view(), name='componentstock_create'),
    path('component-stocks/<int:pk>/edit/', views.ComponentStockEditView.as_view(), name='componentstock_update'),
    path('component-stocks/<int:pk>/delete/', views.ComponentStockDeleteView.as_view(), name='componentstock_delete'),
    path('component-stocks/<int:pk>/adjust/', views.ComponentStockAdjustView.as_view(), name='componentstock_adjust'),

    # Component Allocations
    path('component-allocations/', views.ComponentAllocationListView.as_view(), name='componentallocation_list'),
    path('component-allocations/add/', views.ComponentAllocationEditView.as_view(), name='componentallocation_create'),
    path('component-allocations/<int:pk>/edit/', views.ComponentAllocationEditView.as_view(), name='componentallocation_update'),
    path('component-allocations/<int:pk>/delete/', views.ComponentAllocationDeleteView.as_view(), name='componentallocation_delete'),
]
