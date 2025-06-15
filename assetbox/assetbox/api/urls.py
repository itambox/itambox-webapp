# assetbox/api/urls.py
from django.urls import path, include
from core.api.views import APIRootView
# Import Spectacular views
from drf_spectacular.views import (
    SpectacularAPIView, 
    SpectacularSwaggerView, 
    SpectacularJSONAPIView, # Use this for openapi.json
    SpectacularRedocView # Optional: Add Redoc view
)

app_name = 'api'

urlpatterns = [
    # Map the root /api/ path to our custom APIRootView
    path('', APIRootView.as_view(), name='api-root'),
    # Include app-specific API URLs under their respective paths
    path('assets/', include('assets.api.urls', namespace='assets_api')),
    path('core/', include('core.api.urls', namespace='core_api')),
    path('extras/', include('extras.api.urls', namespace='extras_api')),
    path('licenses/', include('licenses.api.urls', namespace='licenses_api')),
    path('organization/', include('organization.api.urls', namespace='organization_api')),
    path('software/', include('software.api.urls', namespace='software_api')),
    path('subscriptions/', include('subscriptions.api.urls', namespace='subscriptions_api')),
    # Add other app includes (like users) here if they have APIs
    path('users/', include('users.api.urls', namespace='users_api')),
    
    # --- DRF Spectacular URLs --- 
    # Schema downloads
    path('schema/openapi.json', SpectacularJSONAPIView.as_view(), name='openapi-schema'), # Serve JSON schema
    path('schema/', SpectacularAPIView.as_view(), name='schema'), # Serve YAML schema (optional but common)
    # UI Views
    path('schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='api:openapi-schema'), name='swagger-ui'), # Point to JSON schema
    path('schema/redoc/', SpectacularRedocView.as_view(url_name='api:openapi-schema'), name='redoc'), # Optional Redoc UI
] 