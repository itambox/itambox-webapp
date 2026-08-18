from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.auth.cache import invalidate_user_authorization_cache
from core.tests.mixins import grant
from organization.access import accessible_tenant_ids
from organization.models import Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.rbac import effective_permissions
from organization.services.tenant_onboarding import onboard_managed_tenant
from users.models import GroupMembership, UserGroup

User = get_user_model()


class ManagedTenantOnboardingAuthorizationTests(TestCase):
    def test_provider_admin_creation_projects_admin_access_and_switches_workspace(self):
        provider = Tenant.objects.create(name="Provider", slug="provider", is_provider=True)
        creator = User.objects.create_user(username="provider-admin", is_staff=True)
        administrator_permissions = [
            "organization.add_tenant",
            "organization.view_tenant",
            "organization.change_tenant",
            "organization.add_membership",
        ]
        administrator = Role.objects.create(
            tenant=provider,
            name="Administrator",
            permissions=administrator_permissions,
        )
        technician = Role.objects.create(
            tenant=provider,
            name="MSP Technician",
            permissions=["organization.view_tenant"],
            shared_with_managed=True,
        )
        own_administrator_grant = grant(
            creator,
            provider,
            administrator,
            granted_by=creator,
        )
        grant(
            creator,
            provider,
            technician,
            reach=RoleGrant.REACH_MANAGED,
            managed_scope=RoleGrantScope.SCOPE_ALL_MANAGED,
            granted_by=creator,
        )

        other_provider = Tenant.objects.create(
            name="Other Provider",
            slug="other-provider",
            is_provider=True,
        )
        other_administrator = User.objects.create_user(username="other-provider-admin", is_staff=True)
        other_administrator_role = Role.objects.create(
            tenant=other_provider,
            name="Administrator",
            permissions=administrator_permissions,
        )
        grant(
            other_administrator,
            other_provider,
            other_administrator_role,
            granted_by=other_administrator,
        )

        self.assertFalse(creator.is_superuser)
        self.assertEqual(accessible_tenant_ids(creator), {provider.pk})
        self.assertEqual(accessible_tenant_ids(other_administrator), {other_provider.pk})
        self.client.force_login(creator)
        session = self.client.session
        session["active_tenant_id"] = provider.pk
        session.pop("active_tenant_group_id", None)
        session.save()

        response = self.client.post(
            reverse("organization:tenant_create") + f"?managed_by={provider.pk}",
            {
                "name": "Managed Customer",
                "slug": "managed-customer",
                "currency": "EUR",
                "managed_by": provider.pk,
            },
        )

        self.assertEqual(response.status_code, 302)
        customer = Tenant._base_manager.get(slug="managed-customer")
        self.assertEqual(customer.managed_by_id, provider.pk)
        self.assertEqual(
            response.url,
            reverse("organization:tenant_detail", kwargs={"pk": customer.pk}),
        )

        provider_membership = Membership._base_manager.get(user=creator, tenant=provider)
        managed_administrator_grant = RoleGrant.objects.exclude(pk=own_administrator_grant.pk).get(
            membership=provider_membership,
            role=administrator,
            scopes__scope_type=RoleGrantScope.SCOPE_TENANT,
            scopes__tenant=customer,
        )
        self.assertNotEqual(managed_administrator_grant.pk, own_administrator_grant.pk)
        self.assertEqual(
            set(own_administrator_grant.scopes.values_list("scope_type", "tenant_id")),
            {(RoleGrantScope.SCOPE_OWN, None)},
        )
        self.assertEqual(
            set(managed_administrator_grant.scopes.values_list("scope_type", "tenant_id")),
            {(RoleGrantScope.SCOPE_TENANT, customer.pk)},
        )
        self.assertEqual(
            RoleGrant.objects.filter(membership=provider_membership, role=administrator).count(),
            2,
        )
        self.assertFalse(Membership._base_manager.filter(user=creator, tenant=customer).exists())

        self.assertIn("organization.change_tenant", effective_permissions(creator, customer))
        self.assertTrue(creator.has_perm("organization.change_tenant", obj=customer))
        self.assertEqual(self.client.session.get("active_tenant_id"), customer.pk)
        self.assertNotIn("active_tenant_group_id", self.client.session)

        detail_response = self.client.get(response.url)
        self.assertEqual(detail_response.status_code, 200)
        self.assertTemplateUsed(detail_response, "organization/tenants/tenant_detail.html")
        self.assertEqual(detail_response.wsgi_request.active_tenant, customer)
        own_tenant_ids = {tenant.pk for tenant in detail_response.context["own_tenants_switcher"]}
        managed_tenant_ids = {
            tenant.pk
            for item in detail_response.context["grouped_managed_tenants_switcher"]
            for tenant in item["tenants"]
        }
        self.assertIn(provider.pk, own_tenant_ids)
        self.assertNotIn(customer.pk, own_tenant_ids)
        self.assertIn(customer.pk, managed_tenant_ids)

        self.assertFalse(
            RoleGrantScope.objects.filter(
                role_grant__membership__user=other_administrator,
                scope_type=RoleGrantScope.SCOPE_TENANT,
                tenant=customer,
            ).exists()
        )
        self.assertNotIn(customer.pk, accessible_tenant_ids(other_administrator))
        self.assertNotIn(
            "organization.change_tenant",
            effective_permissions(other_administrator, customer),
        )
        self.assertFalse(other_administrator.has_perm("organization.change_tenant", obj=customer))

    @staticmethod
    def _administrator_role(provider):
        return Role.objects.create(
            tenant=provider,
            name="Administrator",
            permissions=[
                "organization.add_tenant",
                "organization.view_tenant",
                "organization.change_tenant",
                "organization.add_membership",
            ],
        )

    @staticmethod
    def _authorization_fingerprint():
        return tuple(
            tuple(model._base_manager.order_by("pk").values()) for model in (Membership, RoleGrant, RoleGrantScope)
        )

    def _assert_onboarding_rejected(self, *, actor, provider_id, tenant):
        before = self._authorization_fingerprint()

        with self.assertRaises(PermissionDenied):
            onboard_managed_tenant(actor=actor, provider_id=provider_id, tenant=tenant)

        self.assertEqual(self._authorization_fingerprint(), before)
        self.assertFalse(RoleGrantScope._base_manager.filter(tenant_id=tenant.pk).exists())
        self.assertFalse(Membership._base_manager.filter(tenant_id=tenant.pk).exists())

    def _direct_authorizer(self):
        provider = Tenant.objects.create(name="Direct Provider", slug="direct-provider", is_provider=True)
        tenant = Tenant.objects.create(name="Direct Customer", slug="direct-customer", managed_by=provider)
        actor = User.objects.create_user(username="direct-provider-admin", is_staff=True)
        administrator = self._administrator_role(provider)
        authorizer = grant(actor, provider, administrator, granted_by=actor)
        return provider, tenant, actor, administrator, authorizer

    def _post_managed_tenant(self, *, actor, active_tenant, provider, name, slug):
        self.client.force_login(actor)
        session = self.client.session
        session["active_tenant_id"] = active_tenant.pk
        session.pop("active_tenant_group_id", None)
        session.save()
        return self.client.post(
            reverse("organization:tenant_create") + f"?managed_by={provider.pk}",
            {"name": name, "slug": slug, "currency": "EUR", "managed_by": provider.pk},
        )

    def test_direct_onboarding_rejects_provider_mismatched_with_management_edge(self):
        persisted_provider = Tenant.objects.create(
            name="Persisted Provider",
            slug="persisted-provider",
            is_provider=True,
        )
        requested_provider = Tenant.objects.create(
            name="Requested Provider",
            slug="requested-provider",
            is_provider=True,
        )
        tenant = Tenant.objects.create(
            name="Persisted Provider Customer",
            slug="persisted-provider-customer",
            managed_by=persisted_provider,
        )
        actor = User.objects.create_user(username="requested-provider-admin", is_staff=True)
        administrator = self._administrator_role(requested_provider)
        grant(actor, requested_provider, administrator, granted_by=actor)

        self._assert_onboarding_rejected(actor=actor, provider_id=requested_provider.pk, tenant=tenant)

    def test_direct_onboarding_rejects_actor_without_active_provider_membership(self):
        provider = Tenant.objects.create(name="Membership Provider", slug="membership-provider", is_provider=True)
        tenant = Tenant.objects.create(name="Membership Customer", slug="membership-customer", managed_by=provider)
        actor = User.objects.create_user(username="suspended-provider-member", is_staff=True)
        membership = Membership.objects.create(user=actor, tenant=provider, is_active=False)
        administrator = self._administrator_role(provider)
        grant(actor, provider, administrator, granted_by=actor)

        self.assertTrue(actor.is_authenticated)
        self.assertFalse(membership.is_active)
        self._assert_onboarding_rejected(actor=actor, provider_id=provider.pk, tenant=tenant)

    def test_direct_onboarding_rejects_duplicate_live_managed_aggregates(self):
        provider, tenant, actor, administrator, authorizer = self._direct_authorizer()
        for number in (1, 2):
            existing_tenant = Tenant.objects.create(
                name=f"Existing Customer {number}",
                slug=f"existing-customer-{number}",
                managed_by=provider,
            )
            aggregate = RoleGrant.objects.create(
                membership=authorizer.membership,
                role=administrator,
                granted_by=actor,
                reason=f"Existing managed aggregate {number}",
                valid_until=authorizer.valid_until,
            )
            RoleGrantScope.objects.create(
                role_grant=aggregate,
                scope_type=RoleGrantScope.SCOPE_TENANT,
                tenant=existing_tenant,
            )

        managed_aggregates = RoleGrant._base_manager.filter(
            membership=authorizer.membership,
            role=administrator,
            scopes__scope_type=RoleGrantScope.SCOPE_TENANT,
        )
        self.assertEqual(managed_aggregates.count(), 2)
        self._assert_onboarding_rejected(actor=actor, provider_id=provider.pk, tenant=tenant)

    def test_direct_onboarding_coalesces_duplicate_own_authorizers_at_union_deadline(self):
        provider = Tenant.objects.create(name="Union Provider", slug="union-provider", is_provider=True)
        customers = (
            Tenant.objects.create(name="Union Customer One", slug="union-customer-one", managed_by=provider),
            Tenant.objects.create(name="Union Customer Two", slug="union-customer-two", managed_by=provider),
        )
        actor = User.objects.create_user(username="union-provider-admin", is_staff=True)
        membership = Membership.objects.create(user=actor, tenant=provider)
        administrator = self._administrator_role(provider)
        later_deadline = timezone.now() + timedelta(days=365)
        sooner_deadline = timezone.now() + timedelta(days=30)

        def create_own_authorizer(*, reason, valid_until):
            authorizer = RoleGrant.objects.create(
                membership=membership,
                role=administrator,
                granted_by=actor,
                reason=reason,
                valid_until=valid_until,
            )
            scope = RoleGrantScope.objects.create(
                role_grant=authorizer,
                scope_type=RoleGrantScope.SCOPE_OWN,
            )
            return authorizer, scope

        later_authorizer, later_scope = create_own_authorizer(
            reason="Longer provider authority",
            valid_until=later_deadline,
        )
        sooner_authorizer, sooner_scope = create_own_authorizer(
            reason="Shorter duplicate provider authority",
            valid_until=sooner_deadline,
        )
        source_ids = (later_authorizer.pk, sooner_authorizer.pk)
        source_scope_ids = (later_scope.pk, sooner_scope.pk)

        def source_fingerprint():
            return (
                tuple(RoleGrant._base_manager.filter(pk__in=source_ids).order_by("pk").values()),
                tuple(RoleGrantScope._base_manager.filter(pk__in=source_scope_ids).order_by("pk").values()),
            )

        sources_before = source_fingerprint()
        self.assertLess(later_authorizer.pk, sooner_authorizer.pk)
        self.assertGreater(later_authorizer.valid_until, sooner_authorizer.valid_until)

        for customer in customers:
            self.assertEqual(
                onboard_managed_tenant(actor=actor, provider_id=provider.pk, tenant=customer),
                customer,
            )

        managed_aggregate = (
            RoleGrant._base_manager.filter(membership=membership, role=administrator).exclude(pk__in=source_ids).get()
        )
        self.assertEqual(managed_aggregate.valid_until, max(later_deadline, sooner_deadline))
        self.assertEqual(
            tuple(
                managed_aggregate.scopes.order_by("tenant_id").values_list(
                    "role_grant_id", "scope_type", "tenant_id", "tenant_group_id"
                )
            ),
            (
                (managed_aggregate.pk, RoleGrantScope.SCOPE_TENANT, customers[0].pk, None),
                (managed_aggregate.pk, RoleGrantScope.SCOPE_TENANT, customers[1].pk, None),
            ),
        )
        self.assertEqual(
            set(RoleGrant._base_manager.filter(membership=membership, role=administrator).values_list("pk", flat=True)),
            {later_authorizer.pk, sooner_authorizer.pk, managed_aggregate.pk},
        )
        self.assertEqual(
            tuple(Membership._base_manager.filter(user=actor).values_list("pk", "tenant_id", "is_active")),
            ((membership.pk, provider.pk, True),),
        )
        self.assertEqual(source_fingerprint(), sources_before)

        before_retry = self._authorization_fingerprint()
        self.assertEqual(
            onboard_managed_tenant(actor=actor, provider_id=provider.pk, tenant=customers[1]),
            customers[1],
        )
        self.assertEqual(self._authorization_fingerprint(), before_retry)

    def test_direct_onboarding_keeps_permanent_legacy_managed_aggregate_separate(self):
        provider, tenant, actor, administrator, authorizer = self._direct_authorizer()
        old_tenant = Tenant.objects.create(
            name="Legacy Managed Customer",
            slug="legacy-managed-customer",
            managed_by=provider,
        )
        legacy_grantor = User.objects.create_user(username="legacy-managed-grantor")
        legacy = RoleGrant.objects.create(
            membership=authorizer.membership,
            role=administrator,
            granted_by=legacy_grantor,
            reason="Historical permanent managed administration",
            valid_until=timezone.now() + timedelta(days=90),
        )
        RoleGrantScope.objects.create(
            role_grant=legacy,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=old_tenant,
        )
        self.assertEqual(RoleGrant._base_manager.filter(pk=legacy.pk).update(valid_until=None), 1)

        def legacy_fingerprint():
            return (
                RoleGrant._base_manager.filter(pk=legacy.pk).values().get(),
                tuple(RoleGrantScope._base_manager.filter(role_grant=legacy).order_by("pk").values()),
            )

        legacy_before = legacy_fingerprint()
        self.assertIsNone(legacy_before[0]["valid_until"])
        self.assertEqual(
            onboard_managed_tenant(actor=actor, provider_id=provider.pk, tenant=tenant),
            tenant,
        )

        onboarding_scope = RoleGrantScope._base_manager.get(
            role_grant__membership=authorizer.membership,
            role_grant__role=administrator,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=tenant,
        )
        onboarding = RoleGrant._base_manager.get(pk=onboarding_scope.role_grant_id)
        self.assertNotIn(onboarding.pk, (authorizer.pk, legacy.pk))
        self.assertEqual(onboarding.valid_until, authorizer.valid_until)
        self.assertEqual(
            tuple(onboarding.scopes.values_list("scope_type", "tenant_id", "tenant_group_id")),
            ((RoleGrantScope.SCOPE_TENANT, tenant.pk, None),),
        )
        self.assertEqual(legacy_fingerprint(), legacy_before)
        self.assertEqual(
            tuple(Membership._base_manager.filter(user=actor).values_list("pk", "tenant_id", "is_active")),
            ((authorizer.membership_id, provider.pk, True),),
        )

        before_retry = self._authorization_fingerprint()
        self.assertEqual(
            onboard_managed_tenant(actor=actor, provider_id=provider.pk, tenant=tenant),
            tenant,
        )
        self.assertEqual(self._authorization_fingerprint(), before_retry)
        self.assertEqual(legacy_fingerprint(), legacy_before)

    def test_direct_onboarding_rejects_corrupted_unbounded_privileged_authorizer(self):
        provider, tenant, actor, _administrator, authorizer = self._direct_authorizer()
        self.assertEqual(RoleGrant._base_manager.filter(pk=authorizer.pk).update(valid_until=None), 1)
        authorizer.refresh_from_db()
        self.assertIsNone(authorizer.valid_until)

        self._assert_onboarding_rejected(actor=actor, provider_id=provider.pk, tenant=tenant)

    def test_direct_onboarding_rejects_absent_or_invalid_actor(self):
        provider = Tenant.objects.create(name="Actor Provider", slug="actor-provider", is_provider=True)
        tenant = Tenant.objects.create(name="Actor Customer", slug="actor-customer", managed_by=provider)
        inactive_actor = User.objects.create_user(username="inactive-provider-actor", is_active=False)

        for label, actor in (("absent", None), ("inactive", inactive_actor)):
            with self.subTest(actor=label):
                self._assert_onboarding_rejected(actor=actor, provider_id=provider.pk, tenant=tenant)

    def test_creation_reuses_existing_managed_administrator_aggregate(self):
        provider = Tenant.objects.create(name="Reuse Provider", slug="reuse-provider", is_provider=True)
        old_customer = Tenant.objects.create(
            name="Older Managed Customer",
            slug="older-managed-customer",
            managed_by=provider,
        )
        creator = User.objects.create_user(username="reuse-provider-admin", is_staff=True)
        legacy_grantor = User.objects.create_user(username="legacy-grantor")
        administrator = self._administrator_role(provider)
        own_grant = grant(creator, provider, administrator, granted_by=creator)
        managed_grant = RoleGrant.objects.create(
            membership=own_grant.membership,
            role=administrator,
            granted_by=legacy_grantor,
            reason="Legacy managed-customer administration",
            valid_until=timezone.now() + timedelta(days=90),
        )
        RoleGrantScope.objects.create(
            role_grant=managed_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=old_customer,
        )
        preserved_metadata = (
            managed_grant.granted_by_id,
            managed_grant.reason,
            managed_grant.granted_at,
            managed_grant.valid_until,
        )

        response = self._post_managed_tenant(
            actor=creator,
            active_tenant=provider,
            provider=provider,
            name="Second Managed Customer",
            slug="second-managed-customer",
        )

        self.assertEqual(response.status_code, 302)
        new_customer = Tenant._base_manager.get(slug="second-managed-customer")
        new_scope = RoleGrantScope.objects.get(
            role_grant__membership=own_grant.membership,
            role_grant__role=administrator,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=new_customer,
        )
        self.assertEqual(new_scope.role_grant_id, managed_grant.pk)
        managed_grant.refresh_from_db()
        self.assertEqual(
            (
                managed_grant.granted_by_id,
                managed_grant.reason,
                managed_grant.granted_at,
                managed_grant.valid_until,
            ),
            preserved_metadata,
        )
        self.assertEqual(
            set(managed_grant.scopes.values_list("scope_type", "tenant_id")),
            {
                (RoleGrantScope.SCOPE_TENANT, old_customer.pk),
                (RoleGrantScope.SCOPE_TENANT, new_customer.pk),
            },
        )
        self.assertEqual(
            managed_grant.scopes.filter(
                scope_type=RoleGrantScope.SCOPE_TENANT,
                tenant=new_customer,
            ).count(),
            1,
        )
        self.assertEqual(
            set(own_grant.scopes.values_list("scope_type", "tenant_id")),
            {(RoleGrantScope.SCOPE_OWN, None)},
        )
        self.assertEqual(
            RoleGrant.objects.filter(role=administrator).count(),
            2,
        )

    def test_creation_does_not_reuse_managed_aggregate_that_outlives_authorizer(self):
        provider = Tenant.objects.create(name="Expiry Provider", slug="expiry-provider", is_provider=True)
        old_customer = Tenant.objects.create(
            name="Long-lived Managed Customer",
            slug="long-lived-managed-customer",
            managed_by=provider,
        )
        creator = User.objects.create_user(username="expiring-provider-admin", is_staff=True)
        administrator = self._administrator_role(provider)
        own_grant = grant(creator, provider, administrator, granted_by=creator)
        RoleGrant._base_manager.filter(pk=own_grant.pk).update(valid_until=timezone.now() + timedelta(days=1))
        own_grant.refresh_from_db()
        long_lived_grant = RoleGrant.objects.create(
            membership=own_grant.membership,
            role=administrator,
            granted_by=creator,
            reason="Long-lived managed-customer administration",
            valid_until=timezone.now() + timedelta(days=90),
        )
        old_scope = RoleGrantScope.objects.create(
            role_grant=long_lived_grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=old_customer,
        )
        preserved_long_lived_grant = (
            long_lived_grant.granted_by_id,
            long_lived_grant.reason,
            long_lived_grant.granted_at,
            long_lived_grant.valid_until,
        )
        self.assertGreater(long_lived_grant.valid_until, own_grant.valid_until)

        response = self._post_managed_tenant(
            actor=creator,
            active_tenant=provider,
            provider=provider,
            name="Deadline-bound Managed Customer",
            slug="deadline-bound-managed-customer",
        )

        self.assertEqual(response.status_code, 302)
        new_customer = Tenant._base_manager.get(slug="deadline-bound-managed-customer")
        new_scope = RoleGrantScope._base_manager.get(
            role_grant__membership=own_grant.membership,
            role_grant__role=administrator,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant=new_customer,
        )
        self.assertNotEqual(new_scope.role_grant_id, long_lived_grant.pk)
        onboarding_grant = RoleGrant._base_manager.get(pk=new_scope.role_grant_id)
        own_grant.refresh_from_db()
        long_lived_grant.refresh_from_db()
        onboarding_grant.refresh_from_db()
        self.assertIsNotNone(onboarding_grant.valid_until)
        self.assertLessEqual(onboarding_grant.valid_until, own_grant.valid_until)
        self.assertEqual(
            (
                long_lived_grant.granted_by_id,
                long_lived_grant.reason,
                long_lived_grant.granted_at,
                long_lived_grant.valid_until,
            ),
            preserved_long_lived_grant,
        )
        self.assertEqual(
            set(long_lived_grant.scopes.values_list("pk", "scope_type", "tenant_id")),
            {(old_scope.pk, RoleGrantScope.SCOPE_TENANT, old_customer.pk)},
        )
        self.assertEqual(
            set(
                RoleGrant._base_manager.filter(
                    membership=own_grant.membership,
                    role=administrator,
                ).values_list("pk", flat=True)
            ),
            {own_grant.pk, long_lived_grant.pk, onboarding_grant.pk},
        )
        self.assertEqual(
            set(
                RoleGrantScope._base_manager.filter(
                    role_grant__membership=own_grant.membership,
                    role_grant__role=administrator,
                ).values_list("role_grant_id", "scope_type", "tenant_id")
            ),
            {
                (own_grant.pk, RoleGrantScope.SCOPE_OWN, None),
                (long_lived_grant.pk, RoleGrantScope.SCOPE_TENANT, old_customer.pk),
                (onboarding_grant.pk, RoleGrantScope.SCOPE_TENANT, new_customer.pk),
            },
        )
        self.assertEqual(
            set(Membership._base_manager.filter(user=creator).values_list("tenant_id", flat=True)),
            {provider.pk},
        )

        before_retry = self._authorization_fingerprint()
        self.assertEqual(
            onboard_managed_tenant(actor=creator, provider_id=provider.pk, tenant=new_customer),
            new_customer,
        )
        self.assertEqual(self._authorization_fingerprint(), before_retry)

    def test_group_administrator_grant_is_extended_to_new_managed_tenant(self):
        provider = Tenant.objects.create(name="Group Provider", slug="group-provider", is_provider=True)
        creator = User.objects.create_user(username="group-provider-admin", is_staff=True)
        nonmember = User.objects.create_user(username="group-provider-outsider", is_staff=True)
        administrator = self._administrator_role(provider)
        provider_membership = Membership.objects.create(user=creator, tenant=provider)
        administrator_group = UserGroup.objects.create(tenant=provider, name="Provider Administrators")
        GroupMembership.objects.create(
            user_group=administrator_group,
            membership=provider_membership,
            added_by=creator,
        )
        group_grant = RoleGrant.objects.create(user_group=administrator_group, role=administrator, granted_by=creator)
        RoleGrantScope.objects.create(
            role_grant=group_grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )

        self.assertTrue(creator.has_perm("organization.add_tenant", obj=provider))
        self.assertFalse(nonmember.has_perm("organization.add_tenant", obj=provider))
        self.assertEqual(accessible_tenant_ids(creator), {provider.pk})
        response = self._post_managed_tenant(
            actor=creator,
            active_tenant=provider,
            provider=provider,
            name="Group Managed Customer",
            slug="group-managed-customer",
        )

        self.assertEqual(response.status_code, 302)
        customer = Tenant._base_manager.get(slug="group-managed-customer")
        self.assertEqual(
            set(group_grant.scopes.values_list("scope_type", "tenant_id")),
            {
                (RoleGrantScope.SCOPE_OWN, None),
                (RoleGrantScope.SCOPE_TENANT, customer.pk),
            },
        )
        self.assertEqual(RoleGrant.objects.filter(role=administrator).count(), 1)
        self.assertFalse(RoleGrant.objects.filter(role=administrator, membership__user=creator).exists())
        self.assertFalse(Membership._base_manager.filter(user=creator, tenant=customer).exists())

        self.assertIn(customer.pk, accessible_tenant_ids(creator))
        self.assertIn("organization.change_tenant", effective_permissions(creator, customer))
        self.assertTrue(creator.has_perm("organization.change_tenant", obj=customer))
        self.assertEqual(self.client.session.get("active_tenant_id"), customer.pk)
        detail_response = self.client.get(response.url)
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.wsgi_request.active_tenant, customer)

        self.assertNotIn(customer.pk, accessible_tenant_ids(nonmember))
        self.assertNotIn("organization.change_tenant", effective_permissions(nonmember, customer))
        self.assertFalse(nonmember.has_perm("organization.change_tenant", obj=customer))

    def test_tampered_managed_by_query_denies_without_persisting(self):
        provider_a = Tenant.objects.create(name="Authorized Provider", slug="authorized-provider", is_provider=True)
        provider_b = Tenant.objects.create(name="Foreign Provider", slug="foreign-provider", is_provider=True)
        actor = User.objects.create_user(username="provider-a-admin", is_staff=True)
        administrator = self._administrator_role(provider_a)
        grant(actor, provider_a, administrator, granted_by=actor)

        self.assertTrue(actor.has_perm("organization.add_tenant", obj=provider_a))
        self.assertFalse(actor.has_perm("organization.add_tenant", obj=provider_b))
        tenant_count = Tenant._base_manager.count()
        role_grant_count = RoleGrant.objects.count()
        scope_count = RoleGrantScope.objects.count()

        response = self._post_managed_tenant(
            actor=actor,
            active_tenant=provider_a,
            provider=provider_b,
            name="Tampered Customer",
            slug="tampered-customer",
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Tenant._base_manager.filter(slug="tampered-customer").exists())
        self.assertEqual(Tenant._base_manager.count(), tenant_count)
        self.assertEqual(RoleGrant.objects.count(), role_grant_count)
        self.assertEqual(RoleGrantScope.objects.count(), scope_count)
        self.assertEqual(self.client.session.get("active_tenant_id"), provider_a.pk)


class AuthorizationCacheInvalidationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Cache tenant", slug="cache-tenant")
        self.role = Role.objects.create(
            tenant=self.tenant,
            name="Cache reader",
            permissions=["assets.view_asset"],
        )
        self.first_user = User.objects.create_user(username="cache-first")
        self.second_user = User.objects.create_user(username="cache-second")
        self.first_membership = Membership.objects.create(
            user=self.first_user,
            tenant=self.tenant,
        )
        self.second_membership = Membership.objects.create(
            user=self.second_user,
            tenant=self.tenant,
        )

    def _group_with_grant(self, name):
        group = UserGroup.objects.create(tenant=self.tenant, name=name)
        grant = RoleGrant.objects.create(user_group=group, role=self.role)
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        return group, grant

    def test_invalidation_publishes_again_after_transaction_commit(self):
        with mock.patch("core.authorization_cache.cache.set") as cache_set:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                invalidate_user_authorization_cache(self.first_user)

            self.assertEqual(cache_set.call_count, 1)
            self.assertEqual(len(callbacks), 1)
            immediate_version = cache_set.call_args.args[1]
            callbacks[0]()

        self.assertEqual(cache_set.call_count, 2)
        self.assertNotEqual(immediate_version, cache_set.call_args.args[1])

    def test_role_grant_principal_reassignment_invalidates_old_and_new_groups(self):
        first_group, grant = self._group_with_grant("First group")
        second_group = UserGroup.objects.create(tenant=self.tenant, name="Second group")
        GroupMembership.objects.create(
            user_group=first_group,
            membership=self.first_membership,
        )
        GroupMembership.objects.create(
            user_group=second_group,
            membership=self.second_membership,
        )
        self.assertTrue(self.first_user.has_perm("assets.view_asset", obj=self.tenant))
        self.assertFalse(self.second_user.has_perm("assets.view_asset", obj=self.tenant))

        grant.user_group = second_group
        grant.save(update_fields=["user_group"])

        self.assertFalse(self.first_user.has_perm("assets.view_asset", obj=self.tenant))
        self.assertTrue(self.second_user.has_perm("assets.view_asset", obj=self.tenant))

    def test_group_membership_reassignment_invalidates_old_and_new_members(self):
        group, _grant = self._group_with_grant("Movable membership group")
        group_membership = GroupMembership.objects.create(
            user_group=group,
            membership=self.first_membership,
        )
        self.assertTrue(self.first_user.has_perm("assets.view_asset", obj=self.tenant))
        self.assertFalse(self.second_user.has_perm("assets.view_asset", obj=self.tenant))

        group_membership.membership = self.second_membership
        group_membership.save(update_fields=["membership"])

        self.assertFalse(self.first_user.has_perm("assets.view_asset", obj=self.tenant))
        self.assertTrue(self.second_user.has_perm("assets.view_asset", obj=self.tenant))

    def test_membership_user_reassignment_invalidates_old_and_new_users(self):
        self.second_membership.delete()
        grant = RoleGrant.objects.create(
            membership=self.first_membership,
            role=self.role,
        )
        RoleGrantScope.objects.create(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_OWN,
        )
        self.assertTrue(self.first_user.has_perm("assets.view_asset", obj=self.tenant))
        self.assertFalse(self.second_user.has_perm("assets.view_asset", obj=self.tenant))

        self.first_membership.user = self.second_user
        self.first_membership.save(update_fields=["user"])

        self.assertFalse(self.first_user.has_perm("assets.view_asset", obj=self.tenant))
        self.assertTrue(self.second_user.has_perm("assets.view_asset", obj=self.tenant))


class AccessibleTenantResolutionPerformanceTests(TestCase):
    def test_own_scope_resolution_batches_owner_tenant_lookups(self):
        user = User.objects.create_user(username="own-scope-query-budget")
        tenants = []
        for index in range(8):
            tenant = Tenant.objects.create(
                name=f"Own Scope Query Tenant {index}",
                slug=f"own-scope-query-{index}",
            )
            role = Role.objects.create(
                tenant=tenant,
                name=f"Own Scope Query Role {index}",
                permissions=["assets.view_asset"],
            )
            grant(user, tenant, role)
            tenants.append(tenant)

        with CaptureQueriesContext(connection) as queries:
            visible_ids = accessible_tenant_ids(user)

        owner_lookup_queries = [
            query["sql"]
            for query in queries
            if 'FROM "organization_tenant"' in query["sql"]
            and "LIMIT 1" in query["sql"]
        ]
        self.assertEqual(visible_ids, {tenant.pk for tenant in tenants})
        self.assertEqual(owner_lookup_queries, [])
