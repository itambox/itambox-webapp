import logging
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, SuspiciousOperation
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

    def verify_token(self, token, **kwargs):
        """Verify the ID token and additionally enforce audience / issuer.

        mozilla_django_oidc validates the signature (rejecting alg downgrade to
        ``none``/HS256 via its alg-match check, with the RS256 default supplied
        by :class:`TenantOIDCSettingsMixin`) and the nonce, but it calls the JWT
        decoder with ``verify_aud=False`` and never checks the issuer. That
        leaves the RP open to token-substitution / confused-deputy attacks where
        a token minted for a *different* client of the same IdP — or by an
        unexpected issuer — is replayed here. Enforce both.
        """
        payload = super().verify_token(token, **kwargs)

        client_id = self.get_settings('OIDC_RP_CLIENT_ID')
        aud = payload.get('aud')
        aud_list = aud if isinstance(aud, list) else [aud]
        if client_id not in aud_list:
            raise SuspiciousOperation(
                'OIDC ID token audience does not match the configured client ID.'
            )

        # Per the spec, when present `azp` must identify this client.
        azp = payload.get('azp')
        if azp is not None and azp != client_id:
            raise SuspiciousOperation(
                'OIDC ID token authorized party (azp) does not match the client ID.'
            )

        # Issuer validation is MANDATORY. If the tenant config omits OIDC_OP_ISSUER,
        # authentication is rejected rather than accepting tokens from any issuer —
        # which would be an open door for token-substitution attacks across IdP clients.
        # Operators must configure OIDC_OP_ISSUER for every tenant that uses OIDC.
        expected_iss = self.get_settings('OIDC_OP_ISSUER', None)
        if not expected_iss:
            raise SuspiciousOperation(
                'OIDC issuer (OIDC_OP_ISSUER) is not configured for this tenant. '
                'Authentication denied to prevent token-substitution attacks. '
                'Set OIDC_OP_ISSUER in ITAMBOX_TENANT_OIDC_CONFIGS for this tenant.'
            )
        if payload.get('iss') != expected_iss:
            raise SuspiciousOperation(
                'OIDC ID token issuer does not match the expected issuer.'
            )

        return payload

    def has_perm(self, user_obj, perm, obj=None):
        return False

    def has_module_perms(self, user_obj, app_label):
        return False

    def get_group_permissions(self, user_obj, obj=None):
        return set()

    def get_all_permissions(self, user_obj, obj=None):
        return set()

    def authenticate(self, request, **kwargs):
        # ``can_login=False`` bars all interactive login, including OIDC/SSO. super() runs the
        # full OIDC code-exchange and user resolution; gate the resolved user post-hoc.
        user = super().authenticate(request, **kwargs)
        if user and not getattr(user, 'can_login', True):
            return None
        return user

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

        from organization.models import AssetHolder, Role, Membership
        from django.db.utils import IntegrityError

        # 1. Profile Provisioning / Linking
        upn = claims.get('upn') or claims.get('email') or user.email
        email = claims.get('email') or user.email

        # Check if the user already has a linked profile in the target tenant
        holder = user.asset_holder_profiles.filter(tenant=tenant).first()
        if not holder:
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

        # 2. Membership & Role Syncing
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

        # Safe JIT provisioning: never auto-create a privileged role from a group
        # claim; assign Admin/Manager only if the operator created them deliberately.
        from core.auth.provisioning import provision_membership
        provision_membership(user, tenant, db_role_name, self.get_permissions_for_role, 'OIDC')

        # 3. Provider-level (MSP staff) group claims: if this tenant belongs to a provider
        # and the provider's OIDC config maps any of the user's group claims to a
        # Role, (re)assign the corresponding Membership. No mapping / no
        # provider → no-op, so non-MSP installs are unaffected.
        if getattr(tenant, 'provider_id', None) and groups_claim:
            provider_configs = getattr(settings, 'ITAMBOX_PROVIDER_OIDC_CONFIGS', {})
            provider_config = provider_configs.get(tenant.provider.slug, {})
            provider_role_mapping = provider_config.get('OIDC_GROUP_PROVIDER_ROLE_MAPPING', {})
            for group in groups_claim:
                mapped_provider_role = provider_role_mapping.get(group)
                if mapped_provider_role:
                    from core.auth.provisioning import provision_provider_membership
                    provision_provider_membership(user, tenant.provider, mapped_provider_role, 'OIDC')
                    break

    def get_permissions_for_role(self, role_name):
        from organization.forms.role_form import MATRIX_MODELS
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
