from assetbox.api.routers import AssetBoxRouter
from .views import ComponentTypeViewSet, ComponentInstanceViewSet

app_name = 'components_api'

router = AssetBoxRouter()
router.register(r'component-types', ComponentTypeViewSet)
router.register(r'component-instances', ComponentInstanceViewSet)

urlpatterns = router.urls
