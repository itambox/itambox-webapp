"""
Tests for the Supplier <-> Contact unification (Task 5).

Verifies:
- A Supplier can have ContactAssignments via the shared Contact system.
- primary_contact property returns the primary assignment's contact.
- The SupplierSerializer exposes a `contacts` field.
"""

from django.test import TestCase
from django.contrib.contenttypes.models import ContentType
from django.urls import reverse

from assets.models import Supplier
from organization.models import Contact, ContactRole, ContactAssignment
from core.tests.mixins import TenantTestMixin


class SupplierContactModelTest(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(
            permissions=['assets.view_supplier', 'assets.change_supplier']
        )
        self.supplier = Supplier.objects.create(
            name='Acme Supplies',
            slug='acme-supplies',
            website='https://acme.example.com',
        )
        self.primary_role, _ = ContactRole.objects.get_or_create(
            slug='primary-contact',
            defaults={'name': 'Primary Contact', 'description': 'Primary Contact'},
        )
        self.secondary_role, _ = ContactRole.objects.get_or_create(
            slug='secondary-contact',
            defaults={'name': 'Secondary Contact', 'description': 'Secondary Contact'},
        )
        self.supplier_ct = ContentType.objects.get_for_model(Supplier)

    def _make_contact(self, name, email='', phone=''):
        return Contact.objects.create(name=name, email=email, phone=phone)

    def _assign(self, contact, role, priority):
        return ContactAssignment.objects.create(
            contact=contact,
            role=role,
            content_type=self.supplier_ct,
            object_id=self.supplier.pk,
            priority=priority,
        )

    def test_contacts_generic_relation(self):
        c1 = self._make_contact('Alice', email='alice@acme.example.com')
        self._assign(c1, self.primary_role, 'primary')
        self.assertEqual(self.supplier.contacts.count(), 1)

    def test_primary_contact_returns_primary_priority(self):
        c_sec = self._make_contact('Bob', email='bob@acme.example.com')
        c_pri = self._make_contact('Alice', email='alice@acme.example.com')
        self._assign(c_sec, self.secondary_role, 'secondary')
        self._assign(c_pri, self.primary_role, 'primary')
        self.assertEqual(self.supplier.primary_contact, c_pri)

    def test_primary_contact_falls_back_to_first(self):
        c = self._make_contact('Charlie', email='charlie@acme.example.com')
        self._assign(c, self.secondary_role, 'secondary')
        self.assertEqual(self.supplier.primary_contact, c)

    def test_primary_contact_none_when_no_contacts(self):
        self.assertIsNone(self.supplier.primary_contact)

    def test_multiple_contacts(self):
        for i in range(3):
            c = self._make_contact(f'Contact {i}', email=f'c{i}@acme.example.com')
            self._assign(c, self.primary_role, 'primary')
        self.assertEqual(self.supplier.contacts.count(), 3)

    def test_contact_fields_not_on_model(self):
        """Ensure the old inline fields are gone."""
        self.assertFalse(hasattr(Supplier, 'contact_email'))
        self.assertFalse(hasattr(Supplier, 'contact_phone'))
        self.assertFalse(hasattr(Supplier, 'contact_name'))


class SupplierSerializerContactsTest(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(
            permissions=['assets.view_supplier']
        )
        self.supplier = Supplier.objects.create(
            name='Beta Corp',
            slug='beta-corp',
        )
        self.role, _ = ContactRole.objects.get_or_create(
            slug='primary-contact',
            defaults={'name': 'Primary Contact', 'description': 'Primary Contact'},
        )
        self.supplier_ct = ContentType.objects.get_for_model(Supplier)
        self.contact = Contact.objects.create(
            name='Dana Smith', email='dana@beta.example.com', phone='+49-30-1234567'
        )
        ContactAssignment.objects.create(
            contact=self.contact,
            role=self.role,
            content_type=self.supplier_ct,
            object_id=self.supplier.pk,
            priority='primary',
        )

    def test_serializer_declares_contacts_field(self):
        """SupplierSerializer must declare 'contacts' in its Meta.fields."""
        from assets.api.serializers import SupplierSerializer
        self.assertIn('contacts', SupplierSerializer.Meta.fields)

    def test_serializer_no_legacy_fields(self):
        """Legacy inline contact fields must not be present in Meta.fields."""
        from assets.api.serializers import SupplierSerializer
        self.assertNotIn('contact_email', SupplierSerializer.Meta.fields)
        self.assertNotIn('contact_phone', SupplierSerializer.Meta.fields)
        self.assertNotIn('contact_name', SupplierSerializer.Meta.fields)

    def test_serializer_contacts_queryset(self):
        """contacts GenericRelation must return the assigned contact."""
        self.assertEqual(self.supplier.contacts.count(), 1)
        self.assertEqual(self.supplier.contacts.first().contact, self.contact)
