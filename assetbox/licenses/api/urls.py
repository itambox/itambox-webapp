from rest_framework import routers
from .views import LicenseViewSet, LicenseSeatAssignmentViewSet

app_name = 'licenses_api' # Define app_name

router = routers.DefaultRouter()
router.register(r'licenses', LicenseViewSet)
router.register(r'assignments', LicenseSeatAssignmentViewSet, basename='licenseseatassignment')

urlpatterns = router.urls 