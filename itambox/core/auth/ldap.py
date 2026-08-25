import logging
import sys
from dataclasses import dataclass

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import CommandError

from core import identity_provisioning, tenant_scope
from core.context import (
    get_current_all_accessible,
    get_current_membership,
    get_current_request_id,
    get_current_tenant,
    get_current_tenant_group,
    get_current_user,
    set_current_all_accessible,
    set_current_membership,
    set_current_tenant,
    set_current_tenant_group,
)
from core.errors import (
    IntegrationAuthenticationError,
    IntegrationConfigurationError,
    IntegrationContext,
    IntegrationRequestError,
    IntegrationUnavailableError,
)

try:  # noqa: C901
    import ldap
    from django_auth_ldap.backend import LDAPBackend
    from django_auth_ldap.config import LDAPSearch

    django_auth_ldap_installed = True
except ImportError:
    django_auth_ldap_installed = False

    class DummyLDAP:
        SCOPE_BASE = 0
        SCOPE_ONELEVEL = 1
        SCOPE_SUBTREE = 2
        RES_SEARCH_ENTRY = 100
        OPT_REFERRALS = 2
        OPT_PROTOCOL_VERSION = 4

        class LDAPError(Exception):
            pass

        def initialize(self, *args, **kwargs):
            return DummyLDAPConnection()

    class DummyLDAPConnection:
        def set_option(self, *args, **kwargs):
            pass

        def simple_bind_s(self, *args, **kwargs):
            pass

        def search(self, *args, **kwargs):
            return 1

        def result(self, *args, **kwargs):
            return None, None

        def unbind_s(self):
            pass

    ldap = DummyLDAP()
    sys.modules["ldap"] = ldap

    class LDAPSearch:
        __slots__ = ("base_dn", "scope", "filterstr", "attrlist")

        def __init__(self, base_dn, scope, filterstr="(objectClass=*)", attrlist=None):
            self.base_dn = base_dn
            self.scope = scope
            self.filterstr = filterstr
            self.attrlist = attrlist

    class DummySettings:
        def __getattr__(self, name):
            return None

    class LDAPBackend:
        @property
        def settings(self):
            return DummySettings()

        def authenticate(self, request, username=None, password=None, **kwargs):
            return None

    from types import ModuleType

    django_auth_ldap = ModuleType("django_auth_ldap")
    backend_mod = ModuleType("django_auth_ldap.backend")
    config_mod = ModuleType("django_auth_ldap.config")

    django_auth_ldap.backend = backend_mod
    django_auth_ldap.config = config_mod

    sys.modules["django_auth_ldap"] = django_auth_ldap
    sys.modules["django_auth_ldap.backend"] = backend_mod
    sys.modules["django_auth_ldap.config"] = config_mod

    backend_mod.LDAPBackend = LDAPBackend
    config_mod.LDAPSearch = LDAPSearch


def _ldap_exception_types(*names):
    return tuple(error_type for name in names if isinstance((error_type := getattr(ldap, name, None)), type))


_LDAP_TRANSIENT_ERRORS = _ldap_exception_types("SERVER_DOWN", "TIMEOUT", "CONNECT_ERROR", "UNAVAILABLE", "BUSY")
_LDAP_AUTHENTICATION_ERRORS = _ldap_exception_types("INVALID_CREDENTIALS", "INSUFFICIENT_ACCESS")
_LDAP_CONFIGURATION_ERRORS = _ldap_exception_types("FILTER_ERROR", "PARAM_ERROR", "PROTOCOL_ERROR")
_LDAP_PROVIDER_ERROR = getattr(ldap, "LDAPError", Exception)


class LDAPConfigurationError(IntegrationConfigurationError, CommandError):
    code = "ldap.configuration"
    user_message = "LDAP configuration is incomplete or invalid."


class LDAPDependencyUnavailableError(LDAPConfigurationError):
    code = "ldap.dependency_unavailable"
    user_message = (
        "django-auth-ldap is unavailable. Use the locked Linux/WSL or Docker "
        "environment; native Windows does not support LDAP synchronization."
    )


class LDAPAuthenticationError(IntegrationAuthenticationError, CommandError):
    code = "ldap.authentication"
    user_message = "LDAP authentication or authorization failed."


