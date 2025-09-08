from django.urls import path
from . import views

app_name = 'inventory'

urlpatterns = [
    # Accessories
    path('accessories/', views.AccessoryListView.as_view(), name='accessory_list'),
    path('accessories/add/', views.AccessoryEditView.as_view(), name='accessory_create'),
    path('accessories/edit/', views.AccessoryBulkEditView.as_view(), name='accessory_bulk_edit'),
    path('accessories/delete/', views.AccessoryBulkDeleteView.as_view(), name='accessory_bulk_delete'),
    path('accessories/<int:pk>/', views.AccessoryDetailView.as_view(), name='accessory_detail'),
    path('accessories/<int:pk>/edit/', views.AccessoryEditView.as_view(), name='accessory_update'),
    path('accessories/<int:pk>/delete/', views.AccessoryDeleteView.as_view(), name='accessory_delete'),
    path('accessories/<int:pk>/clone/', views.AccessoryCloneView.as_view(), name='accessory_clone'),
    path('accessories/<int:pk>/checkout/', views.AccessoryCheckoutView.as_view(), name='accessory_checkout'),
    path('accessories/assignments/<int:pk>/checkin/', views.AccessoryCheckinView.as_view(), name='accessory_checkin'),
    path('accessories/import/', views.AccessoryImportView.as_view(), name='accessory_import'),
    path('accessory-stocks/', views.AccessoryStockListView.as_view(), name='accessorystock_list'),
    path('accessory-stocks/add/', views.AccessoryStockEditView.as_view(), name='accessorystock_create'),
    path('accessory-stocks/<int:pk>/edit/', views.AccessoryStockEditView.as_view(), name='accessorystock_update'),
    path('accessory-stocks/<int:pk>/delete/', views.AccessoryStockDeleteView.as_view(), name='accessorystock_delete'),

    # Consumables
    path('consumables/', views.ConsumableListView.as_view(), name='consumable_list'),
    path('consumables/add/', views.ConsumableEditView.as_view(), name='consumable_create'),
    path('consumables/edit/', views.ConsumableBulkEditView.as_view(), name='consumable_bulk_edit'),
    path('consumables/delete/', views.ConsumableBulkDeleteView.as_view(), name='consumable_bulk_delete'),
    path('consumables/<int:pk>/', views.ConsumableDetailView.as_view(), name='consumable_detail'),
    path('consumables/<int:pk>/edit/', views.ConsumableEditView.as_view(), name='consumable_update'),
    path('consumables/<int:pk>/delete/', views.ConsumableDeleteView.as_view(), name='consumable_delete'),
    path('consumables/<int:pk>/clone/', views.ConsumableCloneView.as_view(), name='consumable_clone'),
    path('consumables/<int:pk>/checkout/', views.ConsumableCheckoutView.as_view(), name='consumable_checkout'),
    path('consumables/import/', views.ConsumableImportView.as_view(), name='consumable_import'),
    path('consumable-stocks/', views.ConsumableStockListView.as_view(), name='consumablestock_list'),
    path('consumable-stocks/add/', views.ConsumableStockEditView.as_view(), name='consumablestock_create'),
    path('consumable-stocks/<int:pk>/edit/', views.ConsumableStockEditView.as_view(), name='consumablestock_update'),
    path('consumable-stocks/<int:pk>/delete/', views.ConsumableStockDeleteView.as_view(), name='consumablestock_delete'),

    # Kits
    path('kits/', views.KitListView.as_view(), name='kit_list'),
    path('kits/add/', views.KitEditView.as_view(), name='kit_create'),
    path('kits/<int:pk>/', views.KitDetailView.as_view(), name='kit_detail'),
    path('kits/<int:pk>/edit/', views.KitEditView.as_view(), name='kit_update'),
    path('kits/<int:pk>/delete/', views.KitDeleteView.as_view(), name='kit_delete'),
    path('kits/<int:pk>/checkout/', views.KitCheckoutView.as_view(), name='kit_checkout_modal'),
    path('kit-items/add/', views.KitItemEditView.as_view(), name='kititem_create'),
    path('kit-items/<int:pk>/edit/', views.KitItemEditView.as_view(), name='kititem_update'),
    path('kit-items/<int:pk>/delete/', views.KitItemDeleteView.as_view(), name='kititem_delete'),
]
