from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.registry import registry


class Command(BaseCommand):
    help = 'Permanently delete soft-deleted objects older than the specified number of days.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Delete objects that were soft-deleted more than this many days ago (default: 30)',
        )

    def handle(self, *args, **options):
        days = options['days']
        cutoff = timezone.now() - timedelta(days=days)

        models_with_soft_delete = registry.get_models_with_feature('soft_delete')
        total_purged = 0

        for model in models_with_soft_delete:
            queryset = model.all_objects.filter(deleted_at__lt=cutoff)
            count = queryset.count()
            if count > 0:
                queryset.delete()
                total_purged += count
                self.stdout.write(
                    self.style.SUCCESS(
                        f'Purged {count} {model._meta.verbose_name_plural} '
                        f'(deleted before {cutoff.date()})'
                    )
                )

        if total_purged == 0:
            self.stdout.write(self.style.SUCCESS('No soft-deleted objects to purge.'))
        else:
            self.stdout.write(
                self.style.SUCCESS(f'Total objects purged: {total_purged}')
            )
