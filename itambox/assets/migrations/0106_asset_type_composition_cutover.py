"""Normalized #479 composition cutover.

Drops the singular legacy ``AssetType.custom_fieldset`` column (superseded by
the ordered ``custom_fieldsets`` composition) and the conditional manufacturer
identity constraint that the final model no longer carries.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0105_asset_type_provenance_capture"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="assettype",
            name="unique_manufacturer_model_active",
        ),
        migrations.RemoveField(
            model_name="assettype",
            name="custom_fieldset",
        ),
    ]
