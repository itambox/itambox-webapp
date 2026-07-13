"""Regression tests for FIX #10 / defect #9 — nav/backend gate parity for group admin.

Under the pre-collapse design the navigation gate
``core.navigation.menu.can_manage_user_groups`` had its own truncated
implementation that diverged from the canonical backend gate
(``core.auth.provider.can_manage_user_groups``, since deleted with the
``Provider``/capability-string vocabulary). A single-company admin holding the
grant directly could see the backend allow access while the nav hid the menu
entry.

Post RBAC-collapse (RBAC_STAGE2_SPEC.md §6), both gates are standard permission
checks with NO separate capability vocabulary: superuser OR
``users.add_usergroup``/``users.change_usergroup`` held (``obj=tenant``) in any
tenant the user can access (``organization.access.accessible_tenant_ids``).
The canonical implementation now lives in ``users.views.is_global_group_admin``
(shared by ``GlobalGroupAdminMixin`` on the UserGroup views); the nav gate
``core.navigation.menu.can_manage_user_groups`` must stay in lock-step with it.

These tests assert the two gates agree across the full grant matrix: plain
member (denied), tenant admin holding the perm directly (allowed), superuser
(allowed), and a user who only holds the perm via a managed-reach
RoleGrant at an ``is_provider`` tenant covering a customer tenant
(allowed, evaluated in the covered customer tenant's context).
"""

import pytest
from django.contrib.auth import get_user_model

from core.navigation.menu import can_manage_user_groups as nav_gate
from core.tests.mixins import TenantTestMixin, grant
from organization.models import Role, RoleGrant, RoleGrantScope, Tenant
from users.views import is_global_group_admin

User = get_user_model()


@pytest.mark.django_db
class TestNavGroupGateUnify(TenantTestMixin):
    """Nav gate (``core.navigation.menu.can_manage_user_groups``) must equal the
    backend gate (``users.views.is_global_group_admin``) for every grant shape."""

    def test_plain_member_denied_by_both_gates(self):
        """A user with an active membership but no usergroup-admin permission is
        denied by both gates."""
        tenant = Tenant.objects.create(name="Plain Co", slug="plain-co")
        user = User.objects.create_user(
            username='plain_member', email='plain_member@example.com', password='password',
        )
        role = Role.objects.create(tenant=tenant, name="No Perms", permissions=[])
        grant(user, tenant, role)

        assert is_global_group_admin(user) is False
        assert nav_gate(user) is False
        assert nav_gate(user) == is_global_group_admin(user)

    def test_tenant_admin_with_change_usergroup_allowed_by_both_gates(self):
        """A non-superuser holding ``users.change_usergroup`` via an own-reach
        RoleGrant on their tenant is recognized by both gates."""
        tenant = Tenant.objects.create(name="Admin Co", slug="admin-co")
        user = User.objects.create_user(
            username='tenant_admin', email='tenant_admin@example.com', password='password',
        )
        role = Role.objects.create(
            tenant=tenant, name="Group Admin", permissions=['users.change_usergroup'],
        )
        grant(user, tenant, role)

        assert is_global_group_admin(user) is True
        assert nav_gate(user) is True
        assert nav_gate(user) == is_global_group_admin(user)

    def test_superuser_allowed_by_both_gates(self):
        """Superusers bypass the permission check entirely in both gates."""
        user = User.objects.create_superuser(
            username='super_admin', email='super_admin@example.com', password='password',
        )

        assert is_global_group_admin(user) is True
        assert nav_gate(user) is True
        assert nav_gate(user) == is_global_group_admin(user)

    def test_managed_reach_grant_at_provider_tenant_allowed_by_both_gates(self):
        """A user whose ONLY route to ``users.add_usergroup`` is a managed-reach
        RoleGrant on an ``is_provider`` tenant (covering a customer via an
        all-managed scope) is recognized by both gates when evaluated for the covered
        customer tenant.

        This exercises rule (3) of ``MembershipBackend._effective_perms_for_tenant``
        (RBAC_STAGE2_SPEC.md §2) and confirms ``accessible_tenant_ids`` surfaces the
        managed tenant so both gates enumerate it identically.
        """
        provider_tenant = Tenant.objects.create(
            name="MSP Provider", slug="msp-provider", is_provider=True,
        )
        customer_tenant = Tenant.objects.create(
            name="Managed Customer", slug="managed-customer", managed_by=provider_tenant,
        )
        user = User.objects.create_user(
            username='msp_tech', email='msp_tech@example.com', password='password',
        )
        managed_role = Role.objects.create(
            tenant=provider_tenant, name="MSP Group Admin",
            permissions=['users.add_usergroup'],
        )
        grant(
            user, provider_tenant, managed_role,
            reach=RoleGrant.REACH_MANAGED,
            managed_scope=RoleGrantScope.SCOPE_ALL_MANAGED,
        )

        with self.tenant_context(customer_tenant):
            assert is_global_group_admin(user) is True
            assert nav_gate(user) is True
            assert nav_gate(user) == is_global_group_admin(user)

    def test_nav_gate_matches_backend_gate_across_matrix(self):
        """Cross-check: build the plain-member / tenant-admin / superuser matrix
        again in one place and assert strict equality, guarding against the two
        gates drifting apart in either direction (nav showing an entry the
        backend would reject, or nav hiding one the backend would allow)."""
        tenant = Tenant.objects.create(name="Parity Co", slug="parity-co")
        granted_role = Role.objects.create(
            tenant=tenant, name="Parity Admin", permissions=['users.add_usergroup'],
        )

        granted = User.objects.create_user(
            username='parity_granted', email='parity_granted@example.com', password='password',
        )
        grant(granted, tenant, granted_role)

        denied = User.objects.create_user(
            username='parity_denied', email='parity_denied@example.com', password='password',
        )
        no_perm_role = Role.objects.create(tenant=tenant, name="No Perms 2", permissions=[])
        grant(denied, tenant, no_perm_role)

        superuser = User.objects.create_superuser(
            username='parity_super', email='parity_super@example.com', password='password',
        )

        for user, expected in ((granted, True), (denied, False), (superuser, True)):
            assert nav_gate(user) == is_global_group_admin(user) == expected
