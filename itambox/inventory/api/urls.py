from itambox.api.routers import ITAMBoxRouter

from .views import (
    AccessoryAssignmentViewSet,
    AccessoryStockViewSet,
    AccessoryViewSet,
    ComponentAllocationViewSet,
    ComponentStockViewSet,
    ComponentViewSet,
    ConsumableAssignmentViewSet,
    ConsumableStockViewSet,
    ConsumableViewSet,
    KitItemViewSet,
    KitViewSet,
)

app_name = "inventory_api"

router = ITAMBoxRouter()
router.register(r"accessories", AccessoryViewSet)
router.register(r"accessory-stocks", AccessoryStockViewSet)
router.register(r"accessory-assignments", AccessoryAssignmentViewSet)
router.register(r"consumables", ConsumableViewSet)
router.register(r"consumable-stocks", ConsumableStockViewSet)
router.register(r"consumable-assignments", ConsumableAssignmentViewSet)
router.register(r"kits", KitViewSet)
router.register(r"kit-items", KitItemViewSet)
router.register(r"components", ComponentViewSet)
router.register(r"component-stocks", ComponentStockViewSet)
router.register(r"component-allocations", ComponentAllocationViewSet)

urlpatterns = router.urls
