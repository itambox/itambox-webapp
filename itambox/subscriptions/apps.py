from django.apps import AppConfig
from django.db.models.signals import post_migrate

from itambox.capabilities import ALWAYS_ON, CONTRACT_VERSION, SOURCE_ALWAYS, STABLE, Capability, registry


class SubscriptionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "subscriptions"
    verbose_name = "Subscriptions"

    def ready(self):
        import subscriptions.signals  # noqa
        import subscriptions.search  # noqa

        # inline import: app-registry: curated import forms load only after the app registry is ready.
        import subscriptions.forms

        self._register_capabilities()
        post_migrate.connect(self._register_subscription_tasks, sender=self)

    def _register_capabilities(self):
        registry.register_all(self._capabilities())

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
                docs_url="development/capability-registry.md",
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
