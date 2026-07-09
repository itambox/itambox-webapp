from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from assets.models import Asset

User = get_user_model()


class ImportExportPermissionTestCase(TestCase):
    def setUp(self):
        # Create users
        self.admin = User.objects.create_user(
            username='adminuser', password='password123', is_staff=True, is_superuser=True
        )
        self.staff = User.objects.create_user(
            username='staffuser', password='password123', is_staff=True, is_superuser=False
        )
        self.guest = User.objects.create_user(
            username='guestuser', password='password123', is_staff=False, is_superuser=False
        )

        # Grant staff standard view permission on assets via multi-tenant RBAC
        from organization.models import Tenant, Role, Membership
        self.tenant = Tenant.objects.create(name='Test Tenant', slug='test-tenant')
        self.role = Role.objects.create(
            tenant=self.tenant,
            name='Staff Role',
            permissions=['assets.view_asset']
        )
        self.membership = Membership.objects.create(user=self.staff,
            tenant=self.tenant,
        )
        self.membership.roles.add(self.role)

    def test_list_view_gating_without_add_permission(self):
        # Log in as staff (with only view permission, no add permission)
        self.client.login(username='staffuser', password='password123')
        
        response = self.client.get(reverse('assets:asset_list'))
        self.assertEqual(response.status_code, 200)
        
        # Verify can_add is False in context
        self.assertFalse(response.context['can_add'])
        
        # Verify that "Create Asset" or "Import" links are NOT present in the HTML output
        self.assertNotContains(response, 'Create Asset')
        self.assertNotContains(response, 'Import')
        # Export should be present since it only requires view permission
        # Export needs only view permission. Since the kebab consolidation, the
        # affordance is the export links in the ⋮ menu (no literal "Export" label).
        self.assertContains(response, '/export/assets/asset/')

    def test_list_view_gating_with_add_permission(self):
        # Grant add permission to staff by updating the role permissions
        self.role.permissions = ['assets.view_asset', 'assets.add_asset']
        self.role.save()
        
        # Log in as staff
        self.client.login(username='staffuser', password='password123')
        
        response = self.client.get(reverse('assets:asset_list'))
        self.assertEqual(response.status_code, 200)
        
        # Verify can_add is True in context
        self.assertTrue(response.context['can_add'])
        
        # Verify that Create and Import action buttons are present in HTML output
        self.assertContains(response, 'Create Asset')
        self.assertContains(response, 'Import')
        # Export needs only view permission. Since the kebab consolidation, the
        # affordance is the export links in the ⋮ menu (no literal "Export" label).
        self.assertContains(response, '/export/assets/asset/')

    def test_list_view_gating_admin(self):
        # Log in as superuser
        self.client.login(username='adminuser', password='password123')
        
        response = self.client.get(reverse('assets:asset_list'))
        self.assertEqual(response.status_code, 200)
        
        # Superuser should always have add permission
        self.assertTrue(response.context['can_add'])
        self.assertContains(response, 'Create Asset')
        self.assertContains(response, 'Import')
        # Export needs only view permission. Since the kebab consolidation, the
        # affordance is the export links in the ⋮ menu (no literal "Export" label).
        self.assertContains(response, '/export/assets/asset/')


