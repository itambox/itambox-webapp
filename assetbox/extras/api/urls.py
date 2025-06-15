from rest_framework import routers
# Remove ConfigTemplateViewSet import for now
from .views import TagViewSet 

app_name = 'extras_api' # Define the app_name for namespacing

router = routers.DefaultRouter()
router.register(r'tags', TagViewSet)
# Remove ConfigTemplateViewSet registration for now
# router.register(r'config-templates', ConfigTemplateViewSet)

urlpatterns = router.urls
