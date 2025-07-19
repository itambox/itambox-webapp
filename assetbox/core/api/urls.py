from django.urls import path, include
from core.api.routers import AssetBoxRouter
from . import views

app_name = 'core_api'

router = AssetBoxRouter()
router.register(r'object-changes', views.ObjectChangeViewSet, basename='objectchange')

urlpatterns = [
    path('', include(router.urls)),
]
