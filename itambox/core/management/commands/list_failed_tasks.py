from django.core.management.base import BaseCommand

from django_q.models import Failure


class Command(BaseCommand):
    help = 'List recent failed django-q2 background tasks with their tracebacks.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=20,
            help='Maximum number of failed tasks to show, most recent first (default: 20)',
        )

    def handle(self, *args, **options):
        limit = options['limit']

        # Failure is a proxy on the Task table filtered to success=False.
        # Order by stop time (when the worker recorded the failure), falling
        # back to start time for records that never reached stopped.
        failures = Failure.objects.order_by('-stopped', '-started')[:limit]

        if not failures:
            self.stdout.write(self.style.SUCCESS('No failed tasks recorded.'))
            return

        for task in failures:
            self.stdout.write(self.style.ERROR(f'Task {task.id} — {task.name}'))
            self.stdout.write(f'  func:    {task.func}')
            self.stdout.write(f'  started: {task.started}')
            self.stdout.write(f'  stopped: {task.stopped}')
            self.stdout.write(f'  attempts: {task.attempt_count}')
            self.stdout.write('  result/traceback:')
            self.stdout.write(f'    {task.result}')
            self.stdout.write('')

        self.stdout.write(
            self.style.SUCCESS(f'Listed {len(failures)} failed task(s).')
        )
