from itambox.api.routers import ITAMBoxRouter
from .views import TagViewSet, DashboardViewSet, CustomFieldViewSet, CustomFieldsetViewSet

app_name = 'extras_api'

router = ITAMBoxRouter()
router.register(r'tags', TagViewSet)
router.register(r'dashboards', DashboardViewSet, basename='dashboard')
router.register(r'custom-fields', CustomFieldViewSet)
router.register(r'custom-fieldsets', CustomFieldsetViewSet)

urlpatterns = router.urls