class LDAPUnavailableError(IntegrationUnavailableError, CommandError):
    code = "ldap.unavailable"
    user_message = "The LDAP directory is temporarily unavailable; retry the operation later."


class LDAPRequestError(IntegrationRequestError, CommandError):
    code = "ldap.request_rejected"
    user_message = "The LDAP directory rejected the operation."


def classify_ldap_error(exc, *, context):
    """Map one python-ldap failure by SDK semantics without exposing its text."""
    if _LDAP_TRANSIENT_ERRORS and isinstance(exc, _LDAP_TRANSIENT_ERRORS):
        error_type = LDAPUnavailableError
    elif _LDAP_AUTHENTICATION_ERRORS and isinstance(exc, _LDAP_AUTHENTICATION_ERRORS):
        error_type = LDAPAuthenticationError
    elif _LDAP_CONFIGURATION_ERRORS and isinstance(exc, _LDAP_CONFIGURATION_ERRORS):
        error_type = LDAPConfigurationError
    else:
        error_type = LDAPRequestError
    error = error_type(context=context, cause_type=type(exc).__name__)
    error.__cause__ = exc
    return error


logger = logging.getLogger("django_auth_ldap")


@dataclass(frozen=True)
class _LDAPContextSnapshot:
    tenant: object | None
    membership: object | None
    tenant_group: object | None
    all_accessible: bool

    @classmethod
    def capture(cls):
        return cls(
            tenant=get_current_tenant(),
            membership=get_current_membership(),
            tenant_group=get_current_tenant_group(),
            all_accessible=get_current_all_accessible(),
        )

    def restore(self):
        set_current_tenant(self.tenant)
        set_current_membership(self.membership)
        set_current_tenant_group(self.tenant_group)
        set_current_all_accessible(self.all_accessible)


class TenantLDAPSettings:
    """
    A dynamic settings wrapper for django-auth-ldap.
    It intercepts queries from the LDAPBackend settings property and returns
    tenant-specific variables if they exist in the tenant configuration dict.
    """

    def __init__(self, config):
        self._config = config

    def __getattr__(self, name):  # noqa: C901
        # Resolve config lookup (check both UPPERCASE and lowercase keys)
        val = self._config.get(name)
        if val is None:
            val = self._config.get(name.lower())

        # If the parameter is a search base, we need to return an LDAPSearch instance
        if name in ("USER_SEARCH", "GROUP_SEARCH"):
            if val and isinstance(val, dict):
                base_dn = val.get("base_dn") or val.get("base") or ""
                filter_str = val.get("filter") or "(uid=%(user)s)"
                scope_str = val.get("scope") or "SUBTREE"

                scope = ldap.SCOPE_SUBTREE
                if scope_str.upper() == "BASE":
                    scope = ldap.SCOPE_BASE
                elif scope_str.upper() == "ONELEVEL":
                    scope = ldap.SCOPE_ONELEVEL
                return LDAPSearch(base_dn, scope, filter_str)
            elif val and isinstance(val, (list, tuple)):
                base_dn = val[0]
                filter_str = val[2] if len(val) > 2 else "(uid=%(user)s)"
                return LDAPSearch(base_dn, ldap.SCOPE_SUBTREE, filter_str)

        # Handle specific groups config instantiation if group_type is defined
        if name == "GROUP_TYPE" and val:
            # val could be a string representing the class name in django_auth_ldap.config
            # e.g., 'GroupOfNamesType', 'PosixGroupType', etc.
            try:
                # inline import: optional-dependency: django-auth-ldap is excluded on
                # native Windows (python-ldap has no wheel there).
                from django_auth_ldap import config as ldap_config

                if hasattr(ldap_config, val):
                    return getattr(ldap_config, val)()
            except ImportError:
                return None

        # If we got a value from the configuration dictionary, return it
        if val is not None:
            if name in ("OPT_REFERRALS", "OPT_PROTOCOL_VERSION", "OPT_NETWORK_TIMEOUT"):
                return int(val)
            return val

        # Fall back to global settings
        global_name = f"AUTH_LDAP_{name}"
        return getattr(settings, global_name, None)


