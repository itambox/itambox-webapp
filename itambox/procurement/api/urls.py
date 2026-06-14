from itambox.api.routers import ITAMBoxRouter
from .views import ContractViewSet

app_name = 'procurement_api'

router = ITAMBoxRouter()
router.register(r'contracts', ContractViewSet)

urlpatterns = router.urls
