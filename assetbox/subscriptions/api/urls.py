from core.api.routers import AssetBoxRouter
from .views import ProviderViewSet, SubscriptionViewSet, SubscriptionAssignmentViewSet

app_name = 'subscriptions_api'

router = AssetBoxRouter()
router.register(r'providers', ProviderViewSet)
router.register(r'subscriptions', SubscriptionViewSet)
router.register(r'assignments', SubscriptionAssignmentViewSet)

urlpatterns = router.urls
