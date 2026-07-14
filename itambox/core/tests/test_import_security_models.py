from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.forms.import_forms import is_model_importable


SENSITIVE_MODEL_LABELS = (
    'organization.membership',
    'organization.role',
    'organization.rolegrant',
    'organization.rolegrantscope',
    'organization.tenantresourcegrant',
    'users.groupmembership',
    'users.token',
    'users.user',
    'users.usergroup',
)


class SecuritySensitiveImportBoundaryTests(TestCase):
    def setUp(self):
        self.superuser = get_user_model().objects.create_superuser(
            username='sensitive-import-superuser',
            password='password',
        )
        self.client.force_login(self.superuser)

    def test_security_sensitive_models_are_not_generically_importable(self):
        for label in SENSITIVE_MODEL_LABELS:
            with self.subTest(label=label):
                self.assertFalse(is_model_importable(apps.get_model(label)))

    def test_direct_generic_import_urls_are_404_even_for_superuser(self):
        for label in SENSITIVE_MODEL_LABELS:
            app_label, model_name = label.split('.')
            with self.subTest(label=label):
                response = self.client.get(reverse('generic_import', kwargs={
                    'app_label': app_label,
                    'model_name': model_name,
                }))
                self.assertEqual(response.status_code, 404)