def _ldap_context(operation):
    tenant = get_current_tenant()
    actor = get_current_user()
    request_id = get_current_request_id()
    return IntegrationContext(
        provider="ldap",
        operation=operation,
        tenant_id=getattr(tenant, "pk", None),
        actor_id=getattr(actor, "pk", None),
        request_id=str(request_id) if request_id else None,
    )


def _normalize_ldap_text(value):
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if value is None:
        return None
    return str(value)


def _read_ldap_attributes(user):
    email = getattr(user, "email", None) or None
    first_name = getattr(user, "first_name", None) or "LDAP"
    last_name = getattr(user, "last_name", None) or "User"
    upn = None
    ldap_user = getattr(user, "ldap_user", None)
    if ldap_user:
        try:
            attrs = ldap_user.attrs
            if attrs:
                upn = _normalize_ldap_text(attrs.get("userPrincipalName")) or _normalize_ldap_text(attrs.get("mail"))
                email = _normalize_ldap_text(attrs.get("mail")) or email
                first_name = _normalize_ldap_text(attrs.get("givenName")) or first_name
                last_name = _normalize_ldap_text(attrs.get("sn")) or last_name
        # broad except: boundary-isolation: LDAP attribute proxies expose provider-specific failures
        except Exception as exc:
            logger.warning(
                "LDAP attribute read failed",
                extra={
                    "source": "ldap",
                    "reason_code": "attribute_read_failed",
                    "user_id": getattr(user, "pk", None),
                    "tenant_id": getattr(get_current_tenant(), "pk", None),
                    "exception_type": type(exc).__name__,
                },
            )
    username = getattr(user, "username", "user")
    upn = upn or email or f"{username}@ldap"
    email = email or f"{username}@ldap.local"
    return email, upn, first_name, last_name


def _read_ldap_groups(user):
    ldap_user = getattr(user, "ldap_user", None)
    if not ldap_user:
        return []
    try:
        values = list(ldap_user.group_names)
    # broad except: boundary-isolation: LDAP group proxies expose provider-specific failures
    except Exception as exc:
        logger.debug(
            "LDAP group read failed; using alternate provider field",
            extra={
                "source": "ldap",
                "reason_code": "group_names_unavailable",
                "user_id": getattr(user, "pk", None),
                "tenant_id": getattr(get_current_tenant(), "pk", None),
                "exception_type": type(exc).__name__,
            },
        )
        try:
            values = list(ldap_user.group_dns)
        # broad except: boundary-isolation: LDAP group proxies expose provider-specific failures
        except Exception as exc:
            logger.warning(
                "LDAP group read failed",
                extra={
                    "source": "ldap",
                    "reason_code": "group_read_failed",
                    "user_id": getattr(user, "pk", None),
                    "tenant_id": getattr(get_current_tenant(), "pk", None),
                    "exception_type": type(exc).__name__,
                },
            )
            return []
    try:
        return [text for value in values if (text := _normalize_ldap_text(value))]
    # broad except: boundary-isolation: LDAP group values may be provider-specific bytes
    except Exception as exc:
        logger.warning(
            "LDAP group normalization failed",
            extra={
                "source": "ldap",
                "reason_code": "group_normalization_failed",
                "user_id": getattr(user, "pk", None),
                "tenant_id": getattr(get_current_tenant(), "pk", None),
                "exception_type": type(exc).__name__,
            },
        )
        return []


def _resolve_ldap_role_name(tenant, groups):
    tenant_configs = getattr(settings, "ITAMBOX_TENANT_LDAP_CONFIGS", {})
    if not isinstance(tenant_configs, dict):
        return "Member"
    tenant_config = tenant_configs.get(getattr(tenant, "slug", ""), {})
    group_role_mapping = tenant_config.get("LDAP_GROUP_ROLE_MAPPING", {}) if isinstance(tenant_config, dict) else {}
    mapped_roles = []
    for group in groups:
        mapped_role = group_role_mapping.get(group) if isinstance(group_role_mapping, dict) else None
        if isinstance(mapped_role, str):
            mapped_roles.append(mapped_role.lower())
    for priority_role in ("admin", "manager", "member"):
        if priority_role in mapped_roles:
            return {"admin": "Admin", "manager": "Manager", "member": "Member"}[priority_role]
    return "Member"


