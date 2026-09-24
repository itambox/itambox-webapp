from itambox.api.routers import ITAMBoxRouter

from .views import SubscriptionAssignmentViewSet, SubscriptionViewSet

app_name = "subscriptions_api"

router = ITAMBoxRouter()
router.register(r"subscriptions", SubscriptionViewSet)
router.register(r"assignments", SubscriptionAssignmentViewSet)

urlpatterns = router.urls
