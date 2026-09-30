"""Retire the legacy report-designer CSV shape and provenance marker.

Templates with the default canonical CSV shape and templates whose only
special state was the grandfathering marker need no data rewrite. A live
template with ``advanced_mode=True`` cannot be converted without changing its
rendered CSV output, so the migration refuses before changing the schema and
lists every affected non-deleted template. On the pre-upgrade deployment,
resolve each listed template by clearing ``advanced_mode`` through the shell
or admin; after upgrade, canonical CSV columns apply to all templates.

Soft-deleted rows are out of scope for refusal and lose the retired columns
with the rest of the table. The reverse is explicitly refused because the
dropped shape and provenance data cannot be reconstructed; rollback is
restore-first from a pre-upgrade backup.
"""

from django.db import migrations

ERROR_PREFIX = "issue586.report_designer_legacy"


def _fail(code, details=""):
    message = f"{ERROR_PREFIX}.{code}"
    if details:
        message = f"{message}: {details}"
    raise RuntimeError(message)


def refuse_unsupported_legacy_csv_shape(apps, schema_editor):
    ReportTemplate = apps.get_model("extras", "ReportTemplate")
    db_alias = schema_editor.connection.alias
    affected = list(
        ReportTemplate._base_manager.using(db_alias)
        .filter(advanced_mode=True, deleted_at__isnull=True)
        .order_by("name", "pk")
        .values_list("name", "pk")
    )
    if affected:
        records = ", ".join(f"{name} (pk={pk})" for name, pk in affected)
        _fail("legacy_csv_shape_unsupported", records)


def refuse_reverse(apps, schema_editor):
    _fail("reverse_refused")


class Migration(migrations.Migration):
    dependencies = [
        ("extras", "0126_scheduledreport_last_run_archive"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.RunPython(refuse_unsupported_legacy_csv_shape, refuse_reverse),
        migrations.RemoveField(model_name="reporttemplate", name="advanced_mode"),
        migrations.RemoveField(model_name="reporttemplate", name="legacy_designer_grandfathered"),
    ]
