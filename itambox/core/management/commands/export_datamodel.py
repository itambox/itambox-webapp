"""Export the installed ITAMbox domain model as a Graphviz DOT graph."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from django.apps import apps
from django.core.management.base import BaseCommand


DOMAIN_APPS = (
    "assets",
    "compliance",
    "core",
    "extras",
    "inventory",
    "licenses",
    "organization",
    "procurement",
    "software",
    "subscriptions",
    "users",
)

APP_COLORS = {
    "assets": "#E8F1FB",
    "compliance": "#FCE9E4",
    "core": "#F1F3F5",
    "extras": "#F8F1E2",
    "inventory": "#E7F4EC",
    "licenses": "#F2E9F9",
    "organization": "#E2F4F3",
    "procurement": "#FBE8EF",
    "software": "#E9EEF9",
    "subscriptions": "#F3F0E1",
    "users": "#EDEDED",
}

CROSS_CUTTING_TARGETS = {
    "extras.Tag",
    "organization.Tenant",
    "users.User",
}


def _quote(value: str) -> str:
    """Return a DOT-safe double-quoted string."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Command(BaseCommand):
    help = "Export the ITAMbox domain models and their direct relations as Graphviz DOT."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apps",
            nargs="+",
            choices=DOMAIN_APPS,
            help="Limit the graph to one or more domain apps.",
        )
        parser.add_argument(
            "--output",
            type=Path,
            help="Write DOT to this file instead of standard output.",
        )
        parser.add_argument(
            "--hide-cross-cutting",
            action="store_true",
            help="Omit ubiquitous tenant, user, and tag relations for a business-domain overview.",
        )

    def handle(self, *args, **options):
        selected_apps = tuple(options["apps"] or DOMAIN_APPS)
        models = sorted(
            (
                model
                for model in apps.get_models(include_auto_created=False)
                if not model._meta.abstract and model._meta.app_label in selected_apps
            ),
            key=lambda model: (model._meta.app_label, model._meta.object_name),
        )
        model_labels = {model._meta.label for model in models}
        models_by_app = defaultdict(list)
        for model in models:
            models_by_app[model._meta.app_label].append(model)

        lines = [
            "digraph itambox_data_model {",
            '  graph [bgcolor="#FFFFFF", compound=true, fontname="Arial", newrank=true, nodesep=0.4, pad=0.25, rankdir=LR, ranksep=1.2, splines=polyline];',
            '  node [color="#52606D", fontname="Arial", fontsize=11, margin="0.14,0.08", shape=box, style="rounded,filled"];',
            '  edge [color="#667085", fontcolor="#475467", fontname="Arial", fontsize=8, penwidth=1.0];',
            "",
        ]

        for app_label in selected_apps:
            app_models = models_by_app.get(app_label)
            if not app_models:
                continue
            lines.append(f"  subgraph cluster_{app_label} {{")
            lines.append(f"    label={_quote(app_label)};")
            lines.append('    color="#98A2B3";')
            lines.append('    fontcolor="#344054";')
            lines.append('    fontname="Arial Bold";')
            lines.append('    fontsize=13;')
            lines.append('    penwidth=1.2;')
            lines.append('    style="rounded";')
            for model in app_models:
                lines.append(
                    f"    {_quote(model._meta.label)} [fillcolor={_quote(APP_COLORS[app_label])}, "
                    f"label={_quote(model._meta.object_name)}];"
                )
            lines.append("  }")
            lines.append("")

        for model in models:
            for field in model._meta.get_fields():
                if field.auto_created or not field.is_relation or not field.concrete:
                    continue
                target = field.related_model
                if target is None or target._meta.label not in model_labels:
                    continue
                if options["hide_cross_cutting"] and target._meta.label in CROSS_CUTTING_TARGETS:
                    continue

                optional = field.blank if field.many_to_many else field.null
                required = "optional" if optional else "required"
                if field.many_to_many:
                    relation_type = "many-to-many"
                    edge_attributes = 'arrowhead="normal", arrowtail="normal", dir="both", style="dashed"'
                elif field.one_to_one:
                    relation_type = "one-to-one"
                    edge_attributes = 'arrowhead="normal", arrowtail="normal", dir="both", penwidth=1.4'
                else:
                    relation_type = "foreign key"
                    edge_attributes = 'arrowhead="normal"'

                label = f"{field.name} ({required}, {relation_type})"
                lines.append(
                    f"  {_quote(model._meta.label)} -> {_quote(target._meta.label)} "
                    f"[label={_quote(label)}, tooltip={_quote(label)}, {edge_attributes}];"
                )

        lines.append("}")
        dot = "\n".join(lines) + "\n"
        output_path = options.get("output")
        if output_path:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(dot, encoding="utf-8")
            self.stdout.write(self.style.SUCCESS(f"Wrote {output_path}"))
            return
        self.stdout.write(dot, ending="")
