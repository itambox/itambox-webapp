# organization/api/urls.py
# This file is no longer used for ViewSet routing as it's handled centrally
# in assetbox/api/urls.py. It can be used for app-specific, non-ViewSet API endpoints.

from rest_framework import routers
from .views import (SiteViewSet, RegionViewSet, LocationViewSet, AssetHolderViewSet)

app_name = 'organization_api'  # Define the app_name for namespacing

router = routers.DefaultRouter()
router.register(r'sites', SiteViewSet)
router.register(r'regions', RegionViewSet)
router.register(r'locations', LocationViewSet)
router.register(r'asset-holders', AssetHolderViewSet)

# The DefaultRouter automatically creates the API root view named 'api-root'
urlpatterns = router.urls 