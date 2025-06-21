import base64
import hashlib
from django.conf import settings
from cryptography.fernet import Fernet, InvalidToken

def get_fernet():
    """
    Derive a 32-byte key for Fernet from settings.SECRET_KEY.
    This ensures zero manual configuration changes or extra environment variables.
    """
    # Hash the SECRET_KEY to get a secure 32-byte digest
    key_bytes = hashlib.sha256(settings.SECRET_KEY.encode('utf-8')).digest()
    # URL-safe base64 encode the 32-byte digest to meet Fernet key requirements
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
