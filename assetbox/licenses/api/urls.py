from core.api.routers import AssetBoxRouter
from .views import LicenseViewSet, LicenseSeatAssignmentViewSet

app_name = 'licenses_api'

router = AssetBoxRouter()
router.register(r'licenses', LicenseViewSet)
router.register(r'assignments', LicenseSeatAssignmentViewSet, basename='licenseseatassignment')

urlpatterns = router.urls
