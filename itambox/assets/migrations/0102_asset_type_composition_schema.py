"""Normalized #479 composition schema for Asset Types.

Lands the final ``AssetType`` specification identity (``library`` /
``library_definition_key`` / ``management_kind`` / ``lifecycle``), the ordered
composition tables (``AssetTypeFieldset`` / ``CategoryDefaultFieldset``) and the
``AssetTypeImageStage`` upload staging model directly.  The singular legacy
``AssetType.custom_fieldset`` column is left in place so the conversion
migration can backfill the ordered composition before the cutover drops it.
"""

import django.core.validators
import django.db.models.constraints
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0101_seed_canonical_missing_status"),
        ("extras", "0114_asset_type_definition_library_schema"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.CreateModel(
            name="AssetTypeFieldset",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("position", models.PositiveIntegerField()),
            ],
            options={
                "ordering": ["position", "fieldset__namespace", "fieldset__slug"],
            },
        ),
        migrations.CreateModel(
            name="CategoryDefaultFieldset",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("position", models.PositiveIntegerField()),
            ],
            options={
                "ordering": ["position", "fieldset__namespace", "fieldset__slug"],
            },
        ),
        migrations.CreateModel(
            name="AssetTypeImageStage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("stage_id", models.CharField(db_index=True, max_length=64, unique=True, verbose_name="Stage ID")),
                ("actor_id", models.PositiveBigIntegerField(db_index=True, verbose_name="Actor ID")),
                (
                    "authentication_revision",
                    models.CharField(max_length=64, verbose_name="Authentication Revision"),
                ),
                ("command_kind", models.CharField(max_length=64, verbose_name="Command Kind")),
                ("storage_key", models.CharField(max_length=255, unique=True, verbose_name="Storage Key")),
                ("byte_size", models.PositiveBigIntegerField(verbose_name="Byte Size")),
                ("content_digest", models.CharField(max_length=64, verbose_name="Content Digest")),
                (
                    "state",
                    models.CharField(
                        choices=[("pending", "Pending"), ("consumed", "Consumed"), ("discarded", "Discarded")],
                        db_index=True,
                        default="pending",
                        max_length=16,
                        verbose_name="State",
                    ),
                ),
                ("expires_at", models.DateTimeField(db_index=True, verbose_name="Expires At")),
                (
                    "consumed_asset_type",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="consumed_image_stages",
                        to="assets.assettype",
                        verbose_name="Consumed Asset Type",
                    ),
                ),
            ],
            options={
                "verbose_name": "Asset Type Image Stage",
                "verbose_name_plural": "Asset Type Image Stages",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddField(
            model_name="assettype",
            name="configuration",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="assettype",
            name="connector_identity",
            field=models.CharField(blank=True, db_index=True, max_length=71, null=True),
        ),
        migrations.AddField(
            model_name="assettype",
            name="deprecated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="assettype",
            name="library",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="asset_types",
                to="extras.specificationlibrary",
            ),
        ),
        migrations.AddField(
            model_name="assettype",
            name="library_definition_key",
            field=models.CharField(
                blank=True,
                max_length=127,
                null=True,
                validators=[django.core.validators.RegexValidator("^[a-z][a-z0-9]*(?:[-_.][a-z0-9]+)*$")],
            ),
        ),
        migrations.AddField(
            model_name="assettype",
            name="lifecycle",
            field=models.CharField(
                choices=[("active", "Active"), ("deprecated", "Deprecated")], default="active", max_length=16
            ),
        ),
        migrations.AddField(
            model_name="assettype",
            name="management_kind",
            field=models.CharField(
                choices=[("core", "Core"), ("library", "Library"), ("local", "Local")], default="local", max_length=16
            ),
        ),
        migrations.AddField(
            model_name="assettype",
            name="region",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="assettype",
            name="replaced_by",
            field=models.CharField(blank=True, max_length=190, null=True),
        ),
        migrations.AddField(
            model_name="assettypefieldset",
            name="asset_type",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="fieldset_memberships", to="assets.assettype"
            ),
        ),
        migrations.AddField(
            model_name="assettypefieldset",
            name="fieldset",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="asset_type_memberships",
                to="extras.customfieldset",
            ),
        ),
        migrations.AddField(
            model_name="categorydefaultfieldset",
            name="category",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name="default_fieldset_memberships", to="assets.category"
            ),
        ),
        migrations.AddField(
            model_name="categorydefaultfieldset",
            name="fieldset",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="category_default_memberships",
                to="extras.customfieldset",
            ),
        ),
        migrations.AddField(
            model_name="assettype",
            name="custom_fieldsets",
            field=models.ManyToManyField(
                blank=True,
                related_name="composed_asset_types",
                through="assets.AssetTypeFieldset",
                to="extras.customfieldset",
            ),
        ),
        migrations.AddField(
            model_name="category",
            name="default_custom_fieldsets",
            field=models.ManyToManyField(
                blank=True,
                related_name="default_for_categories",
                through="assets.CategoryDefaultFieldset",
                to="extras.customfieldset",
            ),
        ),
        migrations.AddConstraint(
            model_name="assettype",
            constraint=models.UniqueConstraint(
                fields=("library", "library_definition_key"), name="assettype_library_identity_uq"
            ),
        ),
        migrations.AddConstraint(
            model_name="assettype",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("library__isnull", True), ("library_definition_key__isnull", True)),
                    models.Q(("library__isnull", False), ("library_definition_key__isnull", False)),
                    _connector="OR",
                ),
                name="assettype_library_identity_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="assettype",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("management_kind", "library"),
                        ("library__isnull", False),
                        ("library_definition_key__isnull", False),
                        models.Q(("library_definition_key", ""), _negated=True),
                    ),
                    models.Q(
                        ("management_kind__in", ["core", "local"]),
                        ("library__isnull", True),
                        ("library_definition_key__isnull", True),
                    ),
                    _connector="OR",
                ),
                name="assettype_library_mgmt_ck",
            ),
        ),
        migrations.AddConstraint(
            model_name="assettypefieldset",
            constraint=models.UniqueConstraint(fields=("asset_type", "fieldset"), name="unique_assettype_fieldset"),
        ),
        migrations.AddConstraint(
            model_name="assettypefieldset",
            constraint=models.UniqueConstraint(
                deferrable=django.db.models.constraints.Deferrable["DEFERRED"],
                fields=("asset_type", "position"),
                name="unique_assettype_fieldset_position",
            ),
        ),
        migrations.AddConstraint(
            model_name="assettypefieldset",
            constraint=models.CheckConstraint(
                condition=models.Q(("position__gte", 1), ("position__lte", 1000000)),
                name="assettype_fieldset_position_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="categorydefaultfieldset",
            constraint=models.UniqueConstraint(fields=("category", "fieldset"), name="unique_category_default_fieldset"),
        ),
        migrations.AddConstraint(
            model_name="categorydefaultfieldset",
            constraint=models.UniqueConstraint(
                deferrable=django.db.models.constraints.Deferrable["DEFERRED"],
                fields=("category", "position"),
                name="unique_category_default_position",
            ),
        ),
        migrations.AddConstraint(
            model_name="categorydefaultfieldset",
            constraint=models.CheckConstraint(
                condition=models.Q(("position__gte", 1), ("position__lte", 1000000)),
                name="category_default_position_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="assettypeimagestage",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("state", "consumed"), ("consumed_asset_type__isnull", False)),
                    models.Q(models.Q(("state", "consumed"), _negated=True), ("consumed_asset_type__isnull", True)),
                    _connector="OR",
                ),
                name="assettypeimagestage_consumed_link_ck",
            ),
        ),
    ]
