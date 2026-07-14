import logging

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header

logger = logging.getLogger('itambox.auth')


class TokenAuthentication(BaseAuthentication):
    model = None  # Will be set via get_user_model() to users.models.Token

    def authenticate(self, request):
        if not (auth := get_authorization_header(request).split()):
            return None

        if auth[0].lower() != b'token':
            return None

        if len(auth) == 1:
            msg = _('Invalid token header. No credentials provided.')
            raise exceptions.AuthenticationFailed(msg)
        elif len(auth) > 2:
            msg = _('Invalid token header. Token string should not contain spaces.')
            raise exceptions.AuthenticationFailed(msg)

        try:
            token = auth[1].decode()
        except UnicodeError:
            msg = _('Invalid token header. Token string should not contain invalid characters.')
            raise exceptions.AuthenticationFailed(msg)

        return self.authenticate_credentials(token, request)

    # Methods that mutate state require a write-enabled token.
    SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')

    def authenticate_credentials(self, key, request=None):
        from users.models import Token

        token = Token.find_by_key(key)
        if token is None:
            raise exceptions.AuthenticationFailed(_('Invalid token.'))

        if token.is_expired:
            raise exceptions.AuthenticationFailed(_('Token expired.'))

        if request is not None and token.allowed_ips:
            from itambox.ratelimit import get_client_ip
            client_ip = get_client_ip(request)
            if not token.validate_client_ip(client_ip):
                logger.warning(
                    'Token %s... rejected: source IP %s not in allowed_ips', key[:6], client_ip
                )
                raise exceptions.AuthenticationFailed(
                    _('Source IP address is not permitted to use this token.')
                )

        # A read-only token must not be usable for any state-changing request.
        if request is not None and not token.write_enabled and request.method not in self.SAFE_METHODS:
            raise exceptions.AuthenticationFailed(
                _('This token is read-only and cannot be used for write operations.')
            )

        if not token.user.is_active:
            raise exceptions.AuthenticationFailed(_('User inactive or deleted.'))

        # A token is bound to exactly one tenant. Authentication must stop being
        # valid as soon as that tenant is deleted or the user loses every access
        # path to it. Relying on model-level permission checks is insufficient:
        # several legitimate endpoints use only IsAuthenticated and would
        # otherwise remain reachable after RBAC revocation.
        if token.tenant.deleted_at is not None:
            raise exceptions.AuthenticationFailed(_('Token tenant inactive or deleted.'))
        if not token.user.is_superuser:
            from organization.access import accessible_tenant_ids
            if token.tenant_id not in accessible_tenant_ids(token.user):
                raise exceptions.AuthenticationFailed(
                    _('Token user no longer has access to the token tenant.')
                )

        if not token.last_used or (timezone.now() - token.last_used).total_seconds() > 60:
            Token.objects.filter(pk=token.pk).update(last_used=timezone.now())

        from core.managers import set_current_tenant, set_current_membership
        from organization.models import Membership
        set_current_tenant(token.tenant)
        membership = Membership.objects.filter(
            user=token.user, tenant=token.tenant, is_active=True,
        ).first()
        set_current_membership(membership)

        # TenantMiddleware runs before DRF token authentication and therefore
        # sees an anonymous request. Keep request-local state aligned with the
        # contextvars populated above for code that reads either representation.
        if request is not None:
            request.active_tenant = token.tenant
            request.active_tenant_group = None
            request.active_membership = membership

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
