from .base_views import (
    InventoryListView, bulk_checkout_inventory,
)
from .accessory_views import (
    AccessoryListView, AccessoryDetailView, AccessoryEditView, AccessoryDeleteView,
    AccessoryCloneView, AccessoryCheckoutView, AccessoryCheckinView,
    AccessoryBulkEditView, AccessoryBulkDeleteView,
    AccessoryStockListView, AccessoryStockEditView, AccessoryStockDeleteView,
    AccessoryAssignmentListView, AccessoryStockAdjustView, AccessoryStockCreateModalView,
)
from .consumable_views import (
    ConsumableListView, ConsumableDetailView, ConsumableEditView, ConsumableDeleteView,
    ConsumableCloneView, ConsumableCheckoutView,
    ConsumableBulkEditView, ConsumableBulkDeleteView,
    ConsumableStockListView, ConsumableStockEditView, ConsumableStockDeleteView,
    ConsumableAssignmentListView, ConsumableStockAdjustView, ConsumableStockCreateModalView,
)
from .kit_views import (
    KitListView, KitDetailView, KitEditView, KitCloneView, KitDeleteView,
    KitItemEditView, KitItemDeleteView, KitCheckoutView,
)
from .component_views import (
    ComponentListView, ComponentDetailView, ComponentEditView, ComponentDeleteView,
    ComponentCloneView, ComponentCheckoutView, ComponentCheckinView,
    ComponentStockListView, ComponentStockEditView, ComponentStockDeleteView,
    ComponentStockAdjustView, ComponentStockCreateModalView,
    ComponentAllocationListView, ComponentAllocationEditView, ComponentAllocationDeleteView,
)
