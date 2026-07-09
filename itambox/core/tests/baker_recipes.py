from model_bakery.recipe import Recipe, foreign_key
from organization.models import Tenant, TenantGroup, Role, Membership
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

# Membership.role was replaced by a `roles` M2M (+ `direct_permissions`),
# which model_bakery cannot populate via `foreign_key`. The recipe creates a
# membership with no roles; callers that need one add it after make, e.g.:
#     m = baker.make_recipe('core.tenant_membership'); m.roles.add(role)
tenant_membership = Recipe(
    Membership,
    user=foreign_key(user),
    tenant=foreign_key(tenant),
)
