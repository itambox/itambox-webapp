from django.test import TestCase
from model_bakery import baker
from assets.models import Manufacturer
from ..models import Software

class SoftwareModelTests(TestCase):
    def setUp(self):
        self.manufacturer = baker.make(Manufacturer, name='Microsoft', slug='microsoft')

    def test_software_creation(self):
        software = baker.make(
            Software,
            name='Windows 11 Enterprise',
            manufacturer=self.manufacturer,
            description='Operating system for enterprise',
        )
        self.assertEqual(str(software), 'Microsoft - Windows 11 Enterprise')
        self.assertEqual(software.manufacturer, self.manufacturer)

    def test_software_absolute_url(self):
        software = baker.make(
            Software,
            name='Office 365',
            manufacturer=self.manufacturer,
        )
        url = software.get_absolute_url()
        self.assertIn(str(software.pk), url)

    def test_software_ordering(self):
        baker.make(Software, name='B Software', manufacturer=self.manufacturer)
        baker.make(Software, name='A Software', manufacturer=self.manufacturer)
        qs = Software.objects.all()
        self.assertEqual(qs[0].name, 'A Software')
        self.assertEqual(qs[1].name, 'B Software')

    def test_software_name_unique(self):
        baker.make(Software, name='Unique Software', manufacturer=self.manufacturer)
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            baker.make(Software, name='Unique Software', manufacturer=self.manufacturer)
