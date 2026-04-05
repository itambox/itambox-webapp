import os
from django.test import TestCase
from core.crypto import encrypt_string, decrypt_string
from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.management import call_command
from licenses.models import License
from software.models import Software
from assets.models import Manufacturer

User = get_user_model()

class CoreCryptoTestCase(TestCase):
    def test_multi_key_encryption_consolidation(self):
        """Test encryption key rotation using MultiFernet."""
        key1 = Fernet.generate_key().decode('ascii')
        key2 = Fernet.generate_key().decode('ascii')
        
        os.environ['ASSETBOX_FIELD_ENCRYPTION_KEYS'] = f"{key1},{key2}"
        plain = "SuperSecretToken"
        cipher = encrypt_string(plain)
        
        decrypted = decrypt_string(cipher)
        self.assertEqual(decrypted, plain)
        
        os.environ['ASSETBOX_FIELD_ENCRYPTION_KEYS'] = f"{key2},{key1}"
        decrypted_rotated = decrypt_string(cipher)
        self.assertEqual(decrypted_rotated, plain)
        
        if 'ASSETBOX_FIELD_ENCRYPTION_KEYS' in os.environ:
            del os.environ['ASSETBOX_FIELD_ENCRYPTION_KEYS']

    def test_rotate_encryption_keys_command(self):
        """Test that the rotate_encryption_keys management command successfully decrypts with old key and re-encrypts with new primary key."""
        mfr = Manufacturer.objects.create(name="Microsoft", slug="microsoft")
        software = Software.objects.create(name="Office 365", version="v2026", manufacturer=mfr)
        
        old_key = Fernet.generate_key().decode('ascii')
        new_key = Fernet.generate_key().decode('ascii')
        
        # Set old key as primary/only key
        os.environ['ASSETBOX_FIELD_ENCRYPTION_KEYS'] = old_key
        raw_product_key = "MICROSOFT-OFFICE-KEY-2026"
        
        license_obj = License.objects.create(
            name="Office Suite",
            software=software,
            seats=10,
            product_key=encrypt_string(raw_product_key)
        )
        
        # Verify it encrypted correctly with the old key
        self.assertTrue(license_obj.product_key.startswith("enc$"))
        
        # Rotate key in settings (new key is primary, old key is fallback)
        os.environ['ASSETBOX_FIELD_ENCRYPTION_KEYS'] = f"{new_key},{old_key}"
        
        # Call rotate_encryption_keys command
        call_command('rotate_encryption_keys')
        
        # Refresh from db
        license_obj.refresh_from_db()
        
        # Decrypted value should still be correct
        self.assertEqual(license_obj.decrypted_product_key, raw_product_key)
        
        # Product key in db should now be encrypted using new key (which is different from old key's ciphertext)
        # We can verify this by checking that decrypting the ciphertext with ONLY the new key succeeds!
        os.environ['ASSETBOX_FIELD_ENCRYPTION_KEYS'] = new_key
        self.assertEqual(license_obj.decrypted_product_key, raw_product_key)
        
        # Clean up environment variables
        if 'ASSETBOX_FIELD_ENCRYPTION_KEYS' in os.environ:
            del os.environ['ASSETBOX_FIELD_ENCRYPTION_KEYS']
