"""Phase 4 tests: MSP-staff SSO JIT provisioning (``provision_provider_membership``).

Post-collapse (RBAC_STAGE2_SPEC.md), a "provider" is just a ``Tenant`` with
``is_provider=True`` — there is no more standalone Provider model, and staff
access is a managed-reach ``RoleAssignment`` hanging off a ``Membership`` at
that tenant, not a roles M2M. ``Token.provider`` was deleted outright: a
provider-tenant SCIM/API token is now just a ``Token`` whose ``tenant`` is an
``is_provider`` tenant (see spec §6 "SCIM provider auth") — there is no field
left to unit-test in this module, so the former ``TokenProviderFKTests`` class
has no successor here (that gate's behaviour belongs to the SCIM/provider-auth
view suite, not this model-level module).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase

from core.auth.provisioning import provision_provider_membership
from organization.models import Membership, Role, RoleAssignment, Tenant

User = get_user_model()


class ProvisionProviderMembershipTests(TestCase):
    def setUp(self):
        self.provider_tenant = Tenant.objects.create(name="MSP", slug="msp", is_provider=True)
        self.role = Role.objects.create(tenant=self.provider_tenant, name="Provider Admin")
        self.user = User.objects.create_user(username="u", email="u@e.com", password="pw")

    def test_assigns_managed_reach_membership_for_mapped_role(self):
        m = provision_provider_membership(self.user, self.provider_tenant, "Provider Admin", "OIDC")
        self.assertIsNotNone(m)
        self.assertTrue(m.is_active)
        self.assertEqual(m.tenant_id, self.provider_tenant.pk)

        assignment = m.assignments.get(role=self.role)
        self.assertEqual(assignment.reach, RoleAssignment.REACH_MANAGED)
        # Starts with empty EXPLICIT coverage until an admin refines it — matches the
        # pre-collapse default (docstring of provision_provider_membership).
        self.assertEqual(assignment.managed_scope, RoleAssignment.SCOPE_EXPLICIT)
        self.assertEqual(assignment.scoped_tenant_ids(), set())

        self.assertEqual(
            Membership.objects.filter(user=self.user, tenant=self.provider_tenant).count(), 1
        )

    def test_missing_role_is_noop(self):
        m = provision_provider_membership(self.user, self.provider_tenant, "Nonexistent", "OIDC")
        self.assertIsNone(m)
        self.assertFalse(Membership.objects.filter(user=self.user).exists())

    def test_reactivates_and_updates_existing_membership(self):
        Membership.objects.create(user=self.user, tenant=self.provider_tenant, is_active=False)

        m = provision_provider_membership(self.user, self.provider_tenant, "Provider Admin", "OIDC")

        self.assertIsNotNone(m)
        self.assertTrue(m.is_active)
        self.assertTrue(
            m.assignments.filter(role=self.role, reach=RoleAssignment.REACH_MANAGED).exists()
        )
        # Idempotent: still a single membership row.
        self.assertEqual(
            Membership.objects.filter(user=self.user, tenant=self.provider_tenant).count(), 1
        )

    def test_repeated_login_does_not_duplicate_the_assignment(self):
        provision_provider_membership(self.user, self.provider_tenant, "Provider Admin", "OIDC")
        provision_provider_membership(self.user, self.provider_tenant, "Provider Admin", "OIDC")

        membership = Membership.objects.get(user=self.user, tenant=self.provider_tenant)
        self.assertEqual(
            membership.assignments.filter(
                role=self.role, reach=RoleAssignment.REACH_MANAGED
            ).count(),
            1,
        )

    def test_non_provider_target_tenant_fails_closed(self):
        """A mapping that targets a tenant without ``is_provider=True`` is a
        misconfiguration (a managed-reach assignment there would fail model
        validation) — the helper must refuse cleanly rather than raising into the
        SSO login path, and must not create a membership."""
        plain_tenant = Tenant.objects.create(name="Plain", slug="plain")
        Role.objects.create(tenant=plain_tenant, name="Provider Admin")

        m = provision_provider_membership(self.user, plain_tenant, "Provider Admin", "OIDC")

        self.assertIsNone(m)
        self.assertFalse(Membership.objects.filter(user=self.user).exists())
