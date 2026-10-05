from django.core.management.base import BaseCommand

from core.tasks.context import TaskContext


class SystemTaskCommand(BaseCommand):
    """Run a state-changing management command as an actorless system task."""

    def execute(self, *args, **options):
        command_name = self.__module__.rsplit(".", 1)[-1]
        with TaskContext(operation=f"management_command.{command_name}"):
            return super().execute(*args, **options)
