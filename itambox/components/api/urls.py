from itambox.api.routers import ITAMBoxRouter
from .views import (
    ComponentViewSet, ComponentStockViewSet, ComponentAllocationViewSet,
)

app_name = 'components_api'

router = ITAMBoxRouter()
router.register(r'components', ComponentViewSet)
router.register(r'component-stocks', ComponentStockViewSet)
router.register(r'component-allocations', ComponentAllocationViewSet)

urlpatterns = router.urls
