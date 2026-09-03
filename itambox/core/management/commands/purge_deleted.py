from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.purge_handlers import purge_object
from core.tasks.context import TaskContext
from itambox.registry import registry


class Command(BaseCommand):
    help = "Permanently delete soft-deleted objects older than the specified number of days."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Delete objects that were soft-deleted more than this many days ago (default: 30)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            default=False,
            help="Show what would be purged without actually deleting anything",
        )

    def _process_model(self, model, cutoff, dry_run):
        if model._meta.abstract:
            return 0, 0
        manager = getattr(model, "all_objects", model._base_manager)
        queryset = manager.filter(deleted_at__lt=cutoff)
        count = queryset.count()
        if count == 0:
            return 0, 0
        if getattr(model, "preserve_tombstones", False):
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped {count} permanent {model._meta.verbose_name_plural} tombstone(s) "
                    f"(deleted before {cutoff.date()})"
                )
            )
            return 0, count
        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f"[DRY RUN] Would purge {count} {model._meta.verbose_name_plural} (deleted before {cutoff.date()})"
                )
            )
            return count, 0
        purged = 0
        for obj in queryset.iterator(chunk_size=500):
            purge_object(obj)
            purged += 1
        self.stdout.write(
            self.style.SUCCESS(f"Purged {purged} {model._meta.verbose_name_plural} (deleted before {cutoff.date()})")
        )
        return purged, 0

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]
        cutoff = timezone.now() - timedelta(days=days)

        # TaskContext sets _request_id and _current_user so that hard-deletes
        # are attributed/logged by ChangeLoggingMixin (permanent purges are
        # compliance-relevant events and must appear in the audit trail).
        # No tenant_id: all_objects is unscoped, and the purge intentionally
        # spans all tenants. No user_id: this is a system CLI command with no
        # actor user; add a --user argument if attributed purges are required.
        with TaskContext(tenant_id=None, user_id=None):
            models_with_soft_delete = registry.get_models_with_feature("soft_delete")
            total_purged = 0
            total_skipped = 0

            for model in models_with_soft_delete:
                purged, skipped = self._process_model(model, cutoff, dry_run)
                total_purged += purged
                total_skipped += skipped

            if dry_run:
                self.stdout.write(self.style.WARNING(f"[DRY RUN] Total objects that would be purged: {total_purged}"))
            elif total_purged == 0 and total_skipped == 0:
                self.stdout.write(self.style.SUCCESS("No soft-deleted objects to purge."))
            else:
                if total_purged:
                    self.stdout.write(self.style.SUCCESS(f"Total objects purged: {total_purged}"))
                if total_skipped:
                    self.stdout.write(self.style.WARNING(f"Total permanent tombstones skipped: {total_skipped}"))
