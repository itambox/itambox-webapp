import logging
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from mozilla_django_oidc.auth import OIDCAuthenticationBackend
from mozilla_django_oidc.utils import import_from_settings
from mozilla_django_oidc.views import OIDCAuthenticationRequestView, OIDCAuthenticationCallbackView
from core.managers import get_current_tenant, set_current_tenant

logger = logging.getLogger(__name__)


class TenantOIDCSettingsMixin:
    """Shared mixin providing tenant-aware OIDC settings resolution.

    Used by TenantOIDCBackend, TenantOIDCAuthorizeView, and
    TenantOIDCCallbackView to avoid duplicating get_settings() logic.
    """

    @classmethod
    def get_settings(cls, attr, *args):
        tenant = get_current_tenant()
        if tenant:
            tenant_configs = getattr(settings, 'ITAMBOX_TENANT_OIDC_CONFIGS', {})
            tenant_config = tenant_configs.get(tenant.slug)
            if tenant_config and attr in tenant_config:
                return tenant_config[attr]
            if tenant_config and attr.lower() in tenant_config:
                return tenant_config[attr.lower()]

        # Defaults for algorithm and scopes
        if attr == 'OIDC_RP_SIGN_ALGO':
            return 'RS256'
        elif attr == 'OIDC_RP_SCOPES':
            return 'openid email profile'

        # Fallback to global django settings
        try:
            return import_from_settings(attr, *args)
        except ImproperlyConfigured:
            tenant_slug = tenant.slug if tenant else '<unknown>'
            raise ImproperlyConfigured(
                f"OIDC not configured for tenant: {tenant_slug} "
                f"(missing setting: {attr})"
            )

    def __getattr__(self, name):
        if name.startswith('OIDC_'):
            return self.get_settings(name)
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

class TenantOIDCBackend(TenantOIDCSettingsMixin, OIDCAuthenticationBackend):
    def __init__(self, *args, **kwargs):
        # Do not call super().__init__() because it assigns settings statically
        self.UserModel = get_user_model()

    def filter_users_by_claims(self, claims):
        email = claims.get('email')
        if email:
            users = self.UserModel.objects.filter(email__iexact=email)
            if users.exists():
                return users

        username = self.get_username(claims)
        if username:
            return self.UserModel.objects.filter(username=username)

        return self.UserModel.objects.none()

    def get_username(self, claims):
        email = claims.get('email')
        sub = claims.get('sub')
        username = email or sub or 'oidc_user'
        return username

    def create_user(self, claims):
        email = claims.get('email')
        base_username = self.get_username(claims)

        username = base_username
        counter = 1
        while self.UserModel.objects.filter(username=username).exists():
            username = f"{base_username}_{counter}"
            counter += 1

        first_name = claims.get('given_name') or claims.get('first_name', '')
        last_name = claims.get('family_name') or claims.get('last_name', '')

        user = self.UserModel.objects.create_user(
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name
        )
        self.sync_user_profile_and_memberships(user, claims)
        return user

    def update_user(self, user, claims):
        email = claims.get('email')
        first_name = claims.get('given_name') or claims.get('first_name', '')
        last_name = claims.get('family_name') or claims.get('last_name', '')

        if email:
            user.email = email
        if first_name:
            user.first_name = first_name
        if last_name:
            user.last_name = last_name
        user.save()

        self.sync_user_profile_and_memberships(user, claims)
        return user

    def sync_user_profile_and_memberships(self, user, claims):
        tenant = get_current_tenant()
        if not tenant:
            return

        from organization.models import AssetHolder, TenantRole, TenantMembership
        from django.db.utils import IntegrityError

        # 1. Profile Provisioning / Linking
        upn = claims.get('upn') or claims.get('email') or user.email
        email = claims.get('email') or user.email

        # Check if the user already has a linked profile
        if hasattr(user, 'asset_holder_profile') and user.asset_holder_profile is not None:
            holder = user.asset_holder_profile
            if holder.tenant != tenant:
                logger.warning("User already has an AssetHolder profile linked in another tenant. Cannot create a new one due to OneToOneField constraint.")
        else:
            holder = None
            if upn:
                holder = AssetHolder.objects.filter(tenant=tenant, upn=upn).first()
            if not holder and email:
                holder = AssetHolder.objects.filter(tenant=tenant, email=email).first()

            from django.db import transaction

            if holder and holder.user is None:
                holder.user = user
                try:
                    with transaction.atomic():
                        holder.save()
                except IntegrityError as e:
                    logger.warning(f"IntegrityError while saving AssetHolder: {e}")
                    holder = None
            elif not holder or (holder and holder.user != user):
                first_name = claims.get('given_name') or claims.get('first_name') or user.first_name or 'OIDC'
                last_name = claims.get('family_name') or claims.get('last_name') or user.last_name or 'User'

                try:
                    with transaction.atomic():
                        holder = AssetHolder.objects.create(
                            user=user,
                            first_name=first_name,
                            last_name=last_name,
                            upn=upn or email or f"{user.username}@oidc",
                            email=email,
                            tenant=tenant
                        )
                except IntegrityError as e:
                    logger.warning(f"IntegrityError while creating AssetHolder: {e}")
                    holder = None

        # 2. TenantMembership & Role Syncing
        groups_claim = claims.get('groups', [])
        if isinstance(groups_claim, str):
            groups_claim = [groups_claim]
        elif not isinstance(groups_claim, list):
            groups_claim = []

        tenant_configs = getattr(settings, 'ITAMBOX_TENANT_OIDC_CONFIGS', {})
        tenant_config = tenant_configs.get(tenant.slug, {})
        group_role_mapping = tenant_config.get('OIDC_GROUP_ROLE_MAPPING', {})

        user_roles = []
        for group in groups_claim:
            if group in group_role_mapping:
                mapped_role = group_role_mapping[group]
                if isinstance(mapped_role, str):
                    user_roles.append(mapped_role.lower())

        # Resolve the highest priority role
        resolved_role_name = None
        for priority_role in ['admin', 'manager', 'member']:
            if priority_role in user_roles:
                resolved_role_name = priority_role
                break

        if not resolved_role_name:
            resolved_role_name = 'member'

        # Map to proper title-cased name for DB
        role_title_map = {
            'admin': 'Admin',
            'manager': 'Manager',
            'member': 'Member'
        }
        db_role_name = role_title_map.get(resolved_role_name, 'Member')

        # Provision role if it doesn't exist
        role, created = TenantRole.objects.get_or_create(
            tenant=tenant,
            name=db_role_name,
            defaults={
                'description': f'Auto-provisioned {db_role_name} role via OIDC',
                'permissions': self.get_permissions_for_role(db_role_name)
            }
        )

        # Sync membership
        TenantMembership.objects.update_or_create(
            user=user,
            tenant=tenant,
            defaults={'role': role}
        )

    def get_permissions_for_role(self, role_name):
        from organization.forms.tenantrole_form import MATRIX_MODELS
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
        # Add dashboard / extra permissions
        perms.update([
            'extras.view_dashboard',
            'extras.change_dashboard',
            'extras.add_dashboard',
            'extras.delete_dashboard'
        ])
        # Filter to only valid db permissions
        all_codenames = set(
            f"{p.content_type.app_label}.{p.codename}"
            for p in Permission.objects.select_related('content_type').all()
        )
        return list(perms & all_codenames)


