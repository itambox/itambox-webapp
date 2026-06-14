from itambox.api.routers import ITAMBoxRouter
from .views import ContractViewSet

app_name = 'procurement_api'

router = ITAMBoxRouter()
router.register(r'contracts', ContractViewSet)

urlpatterns = router.urls

# ---------------------------------------------------------------------------
# NOTE FOR ORCHESTRATOR
# ---------------------------------------------------------------------------
# Mount this API module in itambox/itambox/api/urls.py by adding:
#
#     path('procurement/', include('procurement.api.urls', namespace='procurement_api')),
#
# inside the `urlpatterns` list (alongside the other app-level api paths).
# ---------------------------------------------------------------------------
