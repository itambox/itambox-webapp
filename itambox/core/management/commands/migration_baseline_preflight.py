"""Check whether the database has recognized the shipped migration transition."""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import Error, connections
from django.db.migrations.recorder import MigrationRecorder
from django.db.utils import ConnectionDoesNotExist

from core.migration_preflight import (
    classify_applied_migrations,
    format_table,
    load_manifest,
    manifest_invalid_result,
    recorder_unavailable_result,
)


class Command(BaseCommand):
    help = "Read-only, fail-closed migration baseline recognition preflight."

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default", help="Django database alias (default: default)")
        parser.add_argument(
            "--format",
            choices=("table", "json"),
            default="table",
            help="Output format (default: table)",
        )

    def handle(self, *args, **options):
        try:
            manifest = load_manifest()
        except ValueError:
            result = manifest_invalid_result()
        else:
            try:
                connection = connections[options["database"]]
                recorder = MigrationRecorder(connection)
                if not recorder.has_table():
                    result = recorder_unavailable_result(missing_table=True)
                else:
                    applied_ids = {
                        f"{app_label}.{migration_name}" for app_label, migration_name in recorder.applied_migrations()
                    }
                    result = classify_applied_migrations(applied_ids, manifest)
            except (ConnectionDoesNotExist, Error, ValueError):
                result = recorder_unavailable_result()

        if options["format"] == "json":
            self.stdout.write(json.dumps(result.as_dict(), indent=2, sort_keys=True))
        else:
            self.stdout.write(format_table(result))
        if result.exit_code:
            raise CommandError(f"{result.reason_code}: {result.remediation}")
        return None
