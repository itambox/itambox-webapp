from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.contenttypes.models import ContentType
from assets.models import Asset, StatusLabel
from organization.models import AssetHolder, AssetHolderAssignment

User = get_user_model()

class AssetHolderViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testadmin', password='password123', is_superuser=True, is_staff=True
        )
        self.client.force_login(self.user)
        self.holder = AssetHolder.objects.create(
            first_name='Alice', last_name='Johnson', upn='alice.johnson', email='alice@test.com'
        )

    def test_list_view(self):
        url = reverse('organization:assetholder_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_detail_view(self):
        url = reverse('organization:assetholder_detail', kwargs={'pk': self.holder.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_create_view_post(self):
        url = reverse('organization:assetholder_create')
        response = self.client.post(url, {
            'first_name': 'Bob',
            'last_name': 'Smith',
            'upn': 'bob.smith',
            'email': 'bob@test.com',
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.assertTrue(AssetHolder.objects.filter(upn='bob.smith').exists())

    def test_edit_view_post(self):
        url = reverse('organization:assetholder_update', kwargs={'pk': self.holder.pk})
        response = self.client.post(url, {
            'first_name': 'Alice',
            'last_name': 'Johnson-Smith',
            'upn': 'alice.johnson',
            'email': 'alice@test.com',
        })
        if response.status_code != 302:
            form = response.context.get('form')
            if form:
                self.fail(f'Form invalid. Errors: {form.errors}')
        self.holder.refresh_from_db()
        self.assertEqual(self.holder.last_name, 'Johnson-Smith')

    def test_delete_view_post(self):
        url = reverse('organization:assetholder_delete', kwargs={'pk': self.holder.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AssetHolder.objects.filter(pk=self.holder.pk).exists())

class AssetHolderAssignmentFilterSetTests(TestCase):
    def setUp(self):
        self.holder1 = AssetHolder.objects.create(
            first_name='Alice', last_name='Smith', upn='alice.smith', email='alice@test.com'
        )
        self.holder2 = AssetHolder.objects.create(
            first_name='Bob', last_name='Jones', upn='bob.jones', email='bob@test.com'
        )
        
        self.status = StatusLabel.objects.get_or_create(
            slug="available", defaults={'name': 'Available', 'type': StatusLabel.TYPE_DEPLOYABLE}
        )[0]

        self.asset1 = Asset.objects.create(
            name="Laptop 1", asset_tag="TAG-1", serial_number="SN-1", status=self.status
        )
        self.asset2 = Asset.objects.create(
            name="Laptop 2", asset_tag="TAG-2", serial_number="SN-2", status=self.status
        )
        
        self.ct = ContentType.objects.get_for_model(Asset)
        
        self.assign1 = AssetHolderAssignment.objects.create(
            asset_holder=self.holder1, content_type=self.ct, object_id=self.asset1.pk
        )
        self.assign2 = AssetHolderAssignment.objects.create(
            asset_holder=self.holder2, content_type=self.ct, object_id=self.asset2.pk
        )

    def test_filter_by_asset_holder(self):
        from organization.filters import AssetHolderAssignmentFilterSet
        f = AssetHolderAssignmentFilterSet({'asset_holder': self.holder1.pk}, queryset=AssetHolderAssignment.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.assign1, f.qs)
        self.assertNotIn(self.assign2, f.qs)

    def test_filter_by_content_type(self):
        from organization.filters import AssetHolderAssignmentFilterSet
        f = AssetHolderAssignmentFilterSet({'content_type': self.ct.pk}, queryset=AssetHolderAssignment.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.assign1, f.qs)
        self.assertIn(self.assign2, f.qs)

    def test_filter_search(self):
        from organization.filters import AssetHolderAssignmentFilterSet
        f = AssetHolderAssignmentFilterSet({'q': 'alice'}, queryset=AssetHolderAssignment.objects.all())
        self.assertTrue(f.is_valid())
        self.assertIn(self.assign1, f.qs)
        self.assertNotIn(self.assign2, f.qs)
