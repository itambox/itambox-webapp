import logging
import sys
from django.conf import settings
from core.managers import get_current_tenant, set_current_tenant

try:
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
    sys.modules['ldap'] = ldap

    class LDAPSearch:
        def __init__(self, base_dn, scope, filter_format=None):
            self.base_dn = base_dn
            self.scope = scope
            self.filter_format = filter_format

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
    django_auth_ldap = ModuleType('django_auth_ldap')
    backend_mod = ModuleType('django_auth_ldap.backend')
    config_mod = ModuleType('django_auth_ldap.config')

    django_auth_ldap.backend = backend_mod
    django_auth_ldap.config = config_mod

    sys.modules['django_auth_ldap'] = django_auth_ldap
    sys.modules['django_auth_ldap.backend'] = backend_mod
    sys.modules['django_auth_ldap.config'] = config_mod

    backend_mod.LDAPBackend = LDAPBackend
    config_mod.LDAPSearch = LDAPSearch

logger = logging.getLogger('django_auth_ldap')


class TenantLDAPSettings:
    """
    A dynamic settings wrapper for django-auth-ldap.
    It intercepts queries from the LDAPBackend settings property and returns
    tenant-specific variables if they exist in the tenant configuration dict.
    """
    def __init__(self, config):
        self._config = config

    def __getattr__(self, name):
        # Resolve config lookup (check both UPPERCASE and lowercase keys)
        val = self._config.get(name)
        if val is None:
            val = self._config.get(name.lower())

        # If the parameter is a search base, we need to return an LDAPSearch instance
        if name in ('USER_SEARCH', 'GROUP_SEARCH'):
            if val and isinstance(val, dict):
                base_dn = val.get('base_dn') or val.get('base') or ''
                filter_str = val.get('filter') or '(uid=%(user)s)'
                scope_str = val.get('scope') or 'SUBTREE'
                
                scope = ldap.SCOPE_SUBTREE
                if scope_str.upper() == 'BASE':
                    scope = ldap.SCOPE_BASE
                elif scope_str.upper() == 'ONELEVEL':
                    scope = ldap.SCOPE_ONELEVEL
                return LDAPSearch(base_dn, scope, filter_str)
            elif val and isinstance(val, (list, tuple)):
                base_dn = val[0]
                filter_str = val[2] if len(val) > 2 else '(uid=%(user)s)'
                return LDAPSearch(base_dn, ldap.SCOPE_SUBTREE, filter_str)

        # Handle specific groups config instantiation if group_type is defined
        if name == 'GROUP_TYPE' and val:
            # val could be a string representing the class name in django_auth_ldap.config
            # e.g., 'GroupOfNamesType', 'PosixGroupType', etc.
            try:
                from django_auth_ldap import config as ldap_config
                if hasattr(ldap_config, val):
                    return getattr(ldap_config, val)()
            except ImportError:
                return None

        # If we got a value from the configuration dictionary, return it
        if val is not None:
            if name in ('OPT_REFERRALS', 'OPT_PROTOCOL_VERSION', 'OPT_NETWORK_TIMEOUT'):
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

        tenant_configs = getattr(settings, 'ITAMBOX_TENANT_LDAP_CONFIGS', {})
        tenant_config = tenant_configs.get(tenant.slug)
        if not tenant_config:
            return super().settings

        return TenantLDAPSettings(tenant_config)

    def authenticate(self, request, username=None, password=None, **kwargs):
        # Resolve active tenant from UPN/email suffix if not already set
        if not get_current_tenant() and username and '@' in username:
            parts = username.split('@')
            domain = parts[-1].strip().lower()
            from organization.models import Tenant
            try:
                slug_candidate = domain.split('.')[0]
                tenant = Tenant.objects.get(slug=slug_candidate)
                set_current_tenant(tenant)
            except Tenant.DoesNotExist:
                # Also try direct domain/slug match
                try:
                    tenant = Tenant.objects.get(slug=domain)
                    set_current_tenant(tenant)
                except Tenant.DoesNotExist:
                    pass

        user = super().authenticate(request, username, password, **kwargs)
        if user:
            self.sync_ldap_user_profile_and_memberships(user)
        return user

    def sync_ldap_user_profile_and_memberships(self, user):
        tenant = get_current_tenant()
        if not tenant:
            return

        from organization.models import AssetHolder, TenantRole, TenantMembership
        from django.db.utils import IntegrityError
        from django.db import transaction

        # 1. Profile Provisioning / Linking
        upn = None
        email = user.email
        first_name = user.first_name or 'LDAP'
        last_name = user.last_name or 'User'

        # If django_auth_ldap is active, we can extract attributes from user.ldap_user
        if hasattr(user, 'ldap_user') and user.ldap_user:
            try:
                ldap_attrs = user.ldap_user.attrs
                if ldap_attrs:
                    def get_attr(name):
                        val = ldap_attrs.get(name)
                        if val:
                            if isinstance(val, list):
                                val = val[0]
                            if isinstance(val, bytes):
                                return val.decode('utf-8')
                            return str(val)
                        return None
                    
                    upn = get_attr('userPrincipalName') or get_attr('mail')
                    email = get_attr('mail') or email
                    first_name = get_attr('givenName') or first_name
                    last_name = get_attr('sn') or last_name
            except Exception as e:
                logger.warning(f"Error reading LDAP attributes: {e}")

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
                            user=user,
                            first_name=first_name,
                            last_name=last_name,
                            upn=upn,
                            email=email,
                            tenant=tenant
                        )
                except IntegrityError as e:
                    logger.warning(f"IntegrityError while creating AssetHolder: {e}")
                    holder = None

        # 2. TenantMembership & Role Syncing
        groups = []
        if hasattr(user, 'ldap_user') and user.ldap_user:
            try:
                groups = list(user.ldap_user.group_names)
            except Exception:
                try:
                    groups = list(user.ldap_user.group_dns)
                except Exception:
                    pass

        tenant_configs = getattr(settings, 'ITAMBOX_TENANT_LDAP_CONFIGS', {})
        tenant_config = tenant_configs.get(tenant.slug, {})
        group_role_mapping = tenant_config.get('LDAP_GROUP_ROLE_MAPPING', {})

        user_roles = []
        for group in groups:
            if group in group_role_mapping:
                mapped_role = group_role_mapping[group]
                if isinstance(mapped_role, str):
                    user_roles.append(mapped_role.lower())

        resolved_role_name = None
        for priority_role in ['admin', 'manager', 'member']:
            if priority_role in user_roles:
                resolved_role_name = priority_role
                break

        if not resolved_role_name:
            resolved_role_name = 'member'

        role_title_map = {
            'admin': 'Admin',
            'manager': 'Manager',
            'member': 'Member'
        }
        db_role_name = role_title_map.get(resolved_role_name, 'Member')

        role, created = TenantRole.objects.get_or_create(
            tenant=tenant,
            name=db_role_name,
            defaults={
                'description': f'Auto-provisioned {db_role_name} role via LDAP',
                'permissions': self.get_permissions_for_role(db_role_name)
            }
        )

        TenantMembership.objects.update_or_create(
            user=user,
            tenant=tenant,
            defaults={'role': role}
        )

    def get_permissions_for_role(self, role_name):
        from organization.forms.tenantrole_form import MATRIX_MODELS
        from django.contrib.auth.models import Permission
        perms = set()
        for key, info in MATRIX_MODELS.items():
            app = info['app']
            model = info['model_name']
            if role_name == 'Admin':
                perms.update([
                    f"{app}.view_{model}",
                    f"{app}.add_{model}",
                    f"{app}.change_{model}",
                    f"{app}.delete_{model}"
                ])
            elif role_name in ('Manager', 'Member'):
                perms.update([
                    f"{app}.view_{model}",
                    f"{app}.add_{model}",
                    f"{app}.change_{model}"
                ])
        perms.update([
            'extras.view_dashboard',
            'extras.change_dashboard',
            'extras.add_dashboard',
            'extras.delete_dashboard'
        ])
        all_codenames = set(
            f"{p.content_type.app_label}.{p.codename}"
            for p in Permission.objects.select_related('content_type').all()
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

