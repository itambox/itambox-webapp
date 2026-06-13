import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organization', '0001_initial'),
        ('core', '0029_null_to_empty_strings'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='tenant',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='jobs',
                to='organization.tenant',
            ),
        ),
    ]
