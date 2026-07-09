from contextlib import contextmanager
from django.contrib.auth import get_user_model
from core.managers import set_current_tenant, set_current_membership
from organization.models import Tenant, Membership, Role

User = get_user_model()

class TenantTestMixin:
    """
    Mixin for Django TestCase classes to easily manage tenant-scoped authentication,
    RBAC contexts, and session variables in testing.
    """

    def setup_tenant_context(self, name="Test Tenant", slug="test-tenant", permissions=None):
        """
        Creates a default tenant, a standard user, and an admin user.
        Binds the standard user to a membership role with the specified permissions.
        """
        if permissions is None:
            permissions = []

        self.tenant = Tenant.objects.create(name=name, slug=slug)
        self.tenant_user = User.objects.create_user(username=f"user_{slug}", email=f"user_{slug}@example.com", password="password")
        self.tenant_admin = User.objects.create_superuser(username=f"admin_{slug}", email=f"admin_{slug}@example.com", password="password")

        self.tenant_role = Role.objects.create(
            tenant=self.tenant,
            name="Test Role",
            permissions=permissions
        )
        self.tenant_membership = Membership.objects.create(user=self.tenant_user,
            tenant=self.tenant,
        )
        self.tenant_membership.roles.add(self.tenant_role)

    def set_active_tenant(self, tenant, membership=None):
        """
        Sets the thread-local current tenant and membership context.
        """
        set_current_tenant(tenant)
        set_current_membership(membership)

    def clear_tenant_context(self):
        """
        Clears the current tenant and membership context.
        """
        set_current_tenant(None)
        set_current_membership(None)

    @contextmanager
    def tenant_context(self, tenant, membership=None):
        """
        Context manager to run a block of code under a specific tenant context.
        """
        old_tenant = set_current_tenant(tenant)
        old_membership = set_current_membership(membership)
        try:
            yield
        finally:
            set_current_tenant(old_tenant)
            set_current_membership(old_membership)

    def client_login_to_tenant(self, user, tenant, role_permissions=None):
        """
        Logs the client in and configures the active_tenant_id session variable.
        Also sets the thread-local current tenant/membership context.
        """
        self.client.force_login(user)
        session = self.client.session
        session['active_tenant_id'] = tenant.pk
        session.save()

        # Find or create membership
        if not user.is_superuser:
            membership = Membership.objects.filter(user=user, tenant=tenant).first()
            if not membership and role_permissions is not None:
                role = Role.objects.create(
                    tenant=tenant,
                    name="Dynamic Role",
                    permissions=role_permissions
                )
                membership = Membership.objects.create(user=user,
                    tenant=tenant,
                )
                membership.roles.add(role)
            self.set_active_tenant(tenant, membership)
        else:
            self.set_active_tenant(tenant, None)
