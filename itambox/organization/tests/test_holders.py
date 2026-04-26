from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.contenttypes.models import ContentType
from assets.models import Asset, StatusLabel
from organization.models import AssetHolder

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

    def test_detail_view_with_assignments(self):
        status = StatusLabel.objects.get_or_create(
            slug="available", defaults={'name': 'Available', 'type': 'deployable'}
        )[0]
        asset = Asset.objects.create(
            name="Laptop", asset_tag="LPT-88", serial_number="SN-88", status=status
        )
        from assets.models import AssetAssignment
        AssetAssignment.objects.create(
            asset=asset,
            assigned_user=self.holder,
            is_active=True
        )
        url = reverse('organization:assetholder_detail', kwargs={'pk': self.holder.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Laptop")
        self.assertContains(response, "LPT-88")
