from itambox.api.routers import ITAMBoxRouter
from .views import SoftwareViewSet

app_name = 'software_api'

router = ITAMBoxRouter()
router.register(r'software', SoftwareViewSet)

urlpatterns = router.urls
