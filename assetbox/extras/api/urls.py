from rest_framework import routers
from .views import TagViewSet 

app_name = 'extras_api' # Define the app_name for namespacing

router = routers.DefaultRouter()
router.register(r'tags', TagViewSet)

urlpatterns = router.urls
