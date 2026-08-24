import logging

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.request import Request

from core.managers import (
    set_current_all_accessible,
    set_current_membership,
    set_current_tenant,
    set_current_tenant_group,
)
from core.tenant_access import accessible_tenant_ids, active_membership
from itambox.ratelimit import get_client_ip
from users.models import Token, User

logger = logging.getLogger("itambox.auth")


class TokenAuthentication(BaseAuthentication):
    model = None  # Will be set via get_user_model() to users.models.Token

    def authenticate(self, request: Request) -> tuple[User, Token] | None:
        if not (auth := get_authorization_header(request).split()):
            return None

        if auth[0].lower() != b"token":
            return None

        if len(auth) == 1:
            msg = _("Invalid token header. No credentials provided.")
            raise exceptions.AuthenticationFailed(msg)
        elif len(auth) > 2:
            msg = _("Invalid token header. Token string should not contain spaces.")
            raise exceptions.AuthenticationFailed(msg)

        try:
            token = auth[1].decode()
        except UnicodeError:
            msg = _("Invalid token header. Token string should not contain invalid characters.")
            raise exceptions.AuthenticationFailed(msg) from None

        return self.authenticate_credentials(token, request)

    # Methods that mutate state require a write-enabled token.
    SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

    def authenticate_credentials(self, key: str, request: Request | None = None) -> tuple[User, Token]:
        token = self._resolve_token(key)
        self._validate_token_request(token, request, key)
        self._validate_token_tenant(token)
        self._touch_last_used(token)
        return self._bind_token_context(token, request)

    @staticmethod
    def _resolve_token(key: str) -> Token:
        token = Token.find_by_key(key)
        if token is None:
            raise exceptions.AuthenticationFailed(_("Invalid token."))
        return token

    def _validate_token_request(self, token: Token, request: Request | None, key: str) -> None:
        if token.is_expired:
            raise exceptions.AuthenticationFailed(_("Token expired."))

        if request is not None and token.allowed_ips:
            client_ip = get_client_ip(request)
            if not token.validate_client_ip(client_ip):
                logger.warning("Token %s... rejected: source IP %s not in allowed_ips", key[:6], client_ip)
                raise exceptions.AuthenticationFailed(_("Source IP address is not permitted to use this token."))

        # A read-only token must not be usable for any state-changing request.
        if request is not None and not token.write_enabled and request.method not in self.SAFE_METHODS:
            raise exceptions.AuthenticationFailed(_("This token is read-only and cannot be used for write operations."))

        if not token.user.is_active:
            raise exceptions.AuthenticationFailed(_("User inactive or deleted."))

    @staticmethod
    def _validate_token_tenant(token: Token) -> None:
        # A token is bound to exactly one tenant. Authentication must stop being
        # valid as soon as that tenant is deleted or the user loses every access
        # path to it. Relying on model-level permission checks is insufficient:
        # several legitimate endpoints use only IsAuthenticated and would
        # otherwise remain reachable after RBAC revocation.
        if token.tenant.deleted_at is not None:
            raise exceptions.AuthenticationFailed(_("Token tenant inactive or deleted."))
        if not token.user.is_superuser and token.tenant_id not in accessible_tenant_ids(token.user):
            raise exceptions.AuthenticationFailed(_("Token user no longer has access to the token tenant."))

    @staticmethod
    def _touch_last_used(token: Token) -> None:
        if not token.last_used or (timezone.now() - token.last_used).total_seconds() > 60:
            Token.objects.filter(pk=token.pk).update(last_used=timezone.now())

    @staticmethod
    def _bind_token_context(token: Token, request: Request | None) -> tuple[User, Token]:
        set_current_tenant(token.tenant)
        # A token is bound to exactly one tenant (never a group or the
        # all-accessible scope) — clear both explicitly rather than relying on
        # TenantMiddleware having already zeroed them for the pre-auth anonymous
        # request, so a leaked/ambient group or all-accessible context can never
        # combine with the tenant just set into the contradictory state
        # core.managers.get_current_scope_conflict() would fail closed on.
        set_current_tenant_group(None)
        set_current_all_accessible(False)
        membership = active_membership(token.user, token.tenant_id)
        set_current_membership(membership)

        # TenantMiddleware runs before DRF token authentication and therefore
        # sees an anonymous request. Keep request-local state aligned with the
        # contextvars populated above for code that reads either representation.
        if request is not None:
            request.active_tenant = token.tenant
            request.active_tenant_group = None
            request.active_membership = membership
            request.active_all_accessible = False

        return (token.user, token)

    def authenticate_header(self, request: Request) -> str:
        return "Token"


class TokenAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = TokenAuthentication
    name = "TokenAuth"

    def get_security_definition(self, auto_schema: object) -> dict[str, str]:
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": 'Token-based authentication using "Token <token_key>"',
        }
