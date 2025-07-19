from core.api.routers import AssetBoxRouter
from .views import SoftwareViewSet

app_name = 'software_api'

router = AssetBoxRouter()
router.register(r'software', SoftwareViewSet)

urlpatterns = router.urls
