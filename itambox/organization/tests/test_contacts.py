from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.contenttypes.models import ContentType
from assets.models import Manufacturer
from organization.models import Contact, ContactRole, ContactAssignment

User = get_user_model()


class ContactsTestCase(TestCase):
    def setUp(self):
        from django.contrib.contenttypes.models import ContentType
        ContentType.objects.clear_cache()
        User.objects.filter(username='testadmin').delete()
        self.user = User.objects.create_user(username='testadmin', password='password123', is_superuser=True, is_staff=True)
        self.client.force_login(self.user)
        
        self.manufacturer, _ = Manufacturer.objects.get_or_create(slug='dell', defaults={'name': 'Dell Technologies'})
        self.support_role, _ = ContactRole.objects.get_or_create(name='Technical Support')
        self.sales_role, _ = ContactRole.objects.get_or_create(name='Sales Rep')
        
        self.contact, _ = Contact.objects.get_or_create(
            email='enterprise-support@dell.com',
            defaults={
                'name': 'Dell Enterprise Support',
                'phone': '+1-800-456-3355',
                'web_url': 'https://support.dell.com'
            }
        )

    def test_contact_role_slug_auto_generation(self):
        """Test that ContactRole automatically generates slug if not provided."""
        role = ContactRole.objects.create(name='Billing Department')
        self.assertEqual(role.slug, 'billing-department')

    def test_contact_assignment_and_manufacturer_resolver(self):
        """Test that Manufacturer resolves support contact correctly through get_support_contact."""
        mfr_ct = ContentType.objects.get_for_model(self.manufacturer)
        
        self.assertIsNone(self.manufacturer.get_support_contact)
        
        ContactAssignment.objects.create(
            contact=self.contact,
            role=self.support_role,
            content_type=mfr_ct,
            object_id=self.manufacturer.pk,
            priority='primary'
        )
        
        self.assertEqual(self.manufacturer.get_support_contact, self.contact)

    def test_contacts_crud_views(self):
        """Test that Contacts CRUD views resolve correctly and handle submissions."""
        list_url = reverse('organization:contact_list')
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)

        create_url = reverse('organization:contact_create')
        response = self.client.get(create_url)
        self.assertEqual(response.status_code, 200)
        
        post_data = {
            'name': 'Lenovo Support',
            'email': 'support@lenovo.com',
        }
        response = self.client.post(create_url, post_data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Contact.objects.filter(name='Lenovo Support').exists())

        detail_url = reverse('organization:contact_detail', kwargs={'pk': self.contact.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)

        edit_url = reverse('organization:contact_update', kwargs={'pk': self.contact.pk})
        response = self.client.get(edit_url)
        self.assertEqual(response.status_code, 200)

        delete_url = reverse('organization:contact_delete', kwargs={'pk': self.contact.pk})
        response = self.client.get(delete_url)
        self.assertEqual(response.status_code, 200)

    def test_contact_roles_crud_views(self):
        """Test that ContactRoles CRUD views resolve correctly."""
        list_url = reverse('organization:contactrole_list')
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)

        create_url = reverse('organization:contactrole_create')
        response = self.client.get(create_url)
        self.assertEqual(response.status_code, 200)

        detail_url = reverse('organization:contactrole_detail', kwargs={'pk': self.support_role.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)

    def test_contact_assignment_views(self):
        """Test ContactAssignmentCreateView and ContactAssignmentDeleteView."""
        mfr_ct = ContentType.objects.get_for_model(self.manufacturer)
        
        assign_url = reverse('organization:contactassignment_create')
        response = self.client.get(assign_url, {'content_type': mfr_ct.id, 'object_id': self.manufacturer.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Assign Contact', response.content)
        
        post_data = {
            'contact': self.contact.pk,
            'role': self.support_role.pk,
            'priority': 'primary',
            'content_type': mfr_ct.id,
            'object_id': self.manufacturer.pk,
        }
        response = self.client.post(assign_url, post_data)
        self.assertEqual(response.status_code, 302)
        
        assignment = ContactAssignment.objects.get(contact=self.contact, role=self.support_role)
        self.assertEqual(assignment.priority, 'primary')
        
        delete_url = reverse('organization:contactassignment_delete', kwargs={'pk': assignment.pk})
        response = self.client.post(delete_url, {'confirm': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ContactAssignment.objects.filter(pk=assignment.pk).exists())

class ContactRoleViewTests(TestCase):
    def setUp(self):
        from django.contrib.contenttypes.models import ContentType
        ContentType.objects.clear_cache()
        User.objects.filter(username='roleadmin').delete()
        self.user = User.objects.create_user(username='roleadmin', password='testpassword', is_staff=True, is_superuser=True)
        self.client.force_login(self.user)
        ContactRole.objects.filter(slug='key-support').delete()
        self.role = ContactRole.objects.create(name='Key Support', slug='key-support')

    def test_update_view_post(self):
        url = reverse('organization:contactrole_update', kwargs={'pk': self.role.pk})
        response = self.client.post(url, {'name': 'Key Support Updated', 'slug': 'key-support-updated'})
        self.assertEqual(response.status_code, 302)
        self.role.refresh_from_db()
        self.assertEqual(self.role.name, 'Key Support Updated')

    def test_delete_view_post(self):
        url = reverse('organization:contactrole_delete', kwargs={'pk': self.role.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ContactRole.objects.filter(pk=self.role.pk).exists())
