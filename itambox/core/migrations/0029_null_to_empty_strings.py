from django.db import migrations, models


def normalize_nulls(apps, schema_editor):
    Notification = apps.get_model('core', 'Notification')
    Notification.objects.filter(target_url__isnull=True).update(target_url='')

    EmailSettings = apps.get_model('core', 'EmailSettings')
    EmailSettings.objects.filter(smtp_username__isnull=True).update(smtp_username='')
    EmailSettings.objects.filter(smtp_password__isnull=True).update(smtp_password='')
    EmailSettings.objects.filter(test_recipient__isnull=True).update(test_recipient='')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0028_encrypt_emailsettings_smtp_password'),
    ]

    operations = [
        migrations.RunPython(normalize_nulls, migrations.RunPython.noop),

        migrations.AlterField(
            model_name='notification',
            name='target_url',
            field=models.CharField(blank=True, help_text='Optional destination URL when clicked.', max_length=500),
        ),
        migrations.AlterField(
            model_name='emailsettings',
            name='smtp_username',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AlterField(
            model_name='emailsettings',
            name='smtp_password',
            field=models.CharField(blank=True, max_length=1000),
        ),
        migrations.AlterField(
            model_name='emailsettings',
            name='test_recipient',
            field=models.EmailField(blank=True, help_text='Email address for test notifications', max_length=255),
        ),
    ]
