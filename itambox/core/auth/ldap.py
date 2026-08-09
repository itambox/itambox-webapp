import logging
import sys

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import CommandError

from core.errors import (
    IntegrationAuthenticationError,
    IntegrationConfigurationError,
    IntegrationRequestError,
    IntegrationUnavailableError,
)
from core.managers import get_current_tenant, set_current_tenant

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


class LDAPConfigurationError(IntegrationConfigurationError, CommandError):
    code = "ldap.configuration"
    user_message = "LDAP configuration is incomplete or invalid."


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
        tenant_config = tenant_configs.get(tenant.slug)
        if not tenant_config:
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

    def authenticate(self, request, username=None, password=None, **kwargs):  # noqa: C901
        # The optional dependency fallback is capability removal, not degraded
        # authorization: this backend must never authenticate when python-ldap is
        # unavailable. Other configured backends make their own independent decision.
        if not django_auth_ldap_installed:
            return None
        # Resolve active tenant from UPN/email suffix if not already set
        if not get_current_tenant() and username and "@" in username:
            parts = username.split("@")
            domain = parts[-1].strip().lower()
            from organization.models import Tenant

            try:
                slug_candidate = domain.split(".")[0]
                tenant = Tenant.objects.get(slug=slug_candidate)
                set_current_tenant(tenant)
            except Tenant.DoesNotExist:
                # Also try direct domain/slug match
                try:
                    tenant = Tenant.objects.get(slug=domain)
                    set_current_tenant(tenant)
                except Tenant.DoesNotExist:
                    tenant = None

        # No LDAP configured for this tenant or globally — skip rather than raise,
        # so password (and SSO) backends further down the chain can authenticate.
        if not self._is_configured():
            return None

        try:
            user = super().authenticate(request, username, password, **kwargs)
        except ImproperlyConfigured as e:
            logger.debug("LDAP backend skipped — not configured: %s", e)
            return None
        # ``can_login=False`` bars all interactive login, including LDAP/SSO.
        if user and not getattr(user, "can_login", True):
            return None
        if user:
            self.sync_ldap_user_profile_and_memberships(user)
        return user

    def sync_ldap_user_profile_and_memberships(self, user):  # noqa: C901
        tenant = get_current_tenant()
        if not tenant:
            return

        from django.db import transaction
        from django.db.utils import IntegrityError

        from organization.models import AssetHolder

        # 1. Profile Provisioning / Linking
        upn = None
        email = user.email
        first_name = user.first_name or "LDAP"
        last_name = user.last_name or "User"

        # If django_auth_ldap is active, we can extract attributes from user.ldap_user
        if hasattr(user, "ldap_user") and user.ldap_user:
            try:
                ldap_attrs = user.ldap_user.attrs
                if ldap_attrs:

                    def get_attr(name):
                        val = ldap_attrs.get(name)
                        if val:
                            if isinstance(val, list):
                                val = val[0]
                            if isinstance(val, bytes):
                                return val.decode("utf-8")
                            return str(val)
                        return None

                    upn = get_attr("userPrincipalName") or get_attr("mail")
                    email = get_attr("mail") or email
                    first_name = get_attr("givenName") or first_name
                    last_name = get_attr("sn") or last_name
            # broad except: boundary-isolation: LDAP attribute proxies expose provider-specific failures
            except Exception as exc:
                logger.warning(
                    "Could not read LDAP attributes for user_id=%s tenant_id=%s exception_type=%s",
                    user.pk,
                    tenant.pk,
                    type(exc).__name__,
                )

        if not upn:
            upn = email or f"{user.username}@ldap"
        if not email:
            email = f"{user.username}@ldap.local"

        # Check if the user already has a linked profile in the target tenant
        holder = user.asset_holder_profiles.filter(tenant=tenant).first()
        if not holder:
            if upn:
                holder = AssetHolder.objects.filter(tenant=tenant, upn=upn).first()
            if not holder and email:
                holder = AssetHolder.objects.filter(tenant=tenant, email=email).first()

            if holder and holder.user is None:
                holder.user = user
                try:
                    with transaction.atomic():
                        holder.save()
                except IntegrityError as e:
                    logger.warning(f"IntegrityError while saving AssetHolder: {e}")
                    holder = None
            elif not holder or (holder and holder.user != user):
                try:
                    with transaction.atomic():
                        holder = AssetHolder.objects.create(
                            user=user, first_name=first_name, last_name=last_name, upn=upn, email=email, tenant=tenant
                        )
                except IntegrityError as e:
                    logger.warning(f"IntegrityError while creating AssetHolder: {e}")
                    holder = None

        # 2. Membership & Role Syncing
        groups = []
        if hasattr(user, "ldap_user") and user.ldap_user:
            try:
                groups = list(user.ldap_user.group_names)
            # broad except: boundary-isolation: LDAP group proxies expose provider-specific failures
            except Exception as exc:
                logger.debug(
                    "LDAP group_names unavailable for user_id=%s tenant_id=%s; trying group_dns (exception_type=%s)",
                    user.pk,
                    tenant.pk,
                    type(exc).__name__,
                )
                try:
                    groups = list(user.ldap_user.group_dns)
                # broad except: boundary-isolation: LDAP group proxies expose provider-specific failures
                except Exception as exc:
                    logger.warning(
                        "Could not read LDAP groups for user_id=%s tenant_id=%s exception_type=%s",
                        user.pk,
                        tenant.pk,
                        type(exc).__name__,
                    )

        tenant_configs = getattr(settings, "ITAMBOX_TENANT_LDAP_CONFIGS", {})
        tenant_config = tenant_configs.get(tenant.slug, {})
        group_role_mapping = tenant_config.get("LDAP_GROUP_ROLE_MAPPING", {})

        user_roles = []
        for group in groups:
            if group in group_role_mapping:
                mapped_role = group_role_mapping[group]
                if isinstance(mapped_role, str):
                    user_roles.append(mapped_role.lower())

        resolved_role_name = None
        for priority_role in ["admin", "manager", "member"]:
            if priority_role in user_roles:
                resolved_role_name = priority_role
                break

        if not resolved_role_name:
            resolved_role_name = "member"

        role_title_map = {"admin": "Admin", "manager": "Manager", "member": "Member"}
        db_role_name = role_title_map.get(resolved_role_name, "Member")

        # Safe JIT provisioning: never auto-create a privileged role from a group
        # claim; assign Admin/Manager only if the operator created them deliberately.
        from core.auth.provisioning import provision_membership

        provision_membership(user, tenant, db_role_name, self.get_permissions_for_role, "LDAP")

    def get_permissions_for_role(self, role_name):
        from django.contrib.auth.models import Permission

        from organization.forms.role_form import MATRIX_MODELS

        perms = set()
        for _key, info in MATRIX_MODELS.items():
            app = info["app"]
            model = info["model_name"]
            if role_name == "Admin":
                perms.update(
                    [f"{app}.view_{model}", f"{app}.add_{model}", f"{app}.change_{model}", f"{app}.delete_{model}"]
                )
            elif role_name in ("Manager", "Member"):
                perms.update([f"{app}.view_{model}", f"{app}.add_{model}", f"{app}.change_{model}"])
        perms.update(
            ["extras.view_dashboard", "extras.change_dashboard", "extras.add_dashboard", "extras.delete_dashboard"]
        )
        all_codenames = set(
            f"{p.content_type.app_label}.{p.codename}" for p in Permission.objects.select_related("content_type").all()
        )
        return list(perms & all_codenames)

    def has_perm(self, user_obj, perm, obj=None):
        return False

    def has_module_perms(self, user_obj, app_label):
        return False

    def get_group_permissions(self, user_obj, obj=None):
        return set()

    def get_all_permissions(self, user_obj, obj=None):
        return set()
