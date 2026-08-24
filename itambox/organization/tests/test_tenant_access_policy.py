"""Contract tests for the organization tenant-access provider."""

from importlib import import_module
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.utils import timezone

from core.tenant_access import (
    configure_tenant_access_policy,
    get_tenant_access_policy,
    override_tenant_access_policy,
)
from organization.apps import OrganizationConfig
from organization.models import Membership, Tenant
from organization.services.tenant_access import (
    OrganizationTenantAccessPolicy,
    organization_tenant_access_policy,
)


class OrganizationTenantAccessPolicyTests(TestCase):
    def setUp(self):
        self.user = self._user()
        self.live_tenant = Tenant.objects.create(name="Live tenant", slug="tenant-access-live")
        self.deleted_tenant = Tenant.objects.create(name="Deleted tenant", slug="tenant-access-deleted")
        self.live_membership = Membership.objects.create(user=self.user, tenant=self.live_tenant)
        Membership.objects.create(user=self.user, tenant=self.deleted_tenant)
        Tenant._base_manager.filter(pk=self.deleted_tenant.pk).update(deleted_at=timezone.now())
        self.policy = OrganizationTenantAccessPolicy()

    @staticmethod
    def _user():
        from django.contrib.auth import get_user_model

        return get_user_model().objects.create_user(username="tenant-access-user")

    def test_active_membership_returns_live_concrete_membership_with_selected_tenant(self):
        membership = self.policy.active_membership(self.user, self.live_tenant.pk)

        self.assertIsInstance(membership, Membership)
        self.assertEqual(membership.pk, self.live_membership.pk)
        self.assertEqual(membership.tenant.pk, self.live_tenant.pk)

    def test_deleted_membership_tenant_is_not_returned(self):
        self.assertIsNone(self.policy.active_membership(self.user, self.deleted_tenant.pk))

    def test_first_active_membership_in_is_bounded_to_authorized_live_ids(self):
        membership = self.policy.first_active_membership_in(
            self.user,
            {self.live_tenant.pk, self.deleted_tenant.pk},
        )

        self.assertIsNotNone(membership)
        self.assertEqual(membership.tenant_id, self.live_tenant.pk)

    def test_empty_authorized_set_returns_without_membership_query(self):
        with self.assertNumQueries(0):
            membership = self.policy.first_active_membership_in(self.user, set())

        self.assertIsNone(membership)

    def test_registration_and_repeated_ready_are_zero_query_and_do_not_call_provider(self):
        with (
            mock.patch.object(organization_tenant_access_policy, "accessible_tenant_ids") as accessible,
            mock.patch.object(organization_tenant_access_policy, "active_membership") as active,
            mock.patch.object(organization_tenant_access_policy, "first_active_membership_in") as first,
            mock.patch.object(organization_tenant_access_policy, "shared_stock_read_allowed") as shared,
        ):
            with self.assertNumQueries(0):
                OrganizationConfig("organization", import_module("organization")).ready()
                OrganizationConfig("organization", import_module("organization")).ready()

        accessible.assert_not_called()
        active.assert_not_called()
        first.assert_not_called()
        shared.assert_not_called()
        self.assertIs(get_tenant_access_policy(), organization_tenant_access_policy)

    def test_competing_tenant_provider_does_not_replace_registered_singleton(self):
        competing = OrganizationTenantAccessPolicy()

        with self.assertRaisesMessage(
            ImproperlyConfigured,
            "tenant access policy provider is already configured with a different object",
        ):
            configure_tenant_access_policy(competing)

        self.assertIs(get_tenant_access_policy(), organization_tenant_access_policy)

    def test_context_override_is_visible_through_typed_wrapper_and_restores(self):
        sentinel = mock.Mock()
        sentinel.accessible_tenant_ids.return_value = {self.live_tenant.pk}

        with override_tenant_access_policy(sentinel):
            from core.tenant_access import accessible_tenant_ids

            self.assertEqual(accessible_tenant_ids(self.user), {self.live_tenant.pk})

        self.assertIs(get_tenant_access_policy(), organization_tenant_access_policy)

    def test_membership_selection_does_not_query_tenant_separately(self):
        with self.assertNumQueries(1) as context:
            membership = self.policy.active_membership(self.user, self.live_tenant.pk)

        self.assertIs(membership.tenant.__class__, Tenant)
        self.assertEqual(len(context), 1)