class MultiTenantLDAPBackend(LDAPBackend):
    """
    A custom authentication backend for django-auth-ldap.
    It overrides the standard settings retrieval process to return tenant-specific
    LDAP credentials and search properties depending on the thread-local active tenant.
    """

    @property
    def settings(self):
        tenant = get_current_tenant()
        if not tenant:
            return super().settings

        tenant_configs = getattr(settings, "ITAMBOX_TENANT_LDAP_CONFIGS", {})
        if not isinstance(tenant_configs, dict):
            return super().settings
        tenant_config = tenant_configs.get(tenant.slug)
        if not isinstance(tenant_config, dict):
            return super().settings

        return TenantLDAPSettings(tenant_config)

    def _is_configured(self):
        """True only when a usable LDAP config is resolvable for the current
        context. Without USER_SEARCH or USER_DN_TEMPLATE, django-auth-ldap raises
        ImproperlyConfigured during authentication — which, for a backend in the
        chain, would abort an ordinary password login. We treat "no config" as
        "not my job" and let the next backend handle it."""
        try:
            s = self.settings
            return bool(getattr(s, "USER_SEARCH", None) or getattr(s, "USER_DN_TEMPLATE", None))
        except ImproperlyConfigured:
            return False

    def _infer_tenant_from_username(self, username):
        if not username or "@" not in username:
            return None
        domain = username.rsplit("@", 1)[1].strip().lower()
        if not domain:
            return None
        tenant_model = tenant_scope.tenant_model()
        manager = getattr(tenant_model, "_base_manager", tenant_model.objects)
        candidates = [domain.split(".", 1)[0], domain]
        seen = set()
        for slug in candidates:
            if not slug or slug in seen:
                continue
            seen.add(slug)
            try:
                return manager.get(slug=slug, deleted_at__isnull=True)
            except tenant_model.DoesNotExist:
                continue
        return None

    def _provision_ldap_identity(self, user, tenant):
        email, upn, first_name, last_name = _read_ldap_attributes(user)
        role_name = _resolve_ldap_role_name(tenant, _read_ldap_groups(user))
        command = identity_provisioning.ExternalIdentityProvisioningCommand(
            user=user,
            customer_tenant=tenant,
            profile=identity_provisioning.ExternalIdentityProfile(
                source="LDAP",
                email=email,
                upn=upn,
                first_name=first_name,
                last_name=last_name,
            ),
            customer_role_name=role_name,
        )
        return identity_provisioning.provision_external_identity(command)

    def authenticate(self, request, username=None, password=None, **kwargs):  # noqa: C901
        previous_context = _LDAPContextSnapshot.capture()
        successful = False
        try:
            # The optional dependency fallback is capability removal, not degraded
            # authorization: this must never authenticate when python-ldap is
            # unavailable. Other configured backends make their own independent decision.
            if not django_auth_ldap_installed:
                return None

            if not get_current_tenant() and username and "@" in username:
                tenant = self._infer_tenant_from_username(username)
                if tenant is not None:
                    set_current_tenant(tenant)

            # No LDAP configured for this tenant or globally — skip rather than raise,
            # so password (and SSO) backends further down the chain can authenticate.
            if not self._is_configured():
                return None

            try:
                user = super().authenticate(request, username, password, **kwargs)
            except ImproperlyConfigured as exc:
                logger.debug(
                    "LDAP backend skipped",
                    extra={
                        "source": "ldap",
                        "reason_code": "not_configured",
                        "exception_type": type(exc).__name__,
                    },
                )
                return None
            except _LDAP_PROVIDER_ERROR as exc:
                raise classify_ldap_error(exc, context=_ldap_context("authenticate")) from exc

            if user is None:
                return None
            # ``can_login=False`` bars all interactive login, including LDAP/SSO.
            if not getattr(user, "can_login", True):
                return None
            tenant = get_current_tenant()
            if tenant is not None:
                self._provision_ldap_identity(user, tenant)
            successful = True
            return user
        finally:
            if not successful:
                previous_context.restore()

    def has_perm(self, user_obj, perm, obj=None):
        return False

    def has_module_perms(self, user_obj, app_label):
        return False

    def get_group_permissions(self, user_obj, obj=None):
        return set()

    def get_all_permissions(self, user_obj, obj=None):
        return set()
