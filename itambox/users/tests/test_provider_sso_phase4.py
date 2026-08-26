"""Provider-staff SSO JIT delegates to the Organization identity port."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from core import identity_provisioning
from organization.models import Membership, Role, RoleGrant, Tenant
from organization.services.identity_provisioning import (
    IdentityProvisioningError,
    organization_identity_provisioner,
)

User = get_user_model()


class ProvisionProviderMembershipTests(TestCase):
    def setUp(self):
        self.provider_tenant = Tenant.objects.create(
            name="MSP",
            slug="msp",
            is_provider=True,
        )
        self.customer_tenant = Tenant.objects.create(
            name="Customer",
            slug="customer",
            managed_by=self.provider_tenant,
        )
        self.role = Role.objects.create(
            tenant=self.provider_tenant,
            name="Provider Admin",
        )
        self.user = User.objects.create_user(
            username="u",
            email="u@e.com",
            password="pw",
        )

    def _provision(self, provider_tenant=None, role_name="Provider Admin"):
        command = identity_provisioning.ExternalIdentityProvisioningCommand(
            user=self.user,
            customer_tenant=self.customer_tenant,
            profile=identity_provisioning.ExternalIdentityProfile(
                source="OIDC",
                email=self.user.email,
                upn=self.user.email,
                first_name="",
                last_name="",
            ),
            customer_role_name="Member",
            provider_staff=identity_provisioning.ProviderStaffIntent(
                provider_tenant=provider_tenant or self.provider_tenant,
                role_name=role_name,
            ),
        )
        with identity_provisioning.override_identity_provisioner(organization_identity_provisioner):
            return identity_provisioning.provision_external_identity(command)

    def test_provisions_identity_only_for_mapped_role(self):
        result = self._provision()

        self.assertEqual(result.mode, "provider_staff")
        membership = Membership.objects.get(user=self.user, tenant=self.provider_tenant)
        self.assertTrue(membership.is_active)
        self.assertEqual(result.membership_id, membership.pk)
        self.assertEqual(result.role_id, self.role.pk)
        self.assertFalse(RoleGrant.objects.filter(membership=membership).exists())
        self.assertEqual(
            Membership.objects.filter(user=self.user, tenant=self.provider_tenant).count(),
            1,
        )

    def test_missing_role_is_terminal_rejection(self):
        result = self._provision(role_name="Nonexistent")

        self.assertEqual(result.mode, "provider_mapping_rejected")
        self.assertIsNone(result.membership_id)
        self.assertIsNone(result.role_id)
        self.assertFalse(Membership.objects.filter(user=self.user).exists())

    def test_reactivates_existing_membership_without_granting_access(self):
        existing = Membership.objects.create(
            user=self.user,
            tenant=self.provider_tenant,
            is_active=False,
        )

        result = self._provision()

        self.assertEqual(result.mode, "provider_staff")
        existing.refresh_from_db()
        self.assertTrue(existing.is_active)
        self.assertFalse(RoleGrant.objects.filter(membership=existing).exists())
        self.assertEqual(result.membership_id, existing.pk)
        self.assertEqual(
            Membership.objects.filter(user=self.user, tenant=self.provider_tenant).count(),
            1,
        )

    def test_repeated_login_does_not_duplicate_membership_or_create_grant(self):
        first = self._provision()
        second = self._provision()

        membership = Membership.objects.get(user=self.user, tenant=self.provider_tenant)
        self.assertEqual(first.mode, "provider_staff")
        self.assertEqual(second.mode, "provider_staff")
        self.assertEqual(first.membership_id, second.membership_id)
        self.assertFalse(RoleGrant.objects.filter(membership=membership).exists())
        self.assertEqual(
            Membership.objects.filter(user=self.user, tenant=self.provider_tenant).count(),
            1,
        )

    def test_non_provider_target_tenant_fails_closed(self):
        plain_tenant = Tenant.objects.create(name="Plain", slug="plain")
        Role.objects.create(tenant=plain_tenant, name="Provider Admin")

        with self.assertRaises(IdentityProvisioningError):
            self._provision(provider_tenant=plain_tenant)

        self.assertFalse(Membership.objects.filter(user=self.user).exists())
