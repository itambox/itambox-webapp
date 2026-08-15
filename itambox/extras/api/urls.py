from itambox.api.routers import ITAMBoxRouter

from .views import (
    AlertLogViewSet,
    AlertRuleViewSet,
    CustomFieldsetViewSet,
    CustomFieldViewSet,
    DashboardViewSet,
    EventRuleViewSet,
    JournalEntryViewSet,
    NotificationChannelViewSet,
    TagViewSet,
    WebhookDeliveryViewSet,
    WebhookEndpointViewSet,
)

app_name = "extras_api"

router = ITAMBoxRouter()
router.register(r"tags", TagViewSet)
router.register(r"dashboards", DashboardViewSet, basename="dashboard")
router.register(r"custom-fields", CustomFieldViewSet)
router.register(r"custom-fieldsets", CustomFieldsetViewSet)
router.register(r"event-rules", EventRuleViewSet)
router.register(r"webhook-endpoints", WebhookEndpointViewSet)
router.register(r"webhook-deliveries", WebhookDeliveryViewSet, basename="webhookdelivery")
router.register(r"notification-channels", NotificationChannelViewSet)
router.register(r"alert-logs", AlertLogViewSet)
router.register(r"alert-rules", AlertRuleViewSet)
router.register(r"journal-entries", JournalEntryViewSet)

urlpatterns = router.urls
