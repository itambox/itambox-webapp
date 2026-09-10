from django.contrib.auth.models import Permission
from django.test import SimpleTestCase, TestCase

from extras.models import SpecificationLibrary


class LibraryAdministrationCapabilityTests(SimpleTestCase):
    def test_library_declares_separate_administration_capability(self):
        self.assertIn(
            "manage_specification_library",
            {codename for codename, _label in SpecificationLibrary._meta.permissions},
        )


class LibraryAdministrationPermissionDatabaseTests(TestCase):
    def test_migrations_provision_library_permission(self):
        self.assertTrue(
            Permission.objects.filter(
                content_type__app_label="extras",
                content_type__model="specificationlibrary",
                codename="manage_specification_library",
            ).exists()
        )
