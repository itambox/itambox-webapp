import core.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contenttypes', '0002_remove_content_type_name'),
        ('core', '0023_remove_event_eventrule_webhookendpoint'),
        ('extras', '0012_repoint_event_contenttypes'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name='LabelTemplate',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('name', models.CharField(max_length=255, unique=True)),
                        ('description', models.TextField(blank=True)),
                        ('page_width', models.FloatField(default=2.25, help_text='Label width in inches')),
                        ('page_height', models.FloatField(default=1.25, help_text='Label height in inches')),
                        ('barcode_format', models.CharField(choices=[('code128', 'Code 128'), ('code39', 'Code 39'), ('qr', 'QR Code'), ('datamatrix', 'Data Matrix')], default='code128', max_length=20)),
                        ('template_code', models.TextField(blank=True, help_text='Jinja2/HTML template for label layout')),
                        ('printer_settings', models.JSONField(blank=True, default=dict)),
                    ],
                    options={
                        'verbose_name': 'Label Template',
                        'verbose_name_plural': 'Label Templates',
                        'ordering': ['name'],
                    },
                    bases=(core.models.ChangeLoggingMixin, models.Model),
                ),
                migrations.CreateModel(
                    name='ExportTemplate',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('created_at', models.DateTimeField(auto_now_add=True)),
                        ('updated_at', models.DateTimeField(auto_now=True)),
                        ('name', models.CharField(max_length=255)),
                        ('description', models.TextField(blank=True)),
                        ('template_code', models.TextField(help_text='Jinja2 or Django template code for export')),
                        ('mime_type', models.CharField(default='text/csv', help_text='MIME type for the exported file', max_length=50)),
                        ('file_extension', models.CharField(default='csv', max_length=10)),
                        ('content_type', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='export_templates', to='contenttypes.contenttype')),
                    ],
                    options={
                        'verbose_name': 'Export Template',
                        'verbose_name_plural': 'Export Templates',
                        'ordering': ['content_type', 'name'],
                    },
                ),
                migrations.AddConstraint(
                    model_name='exporttemplate',
                    constraint=models.UniqueConstraint(fields=('content_type', 'name'), name='core_exporttemplate_unique_content_type_name'),
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    'ALTER TABLE core_labeltemplate RENAME TO extras_labeltemplate',
                    reverse_sql='ALTER TABLE extras_labeltemplate RENAME TO core_labeltemplate',
                ),
                migrations.RunSQL(
                    'ALTER TABLE core_exporttemplate RENAME TO extras_exporttemplate',
                    reverse_sql='ALTER TABLE extras_exporttemplate RENAME TO core_exporttemplate',
                ),
            ],
        ),
    ]
