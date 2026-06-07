from django.db import migrations

def create_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    
    # Define groups
    approver_group, _ = Group.objects.get_or_create(name='IT Request Approver')
    procurement_group, _ = Group.objects.get_or_create(name='IT Procurement Manager')
    asset_manager_group, _ = Group.objects.get_or_create(name='IT Asset Manager')
    
    def get_perm(app_label, codename):
        return Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
        
    # Approver Permissions:
    approver_perms = [
        get_perm('assets', 'view_assetrequest'),
        get_perm('assets', 'add_assetrequest'),
        get_perm('assets', 'change_assetrequest'),
        get_perm('assets', 'approve_assetrequest'),
        get_perm('assets', 'view_asset'),
    ]
    approver_group.permissions.set([p for p in approver_perms if p is not None])
    
    # Procurement Manager Permissions:
    procurement_perms = [
        get_perm('assets', 'view_assetrequest'),
        get_perm('assets', 'view_asset'),
        get_perm('procurement', 'view_purchaseorder'),
        get_perm('procurement', 'add_purchaseorder'),
        get_perm('procurement', 'change_purchaseorder'),
        get_perm('procurement', 'delete_purchaseorder'),
        get_perm('procurement', 'receive_purchaseorder'),
        get_perm('procurement', 'approve_purchaseorder'),
    ]
    procurement_group.permissions.set([p for p in procurement_perms if p is not None])
    
    # Asset Manager Permissions:
    asset_manager_perms = [
        get_perm('assets', 'view_assetrequest'),
        get_perm('assets', 'change_assetrequest'),
        get_perm('assets', 'approve_assetrequest'),
        get_perm('assets', 'fulfill_assetrequest'),
        get_perm('assets', 'view_asset'),
        get_perm('assets', 'add_asset'),
        get_perm('assets', 'change_asset'),
        get_perm('assets', 'delete_asset'),
    ]
    asset_manager_group.permissions.set([p for p in asset_manager_perms if p is not None])

def remove_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=['IT Request Approver', 'IT Procurement Manager', 'IT Asset Manager']).delete()

class Migration(migrations.Migration):
    dependencies = [
        ('procurement', '0003_alter_purchaseorder_options'),
        ('assets', '0027_alter_assetrequest_options'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(create_groups, remove_groups),
    ]
