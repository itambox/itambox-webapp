from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0100_issue88_shard_62_users_relations"),
        ("assets", "0124_tenant_archive_leaf_markers"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="assetassignment",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    ("is_loan", True),
                    ("is_active", True),
                    ("maintenance__isnull", False),
                    ("deleted_at__isnull", True),
                ),
                fields=("maintenance",),
                name="unique_open_loan_per_maintenance",
            ),
        ),
    ]
