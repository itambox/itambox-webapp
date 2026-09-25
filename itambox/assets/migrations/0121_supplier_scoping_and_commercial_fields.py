# Hand-written for the Supplier vendor consolidation.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0120_repair_episode"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="portal_url",
            field=models.URLField(
                blank=True,
                help_text="URL for the supplier's management/administration portal",
                verbose_name="Admin Portal URL",
            ),
        ),
        migrations.AddField(
            model_name="supplier",
            name="account_id",
            field=models.CharField(
                blank=True,
                help_text="Optional customer account number with the supplier",
                max_length=100,
                verbose_name="Account ID",
            ),
        ),
        migrations.AddField(
            model_name="supplier",
            name="is_active",
            field=models.BooleanField(
                db_index=True,
                default=True,
                help_text="Deactivate to hide from selection lists without deleting",
                verbose_name="Active",
            ),
        ),
        migrations.AddField(
            model_name="supplier",
            name="tenant",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                help_text="The tenant owning this supplier. Null represents system-wide/global suppliers.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="suppliers",
                to="organization.tenant",
                verbose_name="Tenant",
            ),
        ),
        migrations.AddField(
            model_name="supplier",
            name="tenant_group",
            field=models.ForeignKey(
                blank=True,
                db_index=True,
                help_text="The tenant group owning this supplier.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="suppliers",
                to="organization.tenantgroup",
                verbose_name="Tenant Group",
            ),
        ),
        migrations.RemoveConstraint(model_name="supplier", name="unique_supplier_name_active"),
        migrations.RemoveConstraint(model_name="supplier", name="unique_supplier_slug_active"),
        migrations.AddConstraint(
            model_name="supplier",
            constraint=models.CheckConstraint(
                check=models.Q(("tenant__isnull", True)) | models.Q(("tenant_group__isnull", True)),
                name="supplier_tenant_or_group",
            ),
        ),
        migrations.AddConstraint(
            model_name="supplier",
            constraint=models.UniqueConstraint(
                fields=("tenant", "name"),
                condition=models.Q(("tenant__isnull", False), ("deleted_at__isnull", True)),
                name="unique_tenant_supplier_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="supplier",
            constraint=models.UniqueConstraint(
                fields=("tenant", "slug"),
                condition=models.Q(("tenant__isnull", False), ("deleted_at__isnull", True)),
                name="unique_tenant_supplier_slug",
            ),
        ),
        migrations.AddConstraint(
            model_name="supplier",
            constraint=models.UniqueConstraint(
                fields=("tenant_group", "name"),
                condition=models.Q(("tenant_group__isnull", False), ("deleted_at__isnull", True)),
                name="unique_tenant_group_supplier_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="supplier",
            constraint=models.UniqueConstraint(
                fields=("tenant_group", "slug"),
                condition=models.Q(("tenant_group__isnull", False), ("deleted_at__isnull", True)),
                name="unique_tenant_group_supplier_slug",
            ),
        ),
        migrations.AddConstraint(
            model_name="supplier",
            constraint=models.UniqueConstraint(
                fields=("name",),
                condition=models.Q(
                    ("tenant__isnull", True), ("tenant_group__isnull", True), ("deleted_at__isnull", True)
                ),
                name="unique_global_supplier_name",
            ),
        ),
        migrations.AddConstraint(
            model_name="supplier",
            constraint=models.UniqueConstraint(
                fields=("slug",),
                condition=models.Q(
                    ("tenant__isnull", True), ("tenant_group__isnull", True), ("deleted_at__isnull", True)
                ),
                name="unique_global_supplier_slug",
            ),
        ),
    ]
