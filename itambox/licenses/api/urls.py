from itambox.api.routers import ITAMBoxRouter
from .views import LicenseViewSet, LicenseSeatAssignmentViewSet

app_name = 'licenses_api'

router = ITAMBoxRouter()
router.register(r'licenses', LicenseViewSet)
router.register(r'assignments', LicenseSeatAssignmentViewSet, basename='licenseseatassignment')

urlpatterns = router.urls
