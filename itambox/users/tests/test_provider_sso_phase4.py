"""Provider-staff SSO JIT provisions identity without implicit access."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from core.auth.provisioning import provision_provider_membership
from organization.models import Membership, Role, RoleGrant, Tenant

User = get_user_model()


class ProvisionProviderMembershipTests(TestCase):
    def setUp(self):
        self.provider_tenant = Tenant.objects.create(name="MSP", slug="msp", is_provider=True)
        self.role = Role.objects.create(tenant=self.provider_tenant, name="Provider Admin")
        self.user = User.objects.create_user(username="u", email="u@e.com", password="pw")

    def test_provisions_identity_only_for_mapped_role(self):
        membership = provision_provider_membership(
            self.user, self.provider_tenant, "Provider Admin", "OIDC",
        )

        self.assertIsNotNone(membership)
        self.assertTrue(membership.is_active)
        self.assertEqual(membership.tenant_id, self.provider_tenant.pk)
        self.assertFalse(RoleGrant.objects.filter(membership=membership).exists())
        self.assertEqual(
            Membership.objects.filter(user=self.user, tenant=self.provider_tenant).count(),
            1,
        )

    def test_missing_role_is_noop(self):
        membership = provision_provider_membership(
            self.user, self.provider_tenant, "Nonexistent", "OIDC",
        )

        self.assertIsNone(membership)
        self.assertFalse(Membership.objects.filter(user=self.user).exists())

    def test_reactivates_existing_membership_without_granting_access(self):
        Membership.objects.create(user=self.user, tenant=self.provider_tenant, is_active=False)

        membership = provision_provider_membership(
            self.user, self.provider_tenant, "Provider Admin", "OIDC",
        )

        self.assertIsNotNone(membership)
        self.assertTrue(membership.is_active)
        self.assertFalse(RoleGrant.objects.filter(membership=membership).exists())
        self.assertEqual(
            Membership.objects.filter(user=self.user, tenant=self.provider_tenant).count(),
            1,
        )

    def test_repeated_login_does_not_duplicate_membership_or_create_grant(self):
        provision_provider_membership(
            self.user, self.provider_tenant, "Provider Admin", "OIDC",
        )
        provision_provider_membership(
            self.user, self.provider_tenant, "Provider Admin", "OIDC",
        )

        membership = Membership.objects.get(user=self.user, tenant=self.provider_tenant)
        self.assertFalse(RoleGrant.objects.filter(membership=membership).exists())
        self.assertEqual(
            Membership.objects.filter(user=self.user, tenant=self.provider_tenant).count(),
            1,
        )

    def test_non_provider_target_tenant_fails_closed(self):
        plain_tenant = Tenant.objects.create(name="Plain", slug="plain")
        Role.objects.create(tenant=plain_tenant, name="Provider Admin")

        membership = provision_provider_membership(
            self.user, plain_tenant, "Provider Admin", "OIDC",
        )

        self.assertIsNone(membership)
        self.assertFalse(Membership.objects.filter(user=self.user).exists())
