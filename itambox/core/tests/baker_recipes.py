from model_bakery.recipe import Recipe, foreign_key
from organization.models import (
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
    TenantGroup,
)
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
    Role,
    tenant=foreign_key(tenant),
    name="Member",
    permissions=[]
)

user = Recipe(
    User,
    username="testuser",
    email="testuser@example.com"
)

# A Membership is just the (user, tenant) anchor. Callers needing effective
# access should normally use ``core.tests.mixins.grant(...)`` so a scope is added.
tenant_membership = Recipe(
    Membership,
    user=foreign_key(user),
    tenant=foreign_key(tenant),
)

role_grant = Recipe(
    RoleGrant,
    membership=foreign_key(tenant_membership),
    role=foreign_key(tenant_role),
)

role_grant_scope = Recipe(
    RoleGrantScope,
    role_grant=foreign_key(role_grant),
    scope_type=RoleGrantScope.SCOPE_OWN,
)
