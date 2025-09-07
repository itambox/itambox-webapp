import base64
import hashlib
from django.conf import settings
from cryptography.fernet import Fernet, MultiFernet, InvalidToken

import os

def get_fernet():
    """
    Get a Fernet or MultiFernet instance.
    Supports a MultiFernet keyring derived from the comma-separated
    ASSETBOX_FIELD_ENCRYPTION_KEYS environment variable or Django settings.
    Falls back to SECRET_KEY hashing in debug/dev environments.
    """
    keys_str = os.environ.get('ASSETBOX_FIELD_ENCRYPTION_KEYS') or getattr(settings, 'ASSETBOX_FIELD_ENCRYPTION_KEYS', None)
    
    if keys_str:
        keys = [k.strip() for k in keys_str.split(',') if k.strip()]
        if keys:
            fernet_instances = []
            for k in keys:
                try:
                    decoded = base64.urlsafe_b64decode(k)
                    if len(decoded) == 32:
                        fernet_instances.append(Fernet(k))
                        continue
                except Exception:
                    pass
                
                key_bytes = hashlib.sha256(k.encode('utf-8')).digest()
                fernet_key = base64.urlsafe_b64encode(key_bytes)
                fernet_instances.append(Fernet(fernet_key))
            
            if len(fernet_instances) > 1:
                return MultiFernet(fernet_instances)
            elif fernet_instances:
                return fernet_instances[0]

    key_bytes = hashlib.sha256(settings.SECRET_KEY.encode('utf-8')).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)

def encrypt_string(plain_text: str) -> str:
    """
    Encrypt a plaintext string and prepend the 'enc$' prefix sentinel.
    If the string is empty, returns an empty string.
    """
    if not plain_text:
        return ""
    
    fernet = get_fernet()
    encrypted_bytes = fernet.encrypt(plain_text.encode('utf-8'))
    return f"enc${encrypted_bytes.decode('ascii')}"

def decrypt_string(cipher_text: str) -> str:
    """
    Decrypt a cipher string starting with the 'enc$' prefix sentinel.
    If the string does not start with the prefix, it is treated as a plaintext
    value for backwards-compatibility.
    
    If decryption fails, returns the original cipher_text to avoid data loss.
    """
    if not cipher_text:
        return ""
    
    if not cipher_text.startswith("enc$"):
        # Backwards-compatibility fallback: treated as plaintext
        return cipher_text
    
    fernet = get_fernet()
    try:
        encrypted_part = cipher_text[4:]
        decrypted_bytes = fernet.decrypt(encrypted_part.encode('ascii'))
        return decrypted_bytes.decode('utf-8')
    except (InvalidToken, Exception):
        # Fail-safe fallback: return original if key changes or decryption fails
        return cipher_text
