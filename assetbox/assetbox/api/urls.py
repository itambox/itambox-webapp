# assetbox/api/urls.py
from django.urls import path, include
from .views import APIRootView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularJSONAPIView

app_name = 'api' # Required when including with a namespace

# app_name = 'api' # Namespace is defined in the project's root urls.py when including this file

urlpatterns = [
    # Base views
    path("", APIRootView.as_view(), name="api-root"),
    # Apps
    path("assets/", include("assets.api.urls", namespace="assets_api")),
    path("core/", include("core.api.urls", namespace="core_api")),
    path("organization/", include("organization.api.urls", namespace="organization_api")),
    path("extras/", include("extras.api.urls", namespace="extras_api")),
    # API Schema
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    # Added schema UI/JSON paths back, assuming they were intended
    path("schema/swagger-ui/", SpectacularSwaggerView.as_view(url_name="api:schema"), name="swagger-ui"),
    path("schema/openapi.json", SpectacularJSONAPIView.as_view(), name="openapi-schema"),
] 