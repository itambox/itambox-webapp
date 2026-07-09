"""Phase 4 tests: provider-level SSO JIT provisioning helper + Token.provider FK."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from core.auth.provisioning import provision_provider_membership
from organization.models import Provider, Role, Tenant
from organization.models import Membership
from users.models import Token

User = get_user_model()


class ProvisionProviderMembershipTests(TestCase):
    def setUp(self):
        self.provider = Provider.objects.create(name="MSP")
        self.role = Role.objects.create(provider=self.provider, name="Provider Admin")
        self.user = User.objects.create_user(username="u", email="u@e.com", password="pw")

    def test_assigns_membership_for_mapped_role(self):
        m = provision_provider_membership(self.user, self.provider, "Provider Admin", "OIDC")
        self.assertIsNotNone(m)
        self.assertIn(self.role, m.roles.all())
        self.assertTrue(m.is_active)
        self.assertEqual(
            Membership.objects.filter(user=self.user, provider=self.provider).count(), 1
        )

    def test_missing_role_is_noop(self):
        m = provision_provider_membership(self.user, self.provider, "Nonexistent", "OIDC")
        self.assertIsNone(m)
        self.assertFalse(Membership.objects.filter(user=self.user).exists())

    def test_reactivates_and_updates_existing_membership(self):
        Membership.objects.create(user=self.user, provider=self.provider, is_active=False,
        )
        m = provision_provider_membership(self.user, self.provider, "Provider Admin", "OIDC")
        self.assertIsNotNone(m)
        self.assertTrue(m.is_active)
        self.assertIn(self.role, m.roles.all())
        # Idempotent: still a single membership row.
        self.assertEqual(
            Membership.objects.filter(user=self.user, provider=self.provider).count(), 1
        )


class TokenProviderFKTests(TestCase):
    def test_token_provider_nullable_default(self):
        t = Tenant.objects.create(name="T", slug="t")
        u = User.objects.create_user(username="tk", email="tk@e.com", password="pw")
        tok = Token.objects.create(user=u, tenant=t)
        self.assertIsNone(tok.provider_id)

    def test_token_scoped_to_provider(self):
        t = Tenant.objects.create(name="T2", slug="t2")
        p = Provider.objects.create(name="P")
        u = User.objects.create_user(username="tk2", email="tk2@e.com", password="pw")
        tok = Token.objects.create(user=u, tenant=t, provider=p)
        self.assertEqual(tok.provider_id, p.pk)
        self.assertIn(tok, p.tokens.all())
