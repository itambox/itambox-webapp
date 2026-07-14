from model_bakery.recipe import Recipe, foreign_key
from organization.models import Tenant, TenantGroup, Role, Membership, RoleAssignment
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

# A Membership is just the (user, tenant) anchor; what the user may DO is a
# separate RoleAssignment row. Callers that need a grant use the
# `role_assignment` recipe below, or `core.tests.mixins.grant(...)`.
tenant_membership = Recipe(
    Membership,
    user=foreign_key(user),
    tenant=foreign_key(tenant),
)

role_assignment = Recipe(
    RoleAssignment,
    membership=foreign_key(tenant_membership),
    role=foreign_key(tenant_role),
    reach=RoleAssignment.REACH_OWN,
)
