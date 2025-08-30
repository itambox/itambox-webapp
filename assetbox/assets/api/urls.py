from assetbox.api.routers import AssetBoxRouter
from .views import (
    AssetViewSet, AssetRoleViewSet, ManufacturerViewSet,
    InstalledSoftwareViewSet, AssetTypeViewSet, StatusLabelViewSet,
    DepreciationViewSet, SupplierViewSet, CategoryViewSet,
    AssetRequestViewSet, AssetTagSequenceViewSet, ActivityLogViewSet
)

app_name = 'assets_api'

router = AssetBoxRouter()
router.register(r'assets', AssetViewSet)
router.register(r'asset-roles', AssetRoleViewSet)
router.register(r'manufacturers', ManufacturerViewSet)
router.register(r'installed-software', InstalledSoftwareViewSet)
router.register(r'asset-types', AssetTypeViewSet)
router.register(r'status-labels', StatusLabelViewSet)
router.register(r'depreciations', DepreciationViewSet)
router.register(r'suppliers', SupplierViewSet)
router.register(r'categories', CategoryViewSet)
router.register(r'asset-requests', AssetRequestViewSet)
router.register(r'asset-tag-sequences', AssetTagSequenceViewSet)
router.register(r'activity-logs', ActivityLogViewSet, basename='activitylog')

urlpatterns = router.urls
