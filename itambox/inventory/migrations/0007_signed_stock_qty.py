from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0006_accessory_custom_field_data_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='accessorystock',
            name='qty',
            field=models.IntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='componentstock',
            name='qty',
            field=models.IntegerField(default=0),
        ),
        migrations.AlterField(
            model_name='consumablestock',
            name='qty',
            field=models.IntegerField(default=0),
        ),
    ]
