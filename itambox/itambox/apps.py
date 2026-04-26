from django.apps import AppConfig


class ITAMBoxConfig(AppConfig):
    name = 'itambox'
    verbose_name = 'ITAMbox System Framework'

    def ready(self):
        from itambox.registry import registry
        registry.execute_deferred()
