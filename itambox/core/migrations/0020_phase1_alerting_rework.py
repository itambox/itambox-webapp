"""Phase 1 alerting rework:

- Drop dead NotificationTemplate model
- Update AlertRule.alert_type choices to include audit_overdue
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_alertrule_warranty_type_alertlog_severity_delivery'),
    ]

    operations = [
        # Drop the unused NotificationTemplate model
        migrations.DeleteModel(
            name='NotificationTemplate',
        ),

        # Extend alert_type choices to include audit_overdue
        migrations.AlterField(
            model_name='alertrule',
            name='alert_type',
            field=models.CharField(
                max_length=50,
                choices=[
                    ('low_stock', 'Low Stock Alert'),
                    ('upcoming_eol', 'Upcoming EOL Planning'),
                    ('license_expiry', 'License Expiry Alert'),
                    ('renewal_due', 'Renewal Due Alert'),
                    ('warranty_expiry', 'Warranty Expiry Alert'),
                    ('audit_overdue', 'Audit Overdue'),
                ],
            ),
        ),
    ]
