# WARNING: GREENFIELD ONLY. This migration is NOT safe for production databases containing legacy data.
# Unified RBAC, part 1 of 2 — create the new Role + Membership + Provider models.
# (The TenantInvitation FK swap and TenantRole/TenantMembership delete live in
# organization/0027 so users/0008 can re-target UserGroup.roles between the two.)

import core.mixins
import core.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('organization', '0025_delete_usergroup'),
        ('users', '0007_usergroup'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # tenant FK on TenantRole is no longer used; drop it so the field's NOT NULL
        # constraint doesn't fight the delete in 0027.
        migrations.RemoveField(
            model_name='tenantrole',
            name='tenant',
        ),
        migrations.CreateModel(
            name='Provider',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted_at', models.DateTimeField(blank=True, db_index=True, editable=False, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=100, verbose_name='Name')),
                ('slug', models.SlugField(max_length=100, verbose_name='Slug')),
                ('description', models.TextField(blank=True, verbose_name='Description')),
                ('comments', models.TextField(blank=True, verbose_name='Comments')),
                ('settings', models.JSONField(blank=True, default=dict, verbose_name='Settings')),
                ('internal_tenant', models.ForeignKey(blank=True, help_text="The provider's own IT inventory tenant ('home base' for provider staff).", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='provider_internal', to='organization.tenant', verbose_name='Internal tenant')),
            ],
            options={
                'verbose_name': 'Provider',
                'verbose_name_plural': 'Providers',
                'ordering': ['name'],
                'permissions': [('manage_provider', 'Can manage provider settings'), ('manage_tenants', 'Can manage customer tenants under a provider'), ('manage_staff', 'Can manage provider staff'), ('manage_groups', 'Can manage user groups')],
            },
            bases=(core.mixins.AutoSlugMixin, core.mixins.TaggableMixin, core.mixins.ExportableMixin, core.mixins.CloneableMixin, core.models.ChangeLoggingMixin, models.Model),
        ),
        migrations.AddField(
            model_name='tenant',
            name='provider',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tenants', to='organization.provider', verbose_name='Provider'),
        ),
        migrations.CreateModel(
            name='Role',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted_at', models.DateTimeField(blank=True, db_index=True, editable=False, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('scope', models.CharField(choices=[('tenant', 'Tenant'), ('provider', 'Provider')], db_index=True, default='tenant', max_length=20, verbose_name='Scope')),
                ('name', models.CharField(max_length=100, verbose_name='Name')),
                ('slug', models.SlugField(max_length=100, verbose_name='Slug')),
                ('description', models.TextField(blank=True, verbose_name='Description')),
                ('permissions', models.JSONField(blank=True, default=list, help_text="List of permission codenames ('app_label.codename'). For provider-scoped roles, may include organization.manage_tenants/staff/groups/provider.", verbose_name='Permissions')),
                ('is_default', models.BooleanField(default=False, help_text="Auto-attach to new memberships in this role's scope (provider-scoped roles attach to new provider staff; tenant-scoped roles attach to new tenant members).", verbose_name='Default for new memberships')),
                ('provider', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='roles', to='organization.provider', verbose_name='Provider')),
                ('tenant', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='roles', to='organization.tenant', verbose_name='Tenant')),
            ],
            options={
                'verbose_name': 'Role',
                'verbose_name_plural': 'Roles',
                'ordering': ['scope', 'name'],
            },
            bases=(core.mixins.AutoSlugMixin, core.mixins.TaggableMixin, core.mixins.ExportableMixin, core.mixins.CloneableMixin, core.models.ChangeLoggingMixin, models.Model),
        ),
        migrations.CreateModel(
            name='Membership',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('person_type', models.CharField(choices=[('staff', 'Provider staff'), ('member', 'Member'), ('contact', 'Contact (no login)')], db_index=True, default='member', max_length=20, verbose_name='Person type')),
                ('direct_permissions', models.JSONField(blank=True, default=list, help_text='Permission codenames granted directly to this membership, independent of any role. Additive with role permissions.', verbose_name='Direct permissions')),
                ('tenant_scope', models.CharField(blank=True, choices=[('explicit', 'Explicit (assigned tenants only)'), ('tenant_group', 'Tenant group'), ('all', 'All provider tenants')], help_text='Provider staff only. How this technician reaches customer tenants.', max_length=20, null=True, verbose_name='Tenant scope')),
                ('is_active', models.BooleanField(db_index=True, default=True, verbose_name='Active')),
                ('joined_at', models.DateTimeField(auto_now_add=True)),
                ('assigned_tenants', models.ManyToManyField(blank=True, help_text="Used when ``tenant_scope='explicit'``.", related_name='provider_assignments', to='organization.tenant', verbose_name='Assigned tenants')),
                ('scope_group', models.ForeignKey(blank=True, help_text="Used when ``tenant_scope='tenant_group'``.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='provider_membership_scopes', to='organization.tenantgroup', verbose_name='Scope group')),
                ('tenant', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to='organization.tenant', verbose_name='Tenant')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to=settings.AUTH_USER_MODEL, verbose_name='User')),
                ('provider', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='memberships', to='organization.provider', verbose_name='Provider')),
                ('roles', models.ManyToManyField(blank=True, related_name='memberships', to='organization.role', verbose_name='Roles')),
            ],
            options={
                'verbose_name': 'Membership',
                'verbose_name_plural': 'Memberships',
                'ordering': ['user'],
            },
            bases=(core.models.ChangeLoggingMixin, models.Model),
        ),
        migrations.AddConstraint(
            model_name='provider',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('name',), name='organization_provider_unique_name_active'),
        ),
        migrations.AddConstraint(
            model_name='provider',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True)), fields=('slug',), name='organization_provider_unique_slug_active'),
        ),
        migrations.AddConstraint(
            model_name='role',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True), ('scope', 'tenant')), fields=('tenant', 'name'), name='organization_role_unique_tenant_name'),
        ),
        migrations.AddConstraint(
            model_name='role',
            constraint=models.UniqueConstraint(condition=models.Q(('deleted_at__isnull', True), ('scope', 'provider')), fields=('provider', 'name'), name='organization_role_unique_provider_name'),
        ),
        migrations.AddConstraint(
            model_name='role',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('scope', 'tenant'), ('tenant__isnull', False), ('provider__isnull', True)), models.Q(('scope', 'provider'), ('provider__isnull', False), ('tenant__isnull', True)), _connector='OR'), name='organization_role_scope_consistency'),
        ),
        migrations.AddConstraint(
            model_name='membership',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('person_type', 'staff'), ('provider__isnull', False), ('tenant__isnull', True)), models.Q(('person_type__in', ['member', 'contact']), ('tenant__isnull', False), ('provider__isnull', True)), _connector='OR'), name='organization_membership_scope_consistency'),
        ),
        migrations.AddConstraint(
            model_name='membership',
            constraint=models.UniqueConstraint(condition=models.Q(('tenant__isnull', False)), fields=('user', 'tenant'), name='organization_membership_unique_user_tenant'),
        ),
        migrations.AddConstraint(
            model_name='membership',
            constraint=models.UniqueConstraint(condition=models.Q(('provider__isnull', False)), fields=('user', 'provider'), name='organization_membership_unique_user_provider'),
        ),
    ]
