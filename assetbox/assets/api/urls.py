# assets/api/urls.py
# This file is no longer used for ViewSet routing as it's handled centrally
# in assetbox/api/urls.py. It can be used for app-specific, non-ViewSet API endpoints.

from rest_framework import routers
# Remove AssetTypeViewSet import for now
from .views import (
    AssetViewSet, AssetRoleViewSet, ManufacturerViewSet, 
    InstalledSoftwareViewSet # Add InstalledSoftwareViewSet
)

app_name = 'assets_api'  # Define the app_name for namespacing

router = routers.DefaultRouter()
router.register(r'assets', AssetViewSet)
router.register(r'asset-roles', AssetRoleViewSet)
router.register(r'manufacturers', ManufacturerViewSet)
router.register(r'installed-software', InstalledSoftwareViewSet) # Register new ViewSet
# Remove AssetTypeViewSet registration for now
# router.register(r'asset-types', AssetTypeViewSet)

# The DefaultRouter automatically creates the API root view named 'api-root'
urlpatterns = router.urls

# from api.routers import AssetBoxRouter # Removed
# from . import views # Removed
# app_name = 'assets-api' # Removed
# router = AssetBoxRouter() # Removed
# router.register(r'assets', views.AssetViewSet) # Removed
# router.register(r'asset-roles', views.AssetRoleViewSet) # Removed
# router.register(r'manufacturers', views.ManufacturerViewSet) # Removed

# urlpatterns = router.urls # Removed