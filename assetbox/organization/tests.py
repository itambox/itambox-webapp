from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.contenttypes.models import ContentType
from .models import Contact, ContactRole, ContactAssignment
from assets.models import Manufacturer

User = get_user_model()

class ContactsTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testadmin', password='password123', is_superuser=True, is_staff=True)
        self.client.force_login(self.user)
        
        # Create standard Manufacturer
        self.manufacturer = Manufacturer.objects.create(name='Dell Technologies', slug='dell')
        
        # Create Contact Roles
        self.support_role = ContactRole.objects.create(name='Technical Support')
        self.sales_role = ContactRole.objects.create(name='Sales Rep')
        
        # Create Contact
        self.contact = Contact.objects.create(
            name='Dell Enterprise Support',
            email='enterprise-support@dell.com',
            phone='+1-800-456-3355',
            web_url='https://support.dell.com'
        )

    def test_contact_role_slug_auto_generation(self):
        """Test that ContactRole automatically generates slug if not provided."""
        role = ContactRole.objects.create(name='Billing Department')
        self.assertEqual(role.slug, 'billing-department')

    def test_contact_assignment_and_manufacturer_resolver(self):
        """Test that Manufacturer resolves support contact correctly through get_support_contact."""
        mfr_ct = ContentType.objects.get_for_model(self.manufacturer)
        
        # 1. No contact assigned
        self.assertIsNone(self.manufacturer.get_support_contact)
        
        # 2. Assign contact with support role
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
        # 1. List
        list_url = reverse('organization:contact_list')
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)

        # 2. Create
        create_url = reverse('organization:contact_create')
        response = self.client.get(create_url)
        self.assertEqual(response.status_code, 200)
        
        post_data = {
            'name': 'Lenovo Support',
            'email': 'support@lenovo.com',
        }
        response = self.client.post(create_url, post_data)
        self.assertEqual(response.status_code, 302) # Redirects to absolute url (detail page)
        self.assertTrue(Contact.objects.filter(name='Lenovo Support').exists())

        # 3. Detail
        detail_url = reverse('organization:contact_detail', kwargs={'pk': self.contact.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)

        # 4. Edit
        edit_url = reverse('organization:contact_update', kwargs={'pk': self.contact.pk})
        response = self.client.get(edit_url)
        self.assertEqual(response.status_code, 200)

        # 5. Delete
        delete_url = reverse('organization:contact_delete', kwargs={'pk': self.contact.pk})
        response = self.client.get(delete_url)
        self.assertEqual(response.status_code, 200)

    def test_contact_roles_crud_views(self):
        """Test that ContactRoles CRUD views resolve correctly."""
        # 1. List
        list_url = reverse('organization:contactrole_list')
        response = self.client.get(list_url)
        self.assertEqual(response.status_code, 200)

        # 2. Create
        create_url = reverse('organization:contactrole_create')
        response = self.client.get(create_url)
        self.assertEqual(response.status_code, 200)

        # 3. Detail
        detail_url = reverse('organization:contactrole_detail', kwargs={'pk': self.support_role.pk})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)

    def test_contact_assignment_views(self):
        """Test ContactAssignmentCreateView and ContactAssignmentDeleteView."""
        mfr_ct = ContentType.objects.get_for_model(self.manufacturer)
        
        # 1. GET ContactAssignmentCreateView
        assign_url = reverse('organization:contactassignment_create')
        response = self.client.get(assign_url, {'content_type': mfr_ct.id, 'object_id': self.manufacturer.pk})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Assign Contact', response.content)
        
        # 2. POST ContactAssignmentCreateView
        post_data = {
            'contact': self.contact.pk,
            'role': self.support_role.pk,
            'priority': 'primary',
            'content_type': mfr_ct.id,
            'object_id': self.manufacturer.pk,
        }
        response = self.client.post(assign_url, post_data)
        self.assertEqual(response.status_code, 302) # Redirects back to Manufacturer detail view
        
        assignment = ContactAssignment.objects.get(contact=self.contact, role=self.support_role)
        self.assertEqual(assignment.priority, 'primary')
        
        # 3. DELETE ContactAssignmentDeleteView
        delete_url = reverse('organization:contactassignment_delete', kwargs={'pk': assignment.pk})
        response = self.client.post(delete_url, {'confirm': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ContactAssignment.objects.filter(pk=assignment.pk).exists())
