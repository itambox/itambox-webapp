import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """State-only: all differences are help_text/choices/db_index metadata.
    The DB columns and indexes already exist from the original core migrations;
    no schema changes are needed here."""

    dependencies = [
        ('django_q', '0019_alter_task_options_alter_ormq_key_alter_ormq_lock_and_more'),
        ('extras', '0018_repoint_report_contenttypes'),
        ('organization', '0013_assetholder_custom_field_data_and_more'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name='reporttemplate',
                    name='advanced_mode',
                    field=models.BooleanField(default=False, help_text='Enable custom Jinja2/HTML template code override.'),
                ),
                migrations.AlterField(
                    model_name='reporttemplate',
                    name='deleted_at',
                    field=models.DateTimeField(blank=True, db_index=True, editable=False, null=True),
                ),
                migrations.AlterField(
                    model_name='reporttemplate',
                    name='filter_tenants',
                    field=models.ManyToManyField(blank=True, help_text='Filter compiled data to only include these selected tenants. If none are selected, aggregates data globally.', related_name='filtered_templates', to='organization.tenant'),
                ),
                migrations.AlterField(
                    model_name='reporttemplate',
                    name='group_by_field',
                    field=models.CharField(blank=True, help_text='Optional column key to group grid records under (e.g. location, status).', max_length=100, null=True),
                ),
                migrations.AlterField(
                    model_name='reporttemplate',
                    name='include_distribution_chart',
                    field=models.BooleanField(default=False, help_text='Toggle embedding spend or status distribution charts in the HTML report.'),
                ),
                migrations.AlterField(
                    model_name='reporttemplate',
                    name='include_summary_cards',
                    field=models.BooleanField(default=True, help_text='Toggle displaying top card widgets (totals, counts, financial sums).'),
                ),
                migrations.AlterField(
                    model_name='reporttemplate',
                    name='included_columns',
                    field=models.JSONField(blank=True, default=list, help_text='Checked columns to render in the report data grid.'),
                ),
                migrations.AlterField(
                    model_name='reporttemplate',
                    name='style_preset',
                    field=models.CharField(choices=[('default', 'Professional Layout'), ('compact', 'Compact Audit Sheet'), ('financial', 'Financial Spend Summary')], default='default', max_length=50),
                ),
                migrations.AlterField(
                    model_name='reporttemplate',
                    name='template_content',
                    field=models.TextField(blank=True, help_text='Optional Jinja2 custom HTML override template'),
                ),
                migrations.AlterField(
                    model_name='reporttemplate',
                    name='tenant',
                    field=models.ForeignKey(blank=True, help_text='The tenant owning this report template. Null represents system-wide templates.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='report_templates', to='organization.tenant'),
                ),
                migrations.AlterField(
                    model_name='scheduledreport',
                    name='cron_expression',
                    field=models.CharField(blank=True, help_text="Custom Cron Expression (e.g. '0 8 * * 1-5')", max_length=100, null=True),
                ),
                migrations.AlterField(
                    model_name='scheduledreport',
                    name='filter_tenants',
                    field=models.ManyToManyField(blank=True, help_text='Filter compiled data to only include these selected tenants. If none are selected, aggregates data globally.', related_name='filtered_schedules', to='organization.tenant'),
                ),
                migrations.AlterField(
                    model_name='scheduledreport',
                    name='recipients',
                    field=models.TextField(blank=True, default='', help_text='Comma-separated email addresses'),
                ),
                migrations.AlterField(
                    model_name='scheduledreport',
                    name='save_to_archive',
                    field=models.BooleanField(default=True, help_text='Store a copy of generated reports in the local file archive'),
                ),
                migrations.AlterField(
                    model_name='scheduledreport',
                    name='schedule',
                    field=models.ForeignKey(blank=True, help_text='Linked Django-Q Schedule', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='scheduled_reports', to='django_q.schedule'),
                ),
                migrations.AlterField(
                    model_name='scheduledreport',
                    name='start_time',
                    field=models.TimeField(blank=True, help_text='Time of day to run the schedule (e.g. 08:00:00)', null=True),
                ),
                migrations.AlterField(
                    model_name='scheduledreport',
                    name='tenant',
                    field=models.ForeignKey(blank=True, help_text='The tenant owning this scheduled report. Null represents system-wide schedules.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='scheduled_reports', to='organization.tenant'),
                ),
            ],
            database_operations=[],
        ),
    ]
