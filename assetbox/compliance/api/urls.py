from assetbox.api.routers import AssetBoxRouter
from .views import CustodyReceiptViewSet, AssetMaintenanceViewSet

app_name = 'compliance_api'

router = AssetBoxRouter()
router.register(r'custody-receipts', CustodyReceiptViewSet)
router.register(r'asset-maintenances', AssetMaintenanceViewSet)

urlpatterns = router.urls
