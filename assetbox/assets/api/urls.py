from assetbox.api.routers import AssetBoxRouter
from .views import (
    AssetViewSet, AssetRoleViewSet, ManufacturerViewSet,
    InstalledSoftwareViewSet
)

app_name = 'assets_api'

router = AssetBoxRouter()
router.register(r'assets', AssetViewSet)
router.register(r'asset-roles', AssetRoleViewSet)
router.register(r'manufacturers', ManufacturerViewSet)
router.register(r'installed-software', InstalledSoftwareViewSet)

urlpatterns = router.urls
