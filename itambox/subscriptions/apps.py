from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'subscriptions'
    verbose_name = 'Subscriptions'

    def ready(self):
        import subscriptions.signals  # noqa
        import subscriptions.search  # noqa
        self._register_subscription_tasks()

    def _register_subscription_tasks(self):
        try:
            from django.db import connection
            tables = connection.introspection.table_names()
            if 'django_q_schedule' not in tables:
                return
            from django_q.models import Schedule
            Schedule.objects.get_or_create(
                func='subscriptions.tasks.check_subscription_expiries_and_reminders',
                defaults={
                    'name': 'Daily Subscription Expiries and Reminders',
                    'schedule_type': Schedule.DAILY,
                    'repeats': -1,
                },
            )
        except Exception:
            pass
