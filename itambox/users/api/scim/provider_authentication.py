import logging
import re
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework import exceptions
from django.utils import timezone

from core.auth.provider import has_provider_capability
from users.models import Token
from organization.models import Provider

logger = logging.getLogger('itambox.scim.auth')


class SCIMProviderBearerTokenAuthentication(BaseAuthentication):
    """Bearer-token auth for the provider-level SCIM endpoint.

    Mirrors ``SCIMBearerTokenAuthentication`` but resolves a ``Provider`` (not a
    ``Tenant``) from the URL and authorizes against provider capabilities instead of
    tenant roles. A request is accepted only when the bearer token is scoped to this
    provider AND the token's user holds ``manage_provider_users`` for it (superusers
    bypass both). Writes additionally require ``token.write_enabled``.
    """

    def authenticate(self, request):
        # 1. Resolve provider from URL path
        provider_slug = None
        if request.resolver_match and 'provider_slug' in request.resolver_match.kwargs:
            provider_slug = request.resolver_match.kwargs['provider_slug']
        else:
            match = re.search(r'/api/providers/([^/]+)/scim/v2/', request.path)
            if match:
                provider_slug = match.group(1)

        if not provider_slug:
            return None

        try:
            provider = Provider._base_manager.get(slug=provider_slug)
        except Provider.DoesNotExist:
            raise exceptions.AuthenticationFailed('Provider not found.')

        auth = get_authorization_header(request).split()

        # Bearer Auth
        if auth and auth[0].lower() == b'bearer':
            if len(auth) == 1:
                raise exceptions.AuthenticationFailed('Invalid token header. No credentials provided.')
            elif len(auth) > 2:
                raise exceptions.AuthenticationFailed('Invalid token header. Token string should not contain spaces.')

            try:
                token_key = auth[1].decode()
            except UnicodeError:
                raise exceptions.AuthenticationFailed('Invalid token header. Token string should not contain invalid characters.')

            token = Token.find_by_key(token_key)
            if token is None:
                raise exceptions.AuthenticationFailed('Invalid token.')

            if token.is_expired:
                raise exceptions.AuthenticationFailed('Token expired.')

            if token.allowed_ips:
                from itambox.ratelimit import get_client_ip
                client_ip = get_client_ip(request)
                if not token.validate_client_ip(client_ip):
                    logger.warning(
                        'SCIM provider token %s... rejected: source IP %s not in allowed_ips',
                        token_key[:6], client_ip
                    )
                    raise exceptions.AuthenticationFailed(
                        'Source IP address is not permitted to use this token.'
                    )

            user = token.user
            if not user.is_active:
                raise exceptions.AuthenticationFailed('User inactive or deleted.')

            # Authorization: superusers always pass. Everyone else must BOTH (a) present a
            # token scoped to THIS provider and (b) hold the manage_provider_users capability
            # for this provider. Fail closed otherwise.
            if not user.is_superuser:
                if token.provider_id != provider.pk:
                    raise exceptions.AuthenticationFailed('Token is not scoped to this provider.')
                if not has_provider_capability(user, 'manage_provider_users', provider=provider):
                    raise exceptions.AuthenticationFailed(
                        'User does not have sufficient permissions (manage_provider_users required).'
                    )

            # Enforce write_enabled token flag for write methods
            if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                if not token.write_enabled:
                    raise exceptions.AuthenticationFailed('Token does not have write permissions.')

            # Update last_used
            if not token.last_used or (timezone.now() - token.last_used).total_seconds() > 60:
                Token.objects.filter(pk=token.pk).update(last_used=timezone.now())

            return (user, token)

        return None

    def authenticate_header(self, request):
        return 'Bearer'
