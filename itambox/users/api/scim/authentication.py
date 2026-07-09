import logging
import re
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework import exceptions
from django.utils import timezone
from users.models import Token
from organization.models import Tenant, Membership

logger = logging.getLogger('itambox.scim.auth')

class SCIMBearerTokenAuthentication(BaseAuthentication):
    def authenticate(self, request):
        # 1. Resolve tenant from URL path
        tenant_slug = None
        if request.resolver_match and 'tenant_slug' in request.resolver_match.kwargs:
            tenant_slug = request.resolver_match.kwargs['tenant_slug']
        else:
            match = re.search(r'/api/tenants/([^/]+)/scim/v2/', request.path)
            if match:
                tenant_slug = match.group(1)

        if not tenant_slug:
            return None

        try:
            tenant = Tenant._base_manager.get(slug=tenant_slug)
        except Tenant.DoesNotExist:
            raise exceptions.AuthenticationFailed('Tenant not found.')

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
                        'SCIM token %s... rejected: source IP %s not in allowed_ips',
                        token_key[:6], client_ip
                    )
                    raise exceptions.AuthenticationFailed(
                        'Source IP address is not permitted to use this token.'
                    )

            user = token.user
            if not user.is_active:
                raise exceptions.AuthenticationFailed('User inactive or deleted.')

            # Verify tenant membership with admin/owner role
            if not user.is_superuser:
                membership = Membership.objects.filter(user=user, tenant=tenant).prefetch_related('roles').first()
                if not membership:
                    raise exceptions.AuthenticationFailed('User does not have a membership in this tenant.')
                # Authorise admin/owner only. Roles are now an M2M: accept if ANY attached
                # role is named 'admin' or 'owner'. Fail closed when none qualifies.
                role_names = {(r.name or '').lower() for r in membership.roles.all()}
                if not role_names & {'admin', 'owner'}:
                    raise exceptions.AuthenticationFailed('User does not have sufficient permissions (admin or owner role required).')

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
