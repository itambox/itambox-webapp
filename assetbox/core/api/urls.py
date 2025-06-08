# core/api/urls.py
# This file is no longer used for ViewSet routing as it's handled centrally
# in assetbox/api/urls.py. It can be used for app-specific, non-ViewSet API endpoints.

from rest_framework.routers import DefaultRouter
from . import views

app_name = 'core_api'  # Use underscore to match include namespace

router = DefaultRouter()
# Register the viewsets that actually exist in core/api/views.py
router.register(r'user-preferences', views.UserPreferenceViewSet, basename='userpreference')
router.register(r'object-changes', views.ObjectChangeViewSet, basename='objectchange')
# If TagViewSet exists elsewhere (e.g., extras app), it should be registered there.
# router.register(r'tags', views.TagViewSet)

# The DefaultRouter automatically creates the API root view named 'api-root'
urlpatterns = router.urls