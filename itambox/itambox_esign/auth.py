import time
import jwt
import requests
import logging
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

logger = logging.getLogger(__name__)

def get_docusign_access_token():
    config = settings.PLUGINS_RESOLVED_CONFIG.get('itambox_esign', {})
    
    integration_key = config.get('DOCUSIGN_INTEGRATION_KEY')
    user_id = config.get('DOCUSIGN_USER_ID')
    private_key = config.get('DOCUSIGN_RSA_PRIVATE_KEY')
    sandbox = config.get('DOCUSIGN_SANDBOX', True)

    if not all([integration_key, user_id, private_key]):
        raise ImproperlyConfigured("DocuSign plugin is missing required settings (Integration Key, User ID, or RSA Key).")

    # Determine OAuth host based on environment
    oauth_host = "account-d.docusign.com" if sandbox else "account.docusign.com"
    token_url = f"https://{oauth_host}/oauth/token"

    # Construct JWT claim set (valid for 1 hour)
    now = int(time.time())
    payload = {
        "iss": integration_key,
        "sub": user_id,
        "iat": now,
        "exp": now + 3600,
        "aud": oauth_host,
        "scope": "signature impersonation"
    }

    try:
        # Encode JWT assertion using private key (RS256)
        # Handle string or bytes RSA key
        if isinstance(private_key, str):
            private_key_bytes = private_key.encode('utf-8')
        else:
            private_key_bytes = private_key

        assertion = jwt.encode(payload, private_key_bytes, algorithm="RS256")
        
        # Post to token endpoint
        response = requests.post(token_url, data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion
        }, timeout=10)
        
        response.raise_for_status()
        token_data = response.json()
        return token_data['access_token']
        
    except Exception as e:
        logger.error(f"Failed to fetch DocuSign access token: {e}")
        raise
