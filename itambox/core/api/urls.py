from django.urls import path, include
from itambox.api.routers import ITAMBoxRouter
from itambox.api.views import ObjectChangeViewSet

app_name = 'core_api'

router = ITAMBoxRouter()
router.register(r'object-changes', ObjectChangeViewSet, basename='objectchange')

urlpatterns = [
    path('', include(router.urls)),
]
