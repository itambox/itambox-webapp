from itambox.api.routers import ITAMBoxRouter
from .views import CustodyReceiptViewSet, AssetMaintenanceViewSet, AuditSessionViewSet, AssetAuditViewSet

app_name = 'compliance_api'

router = ITAMBoxRouter()
router.register(r'custody-receipts', CustodyReceiptViewSet)
router.register(r'asset-maintenances', AssetMaintenanceViewSet)
router.register(r'audit-sessions', AuditSessionViewSet)
router.register(r'asset-audits', AssetAuditViewSet)

urlpatterns = router.urls
