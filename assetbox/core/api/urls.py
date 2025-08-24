from django.urls import path, include
from assetbox.api.routers import AssetBoxRouter
from assetbox.api.views import ObjectChangeViewSet

app_name = 'core_api'

router = AssetBoxRouter()
router.register(r'object-changes', ObjectChangeViewSet, basename='objectchange')

urlpatterns = [
    path('', include(router.urls)),
]
