import os
from io import StringIO
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.management import call_command
from django.test import TestCase

from core.models import EmailSettings
from extras.models import WebhookEndpoint
from licenses.models import License
from software.models import Software
from assets.models import Manufacturer


class RotateEncryptionKeysTestCase(TestCase):
    """WS7-2: key rotation must re-encrypt EVERY encrypted field, not only
    License.product_key. Otherwise EmailSettings.smtp_password and WebhookEndpoint.secret
    stay under the old key and become permanently undecryptable once the old key is dropped."""

    def test_rotation_covers_all_encrypted_fields(self):
        old_key = Fernet.generate_key().decode()
        new_key = Fernet.generate_key().decode()

        # 1. Encrypt all three secrets under the OLD key.
        with patch.dict(os.environ, {'ITAMBOX_FIELD_ENCRYPTION_KEYS': old_key}):
            email = EmailSettings.objects.create(smtp_password='smtp-secret')
            webhook = WebhookEndpoint.objects.create(
                name='WH', url='https://example.com/hook', secret='wh-secret'
            )
            mfr = Manufacturer.objects.create(name='RotMfr', slug='rot-mfr')
            sw = Software.objects.create(name='RotSw', manufacturer=mfr)
            lic = License.objects.create(name='RotLic', software=sw, product_key='PK-123')

        # 2. Add the NEW key as primary (OLD still present), then rotate.
        with patch.dict(os.environ, {'ITAMBOX_FIELD_ENCRYPTION_KEYS': f'{new_key},{old_key}'}):
            call_command('rotate_encryption_keys', stdout=StringIO(), stderr=StringIO())

        # 3. Drop the OLD key — only NEW remains. Every secret must still decrypt.
        with patch.dict(os.environ, {'ITAMBOX_FIELD_ENCRYPTION_KEYS': new_key}):
            email.refresh_from_db()
            webhook.refresh_from_db()
            lic.refresh_from_db()
            self.assertEqual(email.smtp_password_decrypted, 'smtp-secret')
            self.assertEqual(webhook.secret_decrypted, 'wh-secret')
            self.assertEqual(lic.decrypted_product_key, 'PK-123')
