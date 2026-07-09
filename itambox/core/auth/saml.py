import os
import saml2
from saml2.config import SPConfig
from django.conf import settings
from core.managers import get_current_tenant

def load_saml_config():
    """
    Dynamically constructs and returns the pysaml2 SPConfig object
    configured specifically for the active tenant context.
    """
    tenant = get_current_tenant()
    saml_configs = getattr(settings, 'ITAMBOX_TENANT_SAML_CONFIGS', {})

    tenant_config = None
    if tenant:
        tenant_config = saml_configs.get(tenant.slug)

    if not tenant_config:
        if 'default' in saml_configs:
            tenant_config = saml_configs['default']
        elif saml_configs:
            first_key = list(saml_configs.keys())[0]
            tenant_config = saml_configs[first_key]
        else:
            # Fallback metadata/entityid configuration to allow initialization
            tenant_config = {
                'entityid': 'https://itambox.local/saml2/metadata/',
                'metadata': {'local': []}
            }

    # Resolve active hosts/base URLs
    base_url = tenant_config.get('base_url')
    if not base_url:
        base_url = f"https://{tenant.slug if tenant else 'itambox'}.local"

    sp_config = {
        'entityid': tenant_config.get('entityid', f'{base_url}/saml2/metadata/'),
        'service': {
            'sp': {
                'name': 'ITAMbox SP',
                'endpoints': {
                    'assertion_consumer_service': [
                        (f'{base_url}/saml2/acs/', saml2.BINDING_HTTP_POST),
                    ],
                    'single_logout_service': [
                        (f'{base_url}/saml2/ls/', saml2.BINDING_HTTP_REDIRECT),
                    ],
                },
                # Secure by default: reject forged/unsigned assertions. A tenant may
                # explicitly relax these in its SAML config, but the defaults must be
                # safe so an unsigned, unsolicited assertion cannot mint an admin.
                'allow_unsolicited': tenant_config.get('allow_unsolicited', False),
                'authn_requests_signed': tenant_config.get('authn_requests_signed', False),
                'logout_requests_signed': tenant_config.get('logout_requests_signed', False),
                'want_assertions_signed': tenant_config.get('want_assertions_signed', True),
                'want_response_signed': tenant_config.get('want_response_signed', True),
            },
        },
        'metadata': tenant_config.get('metadata', {}),
        'debug': settings.DEBUG,
    }

    # Load and compile Saml2 SPConfig
    config = SPConfig()
    config.load(sp_config)
    return config


from djangosaml2.backends import Saml2Backend

class TenantSaml2Backend(Saml2Backend):
    """
    A custom SAML2 authentication backend that delegates authentication to djangosaml2
    but rejects all permissions checks (has_perm/has_module_perms) to prevent bypassing
    the custom multi-tenant RBAC system.
    """
    def authenticate(self, request, session_info=None, attribute_mapping=None, create_unknown_user=True, **kwargs):
        user = super().authenticate(request, session_info, attribute_mapping, create_unknown_user, **kwargs)
        # ``can_login=False`` bars ALL interactive login, including SSO (SSO backends do not
        # route through ModelBackend.user_can_authenticate).
        if user and not getattr(user, 'can_login', True):
            return None
        if user and session_info:
            self.sync_saml_user_profile_and_memberships(user, session_info)
        return user

    def sync_saml_user_profile_and_memberships(self, user, session_info):
        tenant = get_current_tenant()
        if not tenant:
            return

        from organization.models import AssetHolder, Role, Membership
        from django.db.utils import IntegrityError
        from django.db import transaction
        import logging

        logger = logging.getLogger('djangosaml2')
        ava = session_info.get('ava', {})
        
        def get_attr(keys):
            if isinstance(keys, str):
                keys = [keys]
            for key in keys:
                val = ava.get(key)
                if val:
                    if isinstance(val, list):
                        val = val[0]
                    if isinstance(val, bytes):
                        return val.decode('utf-8')
                    return str(val)
            return None

        email = get_attr(['email', 'mail', 'User.Email']) or user.email
        first_name = get_attr(['givenName', 'first_name', 'User.FirstName']) or user.first_name or 'SAML'
        last_name = get_attr(['sn', 'last_name', 'User.LastName']) or user.last_name or 'User'
        upn = get_attr(['upn', 'userPrincipalName', 'uid', 'nameidentifier']) or email

        if not upn:
            upn = email or f"{user.username}@saml"
        if not email:
            email = f"{user.username}@saml.local"

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

        groups = ava.get('groups') or ava.get('memberOf') or ava.get('User.Groups') or []
        if isinstance(groups, str):
            groups = [groups]
        elif not isinstance(groups, list):
            groups = []

        groups_cleaned = []
        for g in groups:
            if isinstance(g, bytes):
                groups_cleaned.append(g.decode('utf-8'))
            else:
                groups_cleaned.append(str(g))

        tenant_configs = getattr(settings, 'ITAMBOX_TENANT_SAML_CONFIGS', {})
        tenant_config = tenant_configs.get(tenant.slug, {})
        group_role_mapping = tenant_config.get('SAML_GROUP_ROLE_MAPPING', {})

        user_roles = []
        for group in groups_cleaned:
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

        # Safe JIT provisioning: never auto-create a privileged role from a group
        # claim; assign Admin/Manager only if the operator created them deliberately.
        from core.auth.provisioning import provision_membership
        provision_membership(user, tenant, db_role_name, self.get_permissions_for_role, 'SAML')

    def get_permissions_for_role(self, role_name):
        from organization.forms.role_form import MATRIX_MODELS
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

