"""Shared fixtures for the journey suite. Permissions are always role grants."""

import itertools
from datetime import date

from django.contrib.auth import get_user_model
from model_bakery import baker

from assets.models import Asset
from core.tests.mixins import TenantTestMixin, grant
from organization.models import AssetHolder, Role, Tenant

User = get_user_model()

PASSWORD = "journey-test-password"
_sequence = itertools.count(1)


class JourneyMixin(TenantTestMixin):
    """Tenant plus role-granted users; no superusers, no ``user_permissions``."""

    def make_tenant(self, slug="journey"):
        self.tenant = Tenant.objects.create(name=f"Journey {slug}", slug=slug)
        return self.tenant

    def make_member(self, username, permissions, tenant=None):
        """A tenant member whose ONLY authority is a role carrying ``permissions``."""
        tenant = tenant or self.tenant
        user = User.objects.create_user(username=username, email=f"{username}@example.test", password=PASSWORD)
        role = Role.objects.create(tenant=tenant, name=f"{username} role", permissions=sorted(permissions))
        grant(user, tenant, role)
        return user

    def make_holder(self, user=None, **extra):
        number = next(_sequence)
        values = {
            "first_name": "Journey",
            "last_name": "Holder",
            "email": f"holder-{number}@example.test",
            "upn": f"holder-{number}@example.test",
            "tenant": self.tenant,
            "user": user,
        }
        values.update(extra)
        return AssetHolder.objects.create(**values)

    def make_asset(self, **extra):
        return baker.make(Asset, tenant=self.tenant, **extra)


def today():
    return date.today()
