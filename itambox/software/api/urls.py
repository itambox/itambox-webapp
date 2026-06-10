from itambox.api.routers import ITAMBoxRouter
from .views import SoftwareViewSet, InstalledSoftwareViewSet

app_name = 'software_api'

router = ITAMBoxRouter()
router.register(r'software', SoftwareViewSet)
router.register(r'installed-software', InstalledSoftwareViewSet)

urlpatterns = router.urls
