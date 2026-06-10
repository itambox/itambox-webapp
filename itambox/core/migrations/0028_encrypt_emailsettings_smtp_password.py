from django.db import migrations, models


def encrypt_existing_passwords(apps, schema_editor):
    EmailSettings = apps.get_model('core', 'EmailSettings')
    from core.crypto import encrypt_string
    for obj in EmailSettings.objects.all():
        if obj.smtp_password and not obj.smtp_password.startswith('enc$'):
            obj.smtp_password = encrypt_string(obj.smtp_password)
            EmailSettings.objects.filter(pk=obj.pk).update(smtp_password=obj.smtp_password)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0027_remove_notificationchannel_alertrule_alertlog'),
    ]

    operations = [
        migrations.AlterField(
            model_name='emailsettings',
            name='smtp_password',
            field=models.CharField(blank=True, max_length=1000, null=True),
        ),
        migrations.RunPython(encrypt_existing_passwords, migrations.RunPython.noop),
    ]
