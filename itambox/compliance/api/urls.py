from itambox.api.routers import ITAMBoxRouter
from .views import CustodyReceiptViewSet, AssetMaintenanceViewSet

app_name = 'compliance_api'

router = ITAMBoxRouter()
router.register(r'custody-receipts', CustodyReceiptViewSet)
router.register(r'asset-maintenances', AssetMaintenanceViewSet)

urlpatterns = router.urls
