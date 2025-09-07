import logging

from django.conf import settings
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header

logger = logging.getLogger('assetbox.auth')


class TokenAuthentication(BaseAuthentication):
    model = None  # Will be set via get_user_model() to users.models.Token

    def authenticate(self, request):
        if not (auth := get_authorization_header(request).split()):
            return None

        if auth[0].lower() != b'token':
            return None

        if len(auth) == 1:
            msg = 'Invalid token header. No credentials provided.'
            raise exceptions.AuthenticationFailed(msg)
        elif len(auth) > 2:
            msg = 'Invalid token header. Token string should not contain spaces.'
            raise exceptions.AuthenticationFailed(msg)

        try:
            token = auth[1].decode()
        except UnicodeError:
            msg = 'Invalid token header. Token string should not contain invalid characters.'
            raise exceptions.AuthenticationFailed(msg)

        return self.authenticate_credentials(token)

    def authenticate_credentials(self, key):
        from users.models import Token

        try:
            token = Token.objects.select_related('user').get(key=key)
        except Token.DoesNotExist:
            raise exceptions.AuthenticationFailed('Invalid token.')

        if token.is_expired:
            raise exceptions.AuthenticationFailed('Token expired.')

        if not token.user.is_active:
            raise exceptions.AuthenticationFailed('User inactive or deleted.')

        if not token.last_used or (timezone.now() - token.last_used).total_seconds() > 60:
            Token.objects.filter(pk=token.pk).update(last_used=timezone.now())

        return (token.user, token)

    def authenticate_header(self, request):
        return 'Token'


try:
    from drf_spectacular.extensions import OpenApiAuthenticationExtension
    
    class TokenAuthenticationScheme(OpenApiAuthenticationExtension):
        target_class = TokenAuthentication
        name = 'TokenAuth'

        def get_security_definition(self, auto_schema):
            return {
                'type': 'apiKey',
                'in': 'header',
                'name': 'Authorization',
                'description': 'Token-based authentication using "Token <token_key>"',
            }
except ImportError:
    pass

