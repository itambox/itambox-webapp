from core.api.routers import AssetBoxRouter
from .views import TagViewSet

app_name = 'extras_api'

router = AssetBoxRouter()
router.register(r'tags', TagViewSet)

urlpatterns = router.urls
