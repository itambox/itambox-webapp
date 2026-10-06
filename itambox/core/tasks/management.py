from django.core.management.base import BaseCommand

from core.context import get_current_request_id
from core.tasks.context import TaskContext


class SystemTaskCommand(BaseCommand):
    """Run a state-changing management command under an execution context.

    A command invoked with no active context (direct CLI use, the scheduler)
    enters its own actorless system ``TaskContext`` so its writes are audited
    and attributed to the command. When a request or task context is already
    active, the command runs inside that context: a second, actorless scope
    would shadow the caller's actor for the command's body (the nested-command
    contract pinned by
    ``test_nested_command_context_preserves_outer_task_actor``).
    """

    def execute(self, *args, **options):
        if get_current_request_id() is not None:
            return super().execute(*args, **options)
        command_name = self.__module__.rsplit(".", 1)[-1]
        with TaskContext(operation=f"management_command.{command_name}"):
            return super().execute(*args, **options)
