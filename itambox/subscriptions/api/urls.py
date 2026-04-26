from itambox.api.routers import ITAMBoxRouter
from .views import ProviderViewSet, SubscriptionViewSet, SubscriptionAssignmentViewSet

app_name = 'subscriptions_api'

router = ITAMBoxRouter()
router.register(r'providers', ProviderViewSet)
router.register(r'subscriptions', SubscriptionViewSet)
router.register(r'assignments', SubscriptionAssignmentViewSet)

urlpatterns = router.urls
