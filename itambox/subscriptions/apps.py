from django.apps import AppConfig
from django.db.models.signals import post_migrate


class SubscriptionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'subscriptions'
    verbose_name = 'Subscriptions'

    def ready(self):
        import subscriptions.signals  # noqa
        import subscriptions.search  # noqa
        post_migrate.connect(self._register_subscription_tasks, sender=self)

    def _register_subscription_tasks(self, sender, **kwargs):
        # inline import: avoid AppRegistryNotReady at app-load time
        from django_q.models import Schedule
        from core.schedules import register_schedule

        register_schedule(
            'subscriptions.tasks.check_subscription_expiries_and_reminders',
            defaults={
                'name': 'Daily Subscription Expiries and Reminders',
                'schedule_type': Schedule.DAILY,
                'repeats': -1,
            },
        )
