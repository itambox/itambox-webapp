from assetbox.api.routers import AssetBoxRouter
from .views import (
    ComponentViewSet, ComponentStockViewSet, ComponentAllocationViewSet,
)

app_name = 'components_api'

router = AssetBoxRouter()
router.register(r'components', ComponentViewSet)
router.register(r'component-stocks', ComponentStockViewSet)
router.register(r'component-allocations', ComponentAllocationViewSet)

urlpatterns = router.urls
