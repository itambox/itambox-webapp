from assetbox.api.routers import AssetBoxRouter
from .views import (
    SiteViewSet, RegionViewSet, SiteGroupViewSet, LocationViewSet,
    TenantViewSet, TenantGroupViewSet, AssetHolderViewSet, AssetHolderAssignmentViewSet,
    ContactViewSet, ContactRoleViewSet, ContactAssignmentViewSet
)

app_name = 'organization_api'

router = AssetBoxRouter()
router.register(r'sites', SiteViewSet)
router.register(r'regions', RegionViewSet)
router.register(r'site-groups', SiteGroupViewSet)
router.register(r'locations', LocationViewSet)
router.register(r'tenants', TenantViewSet)
router.register(r'tenant-groups', TenantGroupViewSet)
router.register(r'asset-holders', AssetHolderViewSet)
router.register(r'asset-holder-assignments', AssetHolderAssignmentViewSet)
router.register(r'contacts', ContactViewSet)
router.register(r'contact-roles', ContactRoleViewSet)
router.register(r'contact-assignments', ContactAssignmentViewSet)

urlpatterns = router.urls
