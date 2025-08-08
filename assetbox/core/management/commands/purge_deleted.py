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
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Show what would be purged without actually deleting anything',
        )

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']
        cutoff = timezone.now() - timedelta(days=days)

        models_with_soft_delete = registry.get_models_with_feature('soft_delete')
        total_purged = 0

        for model in models_with_soft_delete:
            queryset = model.all_objects.filter(deleted_at__lt=cutoff)
            count = queryset.count()
            if count == 0:
                continue

            if dry_run:
                self.stdout.write(
                    self.style.WARNING(
                        f'[DRY RUN] Would purge {count} {model._meta.verbose_name_plural} '
                        f'(deleted before {cutoff.date()})'
                    )
                )
                total_purged += count
                continue

            purged = 0
            for obj in queryset.iterator(chunk_size=500):
                obj.delete(force_hard_delete=True)
                purged += 1

            total_purged += purged
            self.stdout.write(
                self.style.SUCCESS(
                    f'Purged {purged} {model._meta.verbose_name_plural} '
                    f'(deleted before {cutoff.date()})'
                )
            )

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'[DRY RUN] Total objects that would be purged: {total_purged}'
                )
            )
        elif total_purged == 0:
            self.stdout.write(self.style.SUCCESS('No soft-deleted objects to purge.'))
        else:
            self.stdout.write(
                self.style.SUCCESS(f'Total objects purged: {total_purged}')
            )
