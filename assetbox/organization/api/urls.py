# organization/api/urls.py
# This file is no longer used for ViewSet routing as it's handled centrally
# in assetbox/api/urls.py. It can be used for app-specific, non-ViewSet API endpoints.

from rest_framework.routers import DefaultRouter
from . import views

app_name = 'organization_api'  # Use underscore to match include namespace

router = DefaultRouter()
router.register(r'regions', views.RegionViewSet)
router.register(r'site-groups', views.SiteGroupViewSet)
router.register(r'sites', views.SiteViewSet)
router.register(r'locations', views.LocationViewSet)
router.register(r'tenants', views.TenantViewSet)
router.register(r'asset-holders', views.AssetHolderViewSet) # Assuming AssetHolder is the model/view name

# The DefaultRouter automatically creates the API root view named 'api-root'
urlpatterns = router.urls 