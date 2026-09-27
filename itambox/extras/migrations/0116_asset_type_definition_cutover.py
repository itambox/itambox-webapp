"""Normalized #479 definition cutover.

Drops the legacy definition columns and transitional structures and lands the
final definition constraints: unconditional unique identities, the activation
check and the library/management coherence checks.
"""

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0105_asset_type_provenance_capture"),
        ("extras", "0115_asset_type_definition_conversion"),
        ("users", "0100_issue88_shard_62_users_relations"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="customfield",
            name="unique_customfield_name_active",
        ),
        migrations.RemoveConstraint(
            model_name="customfieldset",
            name="unique_customfieldset_name_active",
        ),
        migrations.RemoveField(
            model_name="customfield",
            name="choices",
        ),
        migrations.RemoveField(
            model_name="customfield",
            name="deleted_at",
        ),
        migrations.RemoveField(
            model_name="customfieldset",
            name="deleted_at",
        ),
        migrations.RemoveField(
            model_name="customfieldset",
            name="legacy_fields",
        ),
        migrations.RemoveField(
            model_name="customfieldset",
            name="name",
        ),
        migrations.AlterField(
            model_name="customfield",
            name="activation",
            field=models.CharField(
                choices=[("composed", "Composed"), ("global", "Global")],
                db_index=True,
                max_length=16,
                verbose_name="Activation",
            ),
        ),
        migrations.AlterField(
            model_name="customfieldset",
            name="slug",
            field=models.CharField(
                max_length=127, validators=[django.core.validators.RegexValidator("^[a-z0-9][a-z0-9._-]{0,126}$")]
            ),
        ),
        migrations.AddConstraint(
            model_name="customfield",
            constraint=models.UniqueConstraint(fields=("name",), name="unique_customfield_name"),
        ),
        migrations.AddConstraint(
            model_name="customfield",
            constraint=models.CheckConstraint(
                condition=models.Q(("activation__in", ["composed", "global"])),
                name="customfield_activation_valid",
            ),
        ),
        migrations.AddConstraint(
            model_name="customfieldset",
            constraint=models.UniqueConstraint(fields=("namespace", "slug"), name="unique_customfieldset_identity"),
        ),
        migrations.AddConstraint(
            model_name="customfield",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("management_kind", "library"), ("library__isnull", False)),
                    models.Q(("management_kind__in", ["core", "local"]), ("library__isnull", True)),
                    _connector="OR",
                ),
                name="customfield_library_management_coherence",
            ),
        ),
        migrations.AddConstraint(
            model_name="customfieldset",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("management_kind", "library"), ("library__isnull", False)),
                    models.Q(("management_kind__in", ["core", "local"]), ("library__isnull", True)),
                    _connector="OR",
                ),
                name="customfieldset_library_management_coherence",
            ),
        ),
        migrations.AddConstraint(
            model_name="customfieldchoiceset",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("management_kind", "library"), ("library__isnull", False)),
                    models.Q(("management_kind__in", ["core", "local"]), ("library__isnull", True)),
                    _connector="OR",
                ),
                name="customfieldchoiceset_library_management_coherence",
            ),
        ),
    ]
