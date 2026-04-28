from django.apps import AppConfig


class SubscriptionsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'subscriptions'
    verbose_name = 'Subscriptions'

    def ready(self):
        import subscriptions.signals  # noqa
        import subscriptions.search  # noqa
