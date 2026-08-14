from django.db import migrations, models, router
from django.db.migrations.operations.base import Operation


class AddPersistentReportDesignerFields(Operation):
    """Restore the durable 1.x fields while preserving their physical columns."""

    reduces_to_sql = False
    reversible = True

    def __init__(self):
        self.fields = (
            (
                "advanced_mode",
                models.BooleanField(
                    default=False,
                    help_text="Use the legacy summary CSV shape; this does not enable custom HTML execution.",
                    verbose_name="Legacy CSV Shape",
                ),
            ),
            (
                "legacy_designer_grandfathered",
                models.BooleanField(
                    default=False,
                    editable=False,
                    help_text="Migration-managed marker for bounded legacy scheduled templates.",
                    verbose_name="Legacy Designer Grandfathered",
                ),
            ),
            (
                "template_content",
                models.TextField(
                    blank=True,
                    help_text="Optional sandboxed Jinja2 custom HTML template.",
                    verbose_name="Custom HTML Template",
                ),
            ),
        )

    def state_forwards(self, app_label, state):
        for name, field in self.fields:
            state.add_field(app_label, "reporttemplate", name, field.clone(), preserve_default=True)

    def state_backwards(self, app_label, state):
        # 0103 intentionally keeps the legacy columns out of historical ORM
        # state while retaining their data in the database. Reverse the state
        # exactly to that contract; the physical columns remain durable.
        for name, _field in reversed(self.fields):
            state.remove_field(app_label, "reporttemplate", name)

    @staticmethod
    def _ensure_columns(schema_editor, model):
        quote = schema_editor.connection.ops.quote_name
        table = quote(model._meta.db_table)
        definitions = {
            "advanced_mode": "boolean DEFAULT FALSE",
            "legacy_designer_grandfathered": "boolean DEFAULT FALSE",
            "template_content": "text DEFAULT ''",
        }
        for name, sql_type in definitions.items():
            column = quote(name)
            schema_editor.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {sql_type}")
            default = "FALSE" if sql_type.startswith("boolean") else "''"
            schema_editor.execute(f"UPDATE {table} SET {column} = {default} WHERE {column} IS NULL")
            schema_editor.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT {default}")
            schema_editor.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL")

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        model = to_state.apps.get_model(app_label, "ReportTemplate")
        if router.allow_migrate_model(schema_editor.connection.alias, model):
            self._ensure_columns(schema_editor, model)

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        # The release policy forbids deleting user-authored compatibility data
        # during a 1.x reversal. Reconcile the physical schema explicitly so a
        # database upgraded from the old 0103 is also safe to reverse/forward.
        model = from_state.apps.get_model(app_label, "ReportTemplate")
        if router.allow_migrate_model(schema_editor.connection.alias, model):
            self._ensure_columns(schema_editor, model)

    def describe(self):
        return "Add persistent report-designer compatibility fields"

    @property
    def migration_name_fragment(self):
        return "add_persistent_report_designer_fields"


def recover_and_stamp_report_designer(apps, schema_editor):
    """Recover legacy values when changelog history still contains them.

    The predecessor migration removed the two fields. This forward recovery is
    deliberately conservative: it only fills the new default values from the
    latest serialized ReportTemplate change and only stamps the approved live
    schedule + non-empty-content set. Re-running it cannot broaden the set or
    overwrite operator edits.
    """
    ReportTemplate = apps.get_model("extras", "ReportTemplate")
    ScheduledReport = apps.get_model("extras", "ScheduledReport")
    ObjectChange = apps.get_model("core", "ObjectChange")

    changes = ObjectChange._base_manager.filter(
        changed_object_type__app_label="extras",
        changed_object_type__model="reporttemplate",
    ).order_by("changed_object_id", "-time", "-pk")
    latest = {}
    for change in changes.iterator():
        if change.changed_object_id in latest:
            continue
        data = change.postchange_data or {}
        if "advanced_mode" in data or "template_content" in data:
            latest[change.changed_object_id] = data

    for template in ReportTemplate._base_manager.all().iterator():
        data = latest.get(template.pk, {})
        updates = []
        if not template.advanced_mode and isinstance(data.get("advanced_mode"), bool):
            template.advanced_mode = data["advanced_mode"]
            updates.append("advanced_mode")
        if not template.template_content and isinstance(data.get("template_content"), str):
            template.template_content = data["template_content"]
            updates.append("template_content")
        if updates:
            ReportTemplate._base_manager.filter(pk=template.pk).update(
                **{field: getattr(template, field) for field in updates}
            )

    live_report_ids = set(
        ScheduledReport._base_manager.filter(is_active=True).values_list("report_id", flat=True).distinct()
    )
    ReportTemplate._base_manager.filter(
        pk__in=live_report_ids,
        deleted_at__isnull=True,
        template_content__regex=r"\S",
        legacy_designer_grandfathered=False,
    ).update(legacy_designer_grandfathered=True)


def clear_grandfathering_marker(apps, schema_editor):
    """Reverse only the activation effect; never rewrite user content."""
    ReportTemplate = apps.get_model("extras", "ReportTemplate")
    ReportTemplate._base_manager.filter(legacy_designer_grandfathered=True).update(legacy_designer_grandfathered=False)


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0029_null_to_empty_strings"),
        ("extras", "0103_remove_reporttemplate_advanced_mode_and_more"),
    ]

    operations = [
        AddPersistentReportDesignerFields(),
        migrations.RunPython(recover_and_stamp_report_designer, clear_grandfathering_marker),
    ]
