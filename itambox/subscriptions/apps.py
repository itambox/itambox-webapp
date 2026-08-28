from django.apps import AppConfig
from django.db.models.signals import post_migrate

from itambox.capabilities import (
    ALWAYS_ON,
    CAPABILITY_REGISTRY_DOC_URL,
    CONTRACT_VERSION,
    SOURCE_ALWAYS,
    STABLE,
    Capability,
)
from itambox.capabilities import (
    registry as capability_registry,
)
from itambox.registry import registry as generic_presentation_registry


class SubscriptionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "subscriptions"
    verbose_name = "Subscriptions"

    def ready(self):
        # inline imports: app-registry: subscription forms, signals, search,
        # presentation, and seat usage load after app population.
        import subscriptions.forms  # noqa: F401 -- side-effect import registers curated import forms
        import subscriptions.search  # noqa
        import subscriptions.signals  # noqa
        from subscriptions.feature_views import SUBSCRIPTIONS_GENERIC_PRESENTATION_PROVIDER
        from subscriptions.models_seat_usage import register_seat_usage
        from subscriptions.seat_services import count_assigned_seats

        register_seat_usage(count_assigned_seats)
        self._register_capabilities()
        self._register_generic_presentation(SUBSCRIPTIONS_GENERIC_PRESENTATION_PROVIDER)
        post_migrate.connect(self._register_subscription_tasks, sender=self)

    def _register_capabilities(self):
        capability_registry.register_all(self._capabilities())

    def _register_generic_presentation(self, provider):
        generic_presentation_registry.register_generic_presentation(
            "subscriptions",
            provider,
            detail_features=("subscribable",),
            list_params=False,
            list_filter=False,
            list_context=False,
            priority=200,
        )

    def _capabilities(self):
        return (
            Capability(
                key="subscriptions.tracking",
                title="SaaS Subscriptions",
                owning_area="area:subscriptions",
                maturity=STABLE,
                security_critical=False,
                activation=ALWAYS_ON,
                activation_probe=None,
                activation_source=SOURCE_ALWAYS,
                owns=(
                    "subscriptions.Provider",
                    "subscriptions.Subscription",
                    "subscriptions.SubscriptionAssignment",
                ),
                docs_url=CAPABILITY_REGISTRY_DOC_URL,
                limitations=(),
                contract_version=CONTRACT_VERSION,
            ),
        )

    def _register_subscription_tasks(self, sender, **kwargs):
        # inline import: app-registry: avoid AppRegistryNotReady at app-load time
        from django_q.models import Schedule

        from core.schedules import register_schedule

        register_schedule(
            "subscriptions.tasks.check_subscription_expiries_and_reminders",
            defaults={
                "name": "Daily Subscription Expiries and Reminders",
                "schedule_type": Schedule.DAILY,
                "repeats": -1,
            },
        )
