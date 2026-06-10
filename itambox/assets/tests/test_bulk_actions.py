from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from unittest.mock import patch

from assets.models import Manufacturer, Asset, AssetType, AssetRole, StatusLabel
from organization.models import AssetHolder, Tenant
from core.models import Job
from extras.models import LabelTemplate

User = get_user_model()

class BulkActionsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='testpassword', is_staff=True, is_superuser=True
        )
        self.client.login(username='testadmin', password='testpassword')

        self.tenant = Tenant.objects.create(name='Test Tenant', slug='test-tenant')
        self.manufacturer = Manufacturer.objects.create(name="Dell", slug="dell")
        self.role = AssetRole.objects.create(name="Laptop", slug="laptop")
        self.asset_type = AssetType.objects.create(
            manufacturer=self.manufacturer,
            model="ThinkPad T14",
            slug="lenovo-thinkpad-t14"
        )
        self.status, _ = StatusLabel.objects.get_or_create(
            slug="available",
            defaults={"name": "Available", "type": "deployable"}
        )

        self.asset1 = Asset.objects.create(
            name="Asset 1",
            asset_tag="AST-001",
            asset_type=self.asset_type,
            asset_role=self.role,
            status=self.status,
            tenant=self.tenant
        )
        self.asset2 = Asset.objects.create(
            name="Asset 2",
            asset_tag="AST-002",
            asset_type=self.asset_type,
            asset_role=self.role,
            status=self.status,
            tenant=self.tenant
        )

        self.holder = AssetHolder.objects.create(
            first_name="John",
            last_name="Doe",
            upn="john.doe@example.com",
            email="john.doe@example.com",
            tenant=self.tenant
        )

        self.label_template = LabelTemplate.objects.create(
            name="Standard QR",
            description="Standard QR label",
            barcode_format="qr",
            template_code="<div>{{ asset.name }}</div>"
        )

    @patch('django_q.tasks.async_task')
    def test_bulk_assign_assets(self, mock_async):
        url = reverse('assets:asset_bulk_assign')
        post_data = {
            'pk': [self.asset1.pk, self.asset2.pk],
            'holder_id': self.holder.pk,
        }
        
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)

        # Verify Job was created
        job = Job.objects.filter(name__contains="Bulk Checkout").first()
        self.assertIsNotNone(job)
        self.assertEqual(job.status, Job.STATUS_PENDING)

        # Verify async_task was called
        mock_async.assert_called_once()
        args = mock_async.call_args[0]
        self.assertEqual(args[0], 'core.tasks.bulk_checkout_task')
        self.assertEqual(args[1], job.pk)
        self.assertEqual(args[2], [str(self.asset1.pk), str(self.asset2.pk)])
        self.assertEqual(args[3], 'assetholder')
        self.assertEqual(args[4], self.holder.pk)

    @patch('django_q.tasks.async_task')
    def test_bulk_print_labels(self, mock_async):
        url = reverse('assets:asset_bulk_print_labels')
        post_data = {
            'pk': [self.asset1.pk, self.asset2.pk],
            'template_id': self.label_template.pk,
            'layout_mode': 'roll',
        }

        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)

        # Verify Job was created
        job = Job.objects.filter(name__contains="Label Batch Generation").first()
        self.assertIsNotNone(job)
        self.assertEqual(job.status, Job.STATUS_PENDING)

        # Verify async_task was called
        mock_async.assert_called_once()
        args = mock_async.call_args[0]
        self.assertEqual(args[0], 'core.tasks.labels.generate_label_pdf_batch_task')
        self.assertEqual(args[1], job.pk)
        self.assertEqual(args[2], [str(self.asset1.pk), str(self.asset2.pk)])
        self.assertEqual(args[3], self.label_template.pk)
        self.assertEqual(args[4], 'roll')

    def test_bulk_delete_assets_get(self):
        url = reverse('assets:asset_bulk_delete')
        post_data = {
            'pk': [self.asset1.pk, self.asset2.pk],
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'generic/object_confirm_bulk_delete.html')

    def test_bulk_delete_assets_confirm(self):
        url = reverse('assets:asset_bulk_delete')
        post_data = {
            'pk': [self.asset1.pk, self.asset2.pk],
            '_confirm': 'Confirm',
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)
        # Check that assets were deleted
        self.assertFalse(Asset.objects.filter(pk=self.asset1.pk).exists())
        self.assertFalse(Asset.objects.filter(pk=self.asset2.pk).exists())

    def test_bulk_edit_assets_get(self):
        url = reverse('assets:asset_bulk_edit')
        post_data = {
            'pk': [self.asset1.pk, self.asset2.pk],
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'generic/object_bulk_edit.html')

    def test_bulk_edit_assets_apply(self):
        status2 = StatusLabel.objects.create(
            name="Archived",
            slug="archived",
            type="archived"
        )
        url = reverse('assets:asset_bulk_edit')
        post_data = {
            'pk': [self.asset1.pk, self.asset2.pk],
            '_selected_fields': ['status'],
            'status': status2.pk,
            '_apply': 'Apply',
        }
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)
        # Check that assets were updated
        self.asset1.refresh_from_db()
        self.asset2.refresh_from_db()
        self.assertEqual(self.asset1.status, status2)
        self.assertEqual(self.asset2.status, status2)

    def test_bulk_edit_assets_apply_tags(self):
        from extras.models import Tag
        tag1 = Tag.objects.create(name="Tag 1", slug="tag-1")
        tag2 = Tag.objects.create(name="Tag 2", slug="tag-2")
        
        # Add tag1 initially to asset1
        self.asset1.tags.add(tag1)
        
        url = reverse('assets:asset_bulk_edit')
        post_data = {
            'pk': [self.asset1.pk, self.asset2.pk],
            '_selected_fields': ['add_tags', 'remove_tags'],
            'add_tags': [tag2.pk],
            'remove_tags': [tag1.pk],
            '_apply': 'Apply',
        }
        
        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)
        
        # Verify tag changes
        self.asset1.refresh_from_db()
        self.asset2.refresh_from_db()
        
        # asset1 should have tag2 but not tag1
        self.assertIn(tag2, self.asset1.tags.all())
        self.assertNotIn(tag1, self.asset1.tags.all())
        
        # asset2 should have tag2 but not tag1
        self.assertIn(tag2, self.asset2.tags.all())
        self.assertNotIn(tag1, self.asset2.tags.all())

