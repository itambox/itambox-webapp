from django.urls import path, include
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularJSONAPIView,
    SpectacularRedocView
)
from itambox.api.routers import ITAMBoxRouter
from itambox.api.views import APIRootView, StatusView, AuthenticationCheckView, ObjectChangeViewSet
from inventory.api.views import ComponentViewSet, ComponentStockViewSet, ComponentAllocationViewSet

_core_router = ITAMBoxRouter()
_core_router.register(r'object-changes', ObjectChangeViewSet, basename='objectchange')

app_name = 'api'

urlpatterns = [
    path('', APIRootView.as_view(), name='api-root'),
    path('status/', StatusView.as_view(), name='api-status'),
    path('auth-check/', AuthenticationCheckView.as_view(), name='auth-check'),

    path('assets/', include('assets.api.urls', namespace='assets_api')),
    path('compliance/', include('compliance.api.urls', namespace='compliance_api')),
    path('components/', include(([
        path('components/', ComponentViewSet.as_view({'get': 'list', 'post': 'create'})),
        path('components/<int:pk>/', ComponentViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}), name='component-detail'),
        path('component-stocks/', ComponentStockViewSet.as_view({'get': 'list', 'post': 'create'})),
        path('component-stocks/<int:pk>/', ComponentStockViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}), name='componentstock-detail'),
        path('component-allocations/', ComponentAllocationViewSet.as_view({'get': 'list', 'post': 'create'})),
        path('component-allocations/<int:pk>/', ComponentAllocationViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}), name='componentallocation-detail'),
    ], 'components_api'), namespace='components_api')),
    path('core/', include((_core_router.urls, 'core_api'))),
    path('extras/', include('extras.api.urls', namespace='extras_api')),
    path('inventory/', include('inventory.api.urls', namespace='inventory_api')),
    path('licenses/', include('licenses.api.urls', namespace='licenses_api')),
    path('organization/', include('organization.api.urls', namespace='organization_api')),
    path('software/', include('software.api.urls', namespace='software_api')),
    path('subscriptions/', include('subscriptions.api.urls', namespace='subscriptions_api')),
    path('users/', include('users.api.urls', namespace='users_api')),
    path('tenants/<slug:tenant_slug>/scim/v2/', include('users.api.scim.urls', namespace='scim')),

    path('schema/openapi.json', SpectacularJSONAPIView.as_view(), name='openapi-schema'),
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    path('schema/swagger-ui/', SpectacularSwaggerView.as_view(url_name='api:openapi-schema'), name='swagger-ui'),
    path('schema/redoc/', SpectacularRedocView.as_view(url_name='api:openapi-schema'), name='redoc'),
]
