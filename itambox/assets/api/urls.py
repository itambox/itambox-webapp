from itambox.api.routers import ITAMBoxRouter
from .views import (
    AssetViewSet, AssetRoleViewSet, ManufacturerViewSet,
    InstalledSoftwareViewSet, AssetTypeViewSet, StatusLabelViewSet,
    DepreciationViewSet, SupplierViewSet, CategoryViewSet,
    AssetRequestViewSet, AssetTagSequenceViewSet,
    AssetAssignmentViewSet, AuditSessionViewSet, AssetAuditViewSet
)

app_name = 'assets_api'

router = ITAMBoxRouter()
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
router.register(r'asset-assignments', AssetAssignmentViewSet)
router.register(r'audit-sessions', AuditSessionViewSet)
router.register(r'asset-audits', AssetAuditViewSet)

urlpatterns = router.urls

