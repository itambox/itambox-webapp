# assets/api/urls.py
# This file is no longer used for ViewSet routing as it's handled centrally
# in assetbox/api/urls.py. It can be used for app-specific, non-ViewSet API endpoints.

from rest_framework.routers import DefaultRouter
from . import views

app_name = 'assets_api'  # Use underscore to match include namespace

router = DefaultRouter()
router.register(r'assets', views.AssetViewSet)
router.register(r'asset-roles', views.AssetRoleViewSet) # Assuming AssetRole is the intended model/view name
router.register(r'manufacturers', views.ManufacturerViewSet)

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