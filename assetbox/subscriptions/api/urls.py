from rest_framework import routers
from .views import ProviderViewSet, SubscriptionViewSet, SubscriptionAssignmentViewSet

app_name = 'subscriptions_api' # Define app_name

router = routers.DefaultRouter()
router.register(r'providers', ProviderViewSet)
router.register(r'subscriptions', SubscriptionViewSet)
router.register(r'assignments', SubscriptionAssignmentViewSet, basename='subscriptionassignment')

urlpatterns = router.urls 