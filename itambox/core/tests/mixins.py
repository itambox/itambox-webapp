from contextlib import contextmanager
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from core.managers import set_current_membership, set_current_tenant
from core.mfa import role_is_privileged
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant

User = get_user_model()


def grant(user, tenant, role, reach='own', granted_by=None,
          managed_scope=None, scope_group=None, assigned_tenants=None):
    """Test helper creating a Membership-backed canonical RoleGrant."""
    membership, _ = Membership.objects.get_or_create(user=user, tenant=tenant)
    metadata = {'granted_by': granted_by}
    if role_is_privileged(role):
        metadata.update({
            'reason': 'Test fixture grant',
            'valid_until': timezone.now() + timedelta(days=365),
        })
    role_grant = RoleGrant.objects.create(
        membership=membership,
        role=role,
        **metadata,
    )
    if reach == 'own':
        RoleGrantScope.objects.create(
            role_grant=role_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
    elif managed_scope in ('all', RoleGrantScope.SCOPE_ALL_MANAGED):
        RoleGrantScope.objects.create(
            role_grant=role_grant,
            scope_type=RoleGrantScope.SCOPE_ALL_MANAGED,
        )
    elif managed_scope == RoleGrantScope.SCOPE_TENANT_GROUP:
        RoleGrantScope.objects.create(
            role_grant=role_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT_GROUP,
            tenant_group=scope_group,
        )
    else:
        RoleGrantScope.objects.bulk_create([
            RoleGrantScope(
                role_grant=role_grant,
                scope_type=RoleGrantScope.SCOPE_TENANT,
                tenant=target,
            )
            for target in (assigned_tenants or [])
        ])
    return role_grant


class TenantTestMixin:
    grant = staticmethod(grant)

    def setup_tenant_context(self, name='Test Tenant', slug='test-tenant', permissions=None):
        if permissions is None:
            permissions = []
        self.tenant = Tenant.objects.create(name=name, slug=slug)
        self.tenant_user = User.objects.create_user(
            username=f'user_{slug}',
            email=f'user_{slug}@example.com',
            password='password',
        )
        self.tenant_admin = User.objects.create_superuser(
            username=f'admin_{slug}',
            email=f'admin_{slug}@example.com',
            password='password',
        )
        self.tenant_role = Role.objects.create(
            tenant=self.tenant,
            name='Test Role',
            permissions=permissions,
        )
        self.tenant_grant = grant(self.tenant_user, self.tenant, self.tenant_role)
        self.tenant_assignment = self.tenant_grant
        self.tenant_membership = self.tenant_grant.membership

    def set_active_tenant(self, tenant, membership=None):
        set_current_tenant(tenant)
        set_current_membership(membership)

    def clear_tenant_context(self):
        set_current_tenant(None)
        set_current_membership(None)

    @contextmanager
    def tenant_context(self, tenant, membership=None):
        old_tenant = set_current_tenant(tenant)
        old_membership = set_current_membership(membership)
        try:
            yield
        finally:
            set_current_tenant(old_tenant)
            set_current_membership(old_membership)

    def client_login_to_tenant(self, user, tenant, role_permissions=None):
        self.client.force_login(user)
        session = self.client.session
        session['active_tenant_id'] = tenant.pk
        session.save()

        if not user.is_superuser:
            membership = Membership.objects.filter(user=user, tenant=tenant).first()
            if not membership and role_permissions is not None:
                role = Role.objects.create(
                    tenant=tenant,
                    name='Dynamic Role',
                    permissions=role_permissions,
                )
                membership = grant(user, tenant, role).membership
            self.set_active_tenant(tenant, membership)
        else:
            self.set_active_tenant(tenant, None)
