from itambox.api.routers import ITAMBoxRouter
from .views import (
    SiteViewSet, RegionViewSet, SiteGroupViewSet, LocationViewSet,
    TenantViewSet, TenantGroupViewSet, AssetHolderViewSet,
    ContactViewSet, ContactRoleViewSet, ContactAssignmentViewSet,
    CostCenterViewSet,
)

app_name = 'organization_api'

router = ITAMBoxRouter()
router.register(r'sites', SiteViewSet)
router.register(r'regions', RegionViewSet)
router.register(r'site-groups', SiteGroupViewSet)
router.register(r'locations', LocationViewSet)
router.register(r'tenants', TenantViewSet)
router.register(r'tenant-groups', TenantGroupViewSet)
router.register(r'asset-holders', AssetHolderViewSet)

router.register(r'contacts', ContactViewSet)
router.register(r'contact-roles', ContactRoleViewSet)
router.register(r'contact-assignments', ContactAssignmentViewSet)
router.register(r'cost-centers', CostCenterViewSet)

urlpatterns = router.urls
