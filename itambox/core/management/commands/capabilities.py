"""Operator diagnostics for the capability registry.

Answers one question an operator actually asks -- "is this thing on, and what
would turn it on?" -- without ever answering "what is it set to". Each row
carries the declared class and mode, the state observed right now, the kind of
source that decides it, and whether a value is present. A probe that fails is
reported by exception type alone, so an unreachable database or a rejected
credential shows up without its message reaching a terminal or a log shipper.
"""

import json

from django.core.management.base import BaseCommand

from itambox.capabilities import registry

COLUMNS = (
    ("key", "CAPABILITY"),
    ("maturity", "CLASS"),
    ("activation", "MODE"),
    ("state", "STATE"),
    ("activation_source", "SOURCE"),
    ("value", "VALUE"),
)


class Command(BaseCommand):
    help = "Report declared capabilities, their activation mode, and their current state."

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            choices=("table", "json"),
            default="table",
            help="Output format (default: table).",
        )

    def handle(self, *args, **options):
        rows = registry.diagnostics()
        if options["format"] == "json":
            self.stdout.write(json.dumps(list(rows), indent=2, sort_keys=True))
            return
        self._write_table(rows)
        unresolved = registry.unresolved_references()
        if unresolved:
            self.stdout.write("")
            for row in unresolved:
                self.stdout.write(
                    self.style.WARNING(f"unresolved reference {row.reference} owned by {row.key} ({row.reason})")
                )

    def _write_table(self, rows):
        rendered = [self._render(row) for row in rows]
        widths = {
            field: max(len(heading), *(len(row[field]) for row in rendered)) if rendered else len(heading)
            for field, heading in COLUMNS
        }
        self.stdout.write("  ".join(heading.ljust(widths[field]) for field, heading in COLUMNS).rstrip())
        self.stdout.write("  ".join("-" * widths[field] for field, _ in COLUMNS))
        for row in rendered:
            self.stdout.write("  ".join(row[field].ljust(widths[field]) for field, _ in COLUMNS).rstrip())

    def _render(self, row):
        """One printable row. Every cell is derived, never probe-supplied."""
        if row["probe_error"]:
            state = f"error ({row['probe_error']})"
        elif row["active"]:
            state = "active"
        else:
            state = "inactive"
        return {
            "key": row["key"] + (" *" if row["security_critical"] else ""),
            "maturity": row["maturity"],
            "activation": row["activation"],
            "state": state,
            "activation_source": row["activation_source"],
            "value": "present" if row["value_present"] else "absent",
        }
