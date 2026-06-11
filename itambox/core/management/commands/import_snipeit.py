"""
management command: import_snipeit

Pulls data from a Snipe-IT instance via its REST API and imports it into ITAMbox.

    python manage.py import_snipeit \\
        --url https://snipe.example \\
        --token-env SNIPEIT_TOKEN \\
        [--tenant <slug>] \\
        [--map-companies-to-tenants] \\
        [--dry-run] \\
        [--skip assets,licenses,...] \\
        [--update]

Out of scope (v1): images/file uploads, activity history, depreciation schedules,
kits, Snipe "requestable" requests.
"""
import os
import sys

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

User = get_user_model()


class Command(BaseCommand):
    help = (
        "Import data from a Snipe-IT instance into ITAMbox.\n\n"
        "IMPORTANT: Run with --dry-run first to verify the mapping.\n\n"
        "Out of scope (v1): images, activity history, depreciation schedules, kits."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--url',
            required=True,
            metavar='URL',
            help='Snipe-IT base URL, e.g. https://snipe.example (no trailing slash).',
        )
        parser.add_argument(
            '--token-env',
            required=True,
            metavar='ENV_VAR',
            help='Name of the environment variable that holds the Snipe-IT API token.',
        )
        parser.add_argument(
            '--tenant',
            metavar='SLUG',
            default=None,
            help='Target ITAMbox tenant slug. Required unless --map-companies-to-tenants is set.',
        )
        parser.add_argument(
            '--map-companies-to-tenants',
            action='store_true',
            default=False,
            help='Create/use one ITAMbox tenant per Snipe-IT company (MSP mode).',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            default=False,
            help='Fetch and report the mapping without writing anything to the database.',
        )
        parser.add_argument(
            '--skip',
            metavar='ENTITIES',
            default='',
            help='Comma-separated list of entity types to skip: assets,accessories,consumables,components,licenses,maintenances',
        )
        parser.add_argument(
            '--update',
            action='store_true',
            default=False,
            help='Re-sync fields on existing records (default: skip existing).',
        )
        parser.add_argument(
            '--admin-user',
            metavar='USERNAME',
            default=None,
            help='Username of the ITAMbox admin who owns the import job/changes (default: first superuser).',
        )

    def handle(self, *args, **options):
        from core.importers.snipeit import SnipeITClient, SnipeITImporter, SnipeITError
        from core.models import Job
        from organization.models import Tenant

        # Resolve API token from environment
        token_env = options['token_env']
        token = os.environ.get(token_env, '').strip()
        if not token:
            raise CommandError(
                f"Environment variable '{token_env}' is empty or not set. "
                "Export it before running: export SNIPEIT_TOKEN=<your-token>"
            )

        base_url = options['url'].rstrip('/')
        map_companies = options['map_companies_to_tenants']
        dry_run = options['dry_run']
        update = options['update']
        skip = {s.strip() for s in options['skip'].split(',') if s.strip()}

        # Resolve tenant
        tenant = None
        if options['tenant']:
            try:
                tenant = Tenant.objects.get(slug=options['tenant'])
            except Tenant.DoesNotExist:
                raise CommandError(f"Tenant with slug '{options['tenant']}' not found.")
        elif not map_companies:
            raise CommandError(
                "Either --tenant <slug> or --map-companies-to-tenants is required."
            )

        # Resolve admin user
        admin_username = options.get('admin_user')
        if admin_username:
            try:
                user = User.objects.get(username=admin_username)
            except User.DoesNotExist:
                raise CommandError(f"User '{admin_username}' not found.")
        else:
            user = User.objects.filter(is_superuser=True).order_by('pk').first()
            if not user:
                raise CommandError(
                    "No superuser found. Create one first or pass --admin-user."
                )

        if dry_run:
            self.stdout.write(self.style.WARNING(
                "\n[DRY RUN] No data will be written to the database.\n"
            ))

        # Create Job record for visibility in the Jobs UI
        job_name = f"Snipe-IT Import from {base_url}"
        if dry_run:
            job_name += ' (dry-run)'
        if not dry_run:
            from django.contrib.contenttypes.models import ContentType
            job = Job.objects.create(
                name=job_name,
                status=Job.STATUS_PENDING,
                data={
                    'source': 'snipeit',
                    'url': base_url,
                    'tenant': tenant.slug if tenant else None,
                    'map_companies': map_companies,
                    'skip': list(skip),
                    'update': update,
                    'started_by': user.username,
                },
            )
            job.mark_running()
        else:
            job = None

        started = timezone.now()
        self.stdout.write(f"Importing from {base_url} …")
        if tenant:
            self.stdout.write(f"  Target tenant: {tenant.name}")
        if map_companies:
            self.stdout.write("  Companies → Tenants: ON")
        if skip:
            self.stdout.write(f"  Skipping: {', '.join(sorted(skip))}")

        client = SnipeITClient(base_url=base_url, token=token)

        # Verify connectivity
        try:
            client.get_detail('/api/v1/statuslabels?limit=1&offset=0')
        except SnipeITError as exc:
            msg = f"Cannot connect to Snipe-IT at {base_url}: {exc}"
            if job:
                job.mark_failed(msg)
            raise CommandError(msg)

        from core.tasks.context import TaskContext

        with TaskContext(
            tenant_id=tenant.pk if tenant else None,
            user_id=user.pk,
        ):
            importer = SnipeITImporter(
                client=client,
                tenant=tenant,
                user=user,
                dry_run=dry_run,
                update=update,
                map_companies=map_companies,
                skip=skip,
                job=job,
                stdout=self.stdout,
            )
            try:
                counts = importer.run()
            except SnipeITError as exc:
                msg = f"Import aborted: {exc}"
                self.stdout.write(self.style.ERROR(msg))
                if job:
                    job.mark_failed(msg)
                sys.exit(1)

        elapsed = (timezone.now() - started).total_seconds()

        # Summary
        self.stdout.write('\n' + self.style.SUCCESS('=' * 60))
        self.stdout.write(self.style.SUCCESS(
            'DRY RUN complete' if dry_run else 'Import complete'
        ))
        self.stdout.write(f"Elapsed: {elapsed:.1f}s\n")

        total_created = total_updated = total_failed = 0
        for entity, stats in counts.items():
            self.stdout.write(
                f"  {entity:20s}  "
                f"created={stats['created']:4d}  "
                f"updated={stats['updated']:4d}  "
                f"skipped={stats['skipped']:4d}  "
                f"failed={stats['failed']:4d}"
            )
            total_created += stats['created']
            total_updated += stats['updated']
            total_failed += stats['failed']

        self.stdout.write(self.style.SUCCESS(
            f"\nTotal: {total_created} created, {total_updated} updated, {total_failed} failed"
        ))

        if job:
            job.mark_completed(result={
                'total_created': total_created,
                'total_updated': total_updated,
                'total_failed': total_failed,
                'elapsed_seconds': elapsed,
                'counts': counts,
            })
