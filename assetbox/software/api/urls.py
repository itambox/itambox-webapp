from rest_framework import routers
from .views import SoftwareViewSet

app_name = 'software_api' # Define the app_name for namespacing

router = routers.DefaultRouter()
router.register(r'software', SoftwareViewSet)

urlpatterns = router.urls 