class TenantOIDCAuthorizeView(TenantOIDCSettingsMixin, OIDCAuthenticationRequestView):

    def __init__(self, *args, **kwargs):
        # Bypass parent oidc request view static settings lookup in init
        super(OIDCAuthenticationRequestView, self).__init__(*args, **kwargs)

    @property
    def OIDC_OP_AUTH_ENDPOINT(self):
        return self.get_settings("OIDC_OP_AUTHORIZATION_ENDPOINT")

    @OIDC_OP_AUTH_ENDPOINT.setter
    def OIDC_OP_AUTH_ENDPOINT(self, value):
        pass

    @property
    def OIDC_RP_CLIENT_ID(self):
        return self.get_settings("OIDC_RP_CLIENT_ID")

    @OIDC_RP_CLIENT_ID.setter
    def OIDC_RP_CLIENT_ID(self, value):
        pass

    def dispatch(self, request, *args, **kwargs):
        tenant_slug = kwargs.pop('tenant_slug', None) or request.GET.get('tenant')
        from organization.models import Tenant
        tenant = None
        if tenant_slug:
            try:
                tenant = Tenant.objects.get(slug=tenant_slug)
                request.session['oidc_tenant_slug'] = tenant.slug
            except Tenant.DoesNotExist:
                from django.http import Http404
                raise Http404(f"Tenant '{tenant_slug}' does not exist.")

        if not tenant:
            sess_tenant_slug = request.session.get('oidc_tenant_slug')
            if sess_tenant_slug:
                try:
                    tenant = Tenant.objects.get(slug=sess_tenant_slug)
                except Tenant.DoesNotExist:
                    pass

        if tenant:
            from core.managers import set_current_tenant
            set_current_tenant(tenant)

        return super().dispatch(request, *args, **kwargs)


class TenantOIDCCallbackView(TenantOIDCSettingsMixin, OIDCAuthenticationCallbackView):

    def dispatch(self, request, *args, **kwargs):
        tenant_slug = request.session.get('oidc_tenant_slug')
        if tenant_slug:
            from organization.models import Tenant
            try:
                tenant = Tenant.objects.get(slug=tenant_slug)
                from core.managers import set_current_tenant
                set_current_tenant(tenant)
            except Tenant.DoesNotExist:
                pass
        return super().dispatch(request, *args, **kwargs)

    def login_success(self):
        tenant_slug = self.request.session.get('oidc_tenant_slug')
        if tenant_slug:
            from organization.models import Tenant
            try:
                tenant = Tenant.objects.get(slug=tenant_slug)
                self.request.session['active_tenant_id'] = tenant.pk
            except Tenant.DoesNotExist:
                pass
        return super().login_success()
