from assetbox.api.routers import AssetBoxRouter
from .views import (
    AccessoryViewSet, AccessoryAssignmentViewSet,
    ConsumableViewSet, ConsumableAssignmentViewSet,
    KitViewSet, KitItemViewSet
)

app_name = 'inventory_api'

router = AssetBoxRouter()
router.register(r'accessories', AccessoryViewSet)
router.register(r'accessory-assignments', AccessoryAssignmentViewSet)
router.register(r'consumables', ConsumableViewSet)
router.register(r'consumable-assignments', ConsumableAssignmentViewSet)
router.register(r'kits', KitViewSet)
router.register(r'kit-items', KitItemViewSet)

urlpatterns = router.urls
