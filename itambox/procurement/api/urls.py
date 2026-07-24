from itambox.api.routers import ITAMBoxRouter

from .views import ContractViewSet, PurchaseOrderLineViewSet, PurchaseOrderViewSet

app_name = "procurement_api"

router = ITAMBoxRouter()
router.register(r"contracts", ContractViewSet)
router.register(r"purchase-orders", PurchaseOrderViewSet)
router.register(r"purchase-order-lines", PurchaseOrderLineViewSet)

urlpatterns = router.urls
