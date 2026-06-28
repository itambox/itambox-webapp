# WARNING: GREENFIELD ONLY. This migration is NOT safe for production databases containing legacy data.
# Unified RBAC, part 2 of 2 — re-target TenantInvitation.role and drop the legacy
# TenantRole + TenantMembership models. Depends on users/0008 so UserGroup.roles
# stops pointing at TenantRole BEFORE the table is dropped.
#
# Uses RunSQL + SeparateDatabaseAndState for the model drops because Django's
# DeleteModel would also try to drop the M2M intermediate ``organization_tenantmembership_roles``
# whose lifecycle was tangled by the historical migration sequence — a plain
# DROP TABLE ... CASCADE handles every artifact in one shot.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organization', '0026_remove_tenantrole_tenant_provider_tenant_provider_and_more'),
        ('users', '0008_alter_usergroup_options_token_provider_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='tenantinvitation',
            name='role',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='invitations',
                to='organization.role',
                verbose_name='Role',
            ),
        ),
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel(name='TenantMembership'),
                migrations.DeleteModel(name='TenantRole'),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql=[
                        'DROP TABLE IF EXISTS organization_tenantmembership_roles CASCADE;',
                        'DROP TABLE IF EXISTS organization_tenantmembership CASCADE;',
                        'DROP TABLE IF EXISTS organization_tenantrole CASCADE;',
                    ],
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
        ),
    ]
