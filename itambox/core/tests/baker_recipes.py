from model_bakery.recipe import Recipe, foreign_key
from organization.models import Tenant, TenantGroup, TenantRole, TenantMembership
from django.contrib.auth import get_user_model

User = get_user_model()

tenant_group = Recipe(
    TenantGroup,
    name="Test Group",
    slug="test-group"
)

tenant = Recipe(
    Tenant,
    name="Test Tenant",
    slug="test-tenant",
    group=foreign_key(tenant_group)
)

tenant_role = Recipe(
    TenantRole,
    tenant=foreign_key(tenant),
    name="Member",
    permissions=[]
)

user = Recipe(
    User,
    username="testuser",
    email="testuser@example.com"
)

tenant_membership = Recipe(
    TenantMembership,
    user=foreign_key(user),
    tenant=foreign_key(tenant),
    role=foreign_key(tenant_role)
)
