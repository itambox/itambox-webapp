"""Operator diagnostics for configured Experimental plugins."""

import json

from django.conf import settings
from django.core.management.base import BaseCommand

from itambox.plugins.utils import get_plugin_diagnostics


class Command(BaseCommand):
    help = "Report configured plugin activation and isolated startup failures."

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            choices=("table", "json"),
            default="table",
            help="Output format (default: table).",
        )

    def handle(self, *args, **options):
        active = tuple(getattr(settings, "PLUGINS_ACTIVE", ()))
        diagnostics = get_plugin_diagnostics()
        if options["format"] == "json":
            self.stdout.write(
                json.dumps({"active": active, "diagnostics": list(diagnostics)}, indent=2, sort_keys=True)
            )
            return

        self.stdout.write("PLUGIN  STATE   SOURCE           DETAILS")
        self.stdout.write("------  ------  ---------------  -------")
        for plugin_name in active:
            self.stdout.write(f"{plugin_name}  active  settings.PLUGINS  loaded")
        for diagnostic in diagnostics:
            self.stdout.write(
                f"{diagnostic['plugin']}  disabled  {diagnostic['source']}  "
                f"{diagnostic['failure_class']} at {diagnostic['stage']} ({diagnostic['compatibility']})"
            )
        if not active and not diagnostics:
            self.stdout.write("(none configured)")