class AdvancedImportExportTestCase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='adminuser', password='password123', is_staff=True, is_superuser=True
        )
        self.client.login(username='adminuser', password='password123')
        
        from assets.models import StatusLabel, AssetType, Manufacturer
        self.status = StatusLabel.objects.create(name='Active', slug='active')
        self.manufacturer = Manufacturer.objects.create(name='Dell', slug='dell')
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model='Laptop',
            slug='laptop'
        )
        
        self.asset1 = Asset.objects.create(
            name='Asset Alpha',
            asset_tag='TAG-001',
            status=self.status,
            asset_type=self.asset_type
        )
        self.asset2 = Asset.objects.create(
            name='Asset Beta',
            asset_tag='TAG-002',
            status=self.status,
            asset_type=self.asset_type
        )

    def test_yaml_export_all(self):
        import yaml
        url = reverse('object_export', kwargs={
            'app_label': 'assets',
            'model_name': 'asset',
            'template_id': 0
        })
        response = self.client.get(f"{url}?format=yaml&export_scope=all")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/yaml')
        
        data = yaml.safe_load(response.content)
        self.assertEqual(len(data), 2)
        tags = [item['asset_tag'] for item in data]
        self.assertIn('TAG-001', tags)
        self.assertIn('TAG-002', tags)

    def test_csv_export_filtered(self):
        url = reverse('object_export', kwargs={
            'app_label': 'assets',
            'model_name': 'asset',
            'template_id': 0
        })
        response = self.client.get(f"{url}?format=csv&export_scope=filtered&q=Alpha")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        
        content = response.content.decode('utf-8')
        self.assertIn('TAG-001', content)
        self.assertNotIn('TAG-002', content)

    def test_yaml_export_filtered(self):
        import yaml
        url = reverse('object_export', kwargs={
            'app_label': 'assets',
            'model_name': 'asset',
            'template_id': 0
        })
        response = self.client.get(f"{url}?format=yaml&export_scope=filtered&q=Beta")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/yaml')
        
        data = yaml.safe_load(response.content)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]['asset_tag'], 'TAG-002')

    def test_yaml_import_text_preview_and_confirm(self):
        yaml_data = f"""
- name: "Asset Gamma"
  asset_tag: "TAG-003"
  status: "{self.status.pk}"
  asset_type: "{self.asset_type.pk}"
"""
        import_url = reverse('generic_import', kwargs={
            'app_label': 'assets',
            'model_name': 'asset'
        })
        
        post_data = {
            'active_tab': 'editor',
            'import_format': 'yaml',
            'import_text': yaml_data,
            '_preview': '1'
        }
        response = self.client.post(import_url, post_data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Asset Gamma')
        self.assertContains(response, 'TAG-003')
        
        response = self.client.post(import_url, {'_confirm': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/jobs/', response.url)
        
        self.assertTrue(Asset.objects.filter(asset_tag='TAG-003').exists())

    def test_csv_import_text_preview_and_confirm(self):
        csv_data = f"""name,asset_tag,status,asset_type
Asset Delta,TAG-004,{self.status.pk},{self.asset_type.pk}"""
        import_url = reverse('generic_import', kwargs={
            'app_label': 'assets',
            'model_name': 'asset'
        })
        
        post_data = {
            'active_tab': 'editor',
            'import_format': 'csv',
            'delimiter': ',',
            'import_text': csv_data,
            '_preview': '1'
        }
        response = self.client.post(import_url, post_data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Asset Delta')
        self.assertContains(response, 'TAG-004')
        
        response = self.client.post(import_url, {'_confirm': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/jobs/', response.url)
        
        self.assertTrue(Asset.objects.filter(asset_tag='TAG-004').exists())

    def test_yaml_import_file_upload_preview_and_confirm(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        yaml_content = f"""
- name: "Asset Epsilon"
  asset_tag: "TAG-005"
  status: "{self.status.pk}"
  asset_type: "{self.asset_type.pk}"
""".encode('utf-8')
        uploaded_file = SimpleUploadedFile("data.yaml", yaml_content, content_type="text/yaml")
        
        import_url = reverse('generic_import', kwargs={
            'app_label': 'assets',
            'model_name': 'asset'
        })
        
        post_data = {
            'active_tab': 'upload',
            'import_format': 'yaml',
            'csv_file': uploaded_file,
            '_preview': '1'
        }
        response = self.client.post(import_url, post_data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Asset Epsilon')
        self.assertContains(response, 'TAG-005')
        
        response = self.client.post(import_url, {'_confirm': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/jobs/', response.url)
        self.assertTrue(Asset.objects.filter(asset_tag='TAG-005').exists())

    def test_csv_import_upsert_existing(self):
        csv_data = f"""id,name,asset_tag
{self.asset1.pk},Asset Alpha Updated,TAG-001-UPDATED"""
        import_url = reverse('generic_import', kwargs={
            'app_label': 'assets',
            'model_name': 'asset'
        })
        
        post_data = {
            'active_tab': 'editor',
            'import_format': 'csv',
            'delimiter': ',',
            'import_text': csv_data,
            '_preview': '1'
        }
        response = self.client.post(import_url, post_data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Asset Alpha Updated')
        self.assertContains(response, 'TAG-001-UPDATED')
        
        response = self.client.post(import_url, {'_confirm': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/jobs/', response.url)
        
        self.asset1.refresh_from_db()
        self.assertEqual(self.asset1.name, 'Asset Alpha Updated')
        self.assertEqual(self.asset1.asset_tag, 'TAG-001-UPDATED')
        self.assertEqual(Asset.objects.count(), 2)

    def test_yaml_import_upsert_existing(self):
        yaml_data = f"""
- id: "{self.asset2.pk}"
  name: "Asset Beta Updated"
  asset_tag: "TAG-002-UPDATED"
"""
        import_url = reverse('generic_import', kwargs={
            'app_label': 'assets',
            'model_name': 'asset'
        })
        
        post_data = {
            'active_tab': 'editor',
            'import_format': 'yaml',
            'import_text': yaml_data,
            '_preview': '1'
        }
        response = self.client.post(import_url, post_data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Asset Beta Updated')
        self.assertContains(response, 'TAG-002-UPDATED')
        
        response = self.client.post(import_url, {'_confirm': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/jobs/', response.url)
        
        self.asset2.refresh_from_db()
        self.assertEqual(self.asset2.name, 'Asset Beta Updated')
        self.assertEqual(self.asset2.asset_tag, 'TAG-002-UPDATED')
        self.assertEqual(Asset.objects.count(), 2)

    def test_import_upsert_nonexistent_id_error(self):
        csv_data = """id,name,asset_tag
99999,Asset Phantom,TAG-999"""
        import_url = reverse('generic_import', kwargs={
            'app_label': 'assets',
            'model_name': 'asset'
        })
        
        post_data = {
            'active_tab': 'editor',
            'import_format': 'csv',
            'delimiter': ',',
            'import_text': csv_data,
            '_preview': '1'
        }
        response = self.client.post(import_url, post_data)
        self.assertEqual(response.status_code, 200)
        
        response = self.client.post(import_url, {'_confirm': '1'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('/jobs/', response.url)
        
        from core.models import Job
        job = Job.objects.latest('created')
        self.assertEqual(job.status, Job.STATUS_FAILED)
        self.assertIn('does not exist', job.logs)

