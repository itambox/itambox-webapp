"""RED/GREEN contract tests for the #443 organization identity writer."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import logging
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from django.db import IntegrityError, connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

import organization.services.identity_provisioning as identity_service
from core.identity_provisioning import (
    ExternalIdentityProfile,
    ExternalIdentityProvisioningCommand,
    ProviderStaffIntent,
)
from core.mfa import role_is_privileged
from core.models import ObjectChange
from core.oidc_identity import oidc_sensitive_audit
from core.tasks.context import TaskContext
from extras.models import Event
from organization.models import AssetHolder, Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.services.identity_provisioning import (
    LDAP_DIRECTORY_SYNC_MEMBER_PERMISSIONS,
    LDAP_DIRECTORY_SYNC_REASON,
    IdentityProvisioningError,
    LDAPDirectoryIdentityCommand,
    OrganizationIdentityProvisioner,
    provision_ldap_directory_identity,
)
from users.models import GroupMembership, User, UserGroup

CANARY_EMAIL = "canary-email-443@example.invalid"
CANARY_UPN = "canary-upn-443@example.invalid"
CANARY_GROUP = "canary-group-443"
CANARY_ROLE = "canary-role-443"
CANARY_DRIVER = "canary-driver-443"


class IdentityServiceCase(TestCase):
    maxDiff = None

    def setUp(self):
        self.provisioner = OrganizationIdentityProvisioner()
        self.provider = Tenant.objects.create(
            name="Provider 443",
            slug="provider-443",
            is_provider=True,
        )
        self.customer = Tenant.objects.create(
            name="Customer 443",
            slug="customer-443",
            managed_by=self.provider,
        )
        self.user = User.objects.create_user(
            username="identity-443",
            email="identity-443@example.invalid",
            first_name="Canonical",
            last_name="User",
        )

    def profile(self, *, source="OIDC", email=None, upn=None, first_name="External", last_name="Identity"):
        return ExternalIdentityProfile(
            source=source,
            email=email or self.user.email,
            upn=upn or f"{self.user.username}@example.invalid",
            first_name=first_name,
            last_name=last_name,
        )

    def command(self, *, user=None, tenant=None, role="Member", profile=None, provider_staff=None):
        return ExternalIdentityProvisioningCommand(
            user=user or self.user,
            customer_tenant=tenant or self.customer,
            profile=profile or self.profile(),
            customer_role_name=role,
            provider_staff=provider_staff,
        )

    def provider_intent(self, *, role_name="ProviderStaff", provider=None):
        return ProviderStaffIntent(provider_tenant=provider or self.provider, role_name=role_name)

    def role(self, tenant, name="Member", permissions=None, **extra):
        return Role.objects.create(
            tenant=tenant,
            name=name,
            permissions=list(permissions if permissions is not None else ["assets.view_asset"]),
            **extra,
        )

    def organization_fingerprint(self):
        payload = {
            "tenants": list(
                Tenant._base_manager.order_by("pk").values_list("pk", "deleted_at", "is_provider", "managed_by_id")
            ),
            "memberships": list(
                Membership._base_manager.order_by("pk").values_list("pk", "user_id", "tenant_id", "is_active")
            ),
            "roles": list(Role._base_manager.order_by("pk").values_list("pk", "tenant_id", "name", "permissions")),
            "grants": list(
                RoleGrant._base_manager.order_by("pk").values_list(
                    "pk", "membership_id", "user_group_id", "role_id", "granted_by_id", "reason", "valid_until"
                )
            ),
            "scopes": list(
                RoleGrantScope._base_manager.order_by("pk").values_list(
                    "pk", "role_grant_id", "scope_type", "tenant_id", "tenant_group_id"
                )
            ),
            "holders": list(
                AssetHolder._base_manager.order_by("pk").values_list(
                    "pk", "user_id", "tenant_id", "upn", "email", "first_name", "last_name", "deleted_at"
                )
            ),
            "groups": list(UserGroup._base_manager.order_by("pk").values_list("pk", "tenant_id", "name", "is_active")),
            "group_memberships": list(
                GroupMembership._base_manager.order_by("pk").values_list(
                    "pk", "user_group_id", "membership_id", "source"
                )
            ),
            "users": list(
                User._base_manager.order_by("pk").values_list(
                    "pk", "username", "email", "first_name", "last_name", "is_active"
                )
            ),
            "object_changes": list(
                ObjectChange._base_manager.order_by("pk").values_list(
                    "pk",
                    "tenant_id",
                    "user_id",
                    "request_id",
                    "action",
                    "changed_object_type_id",
                    "changed_object_id",
                    "object_repr",
                    "prechange_data",
                    "postchange_data",
                )
            ),
            "events": list(
                Event._base_manager.order_by("pk").values_list(
                    "pk", "model_id", "object_id", "action", "data", "processed"
                )
            ),
        }
        encoded = json.dumps(payload, default=str, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest(), payload

    def queries_for(self, command):
        with CaptureQueriesContext(connection) as captured:
            result = self.provisioner.provision(command)
        return result, list(captured.captured_queries)

    def assert_no_sensitive_log_text(self, records):
        for record in records:
            message = record.getMessage() if hasattr(record, "getMessage") else str(record)
            structured = json.dumps(record.__dict__, default=str, sort_keys=True)
            rendered = logging.Formatter(
                "%(levelname)s %(name)s %(message)s "
                "%(source)s %(reason_code)s %(user_id)s %(tenant_id)s "
                "%(customer_tenant_id)s %(provider_tenant_id)s %(role_id)s "
                "%(holder_id)s %(membership_id)s %(exception_type)s",
                defaults={
                    "source": None,
                    "reason_code": None,
                    "user_id": None,
                    "tenant_id": None,
                    "customer_tenant_id": None,
                    "provider_tenant_id": None,
                    "role_id": None,
                    "holder_id": None,
                    "membership_id": None,
                    "exception_type": None,
                },
            ).format(record)
            for text in (message, structured, rendered):
                self.assertNotIn(CANARY_EMAIL, text)
                self.assertNotIn(CANARY_UPN, text)
                self.assertNotIn(CANARY_GROUP, text)
                self.assertNotIn(CANARY_ROLE, text)
                self.assertNotIn(CANARY_DRIVER, text)
            self.assertNotIn(
                "IntegrityError", message.split("exception_type=")[-1] if "exception_type=" in message else ""
            )


class CustomerLifecycleContractTests(IdentityServiceCase):
    def test_first_customer_command_creates_one_role_membership_holder_grant_and_scope(self):
        result = self.provisioner.provision(self.command())

        self.assertEqual(result.mode, "customer")
        self.assertIsNotNone(result.holder_id)
        self.assertIsNotNone(result.membership_id)
        self.assertIsNotNone(result.role_id)
        self.assertEqual(Membership.objects.filter(user=self.user, tenant=self.customer).count(), 1)
        self.assertEqual(AssetHolder.objects.filter(user=self.user, tenant=self.customer).count(), 1)
        self.assertEqual(Role.objects.filter(pk=result.role_id, tenant=self.customer, name="Member").count(), 1)
        grant = RoleGrant.objects.get(membership_id=result.membership_id, role_id=result.role_id)
        self.assertEqual(grant.granted_by_id, None)
        self.assertEqual(list(grant.scopes.values_list("scope_type", flat=True)), [RoleGrantScope.SCOPE_OWN])

    def test_established_nonprivileged_repeat_has_no_organization_writes(self):
        role = self.role(self.customer, permissions=["assets.view_asset"])
        first = self.provisioner.provision(self.command())
        self.assertEqual(first.role_id, role.pk)
        before_hash, before = self.organization_fingerprint()

        with CaptureQueriesContext(connection) as captured:
            result = self.provisioner.provision(self.command())

        after_hash, after = self.organization_fingerprint()
        self.assertEqual(result.mode, "customer")
        self.assertEqual(before_hash, after_hash)
        self.assertEqual(before, after)
        self.assertTrue(
            all(
                q["sql"].lstrip().split(None, 1)[0].upper() in {"SELECT", "SAVEPOINT", "RELEASE"}
                for q in captured.captured_queries
                if q["sql"].lstrip()
            )
        )

    def test_privileged_repeat_refreshes_reason_and_one_day_ttl(self):
        role = self.role(self.customer, name="Member", permissions=["assets.view_asset", "assets.change_asset"])
        first = self.provisioner.provision(self.command())
        grant = RoleGrant.objects.get(membership_id=first.membership_id, role_id=role.pk)
        old_expiry = grant.valid_until
        self.assertTrue(role_is_privileged(role))

        before = timezone.now()
        second = self.provisioner.provision(self.command())
        grant.refresh_from_db()

        self.assertEqual(second.membership_id, first.membership_id)
        self.assertEqual(grant.reason, "OIDC group-claim provisioning")
        self.assertIsNotNone(grant.valid_until)
        self.assertGreater(grant.valid_until, old_expiry)
        self.assertGreaterEqual(grant.valid_until, before + timedelta(hours=23))
        self.assertLessEqual(grant.valid_until, before + timedelta(days=1, minutes=1))

    @override_settings(ITAMBOX_SSO_AUTOCREATE_PRIVILEGED_ROLES=False)
    def test_missing_privileged_role_falls_back_to_member_without_permission_catalog_for_existing_member(self):
        member = self.role(self.customer, name="Member", permissions=["assets.view_asset"])

        result, queries = self.queries_for(self.command(role="Manager"))

        self.assertEqual(result.role_id, member.pk)
        self.assertFalse(Role.objects.filter(tenant=self.customer, name="Manager").exists())
        self.assertFalse(any("auth_permission" in query["sql"].lower() for query in queries))

    def test_missing_role_uses_live_permission_policy_once_and_keeps_description(self):
        result, queries = self.queries_for(self.command(role="Manager"))
        role = Role.objects.get(pk=result.role_id)

        self.assertEqual(role.description, "Auto-provisioned Manager role via OIDC")
        self.assertTrue(role.permissions)
        self.assertEqual(sum("auth_permission" in query["sql"].lower() for query in queries), 1)

        with CaptureQueriesContext(connection) as repeat_queries:
            self.provisioner.provision(self.command(role="Manager"))
        self.assertFalse(any("auth_permission" in query["sql"].lower() for query in repeat_queries.captured_queries))

    def test_caller_user_is_never_created_or_profile_updated(self):
        self.user.first_name = "Original"
        self.user.last_name = "Profile"
        self.user.email = "original-443@example.invalid"
        self.user.save(update_fields=["first_name", "last_name", "email"])
        before = (self.user.pk, self.user.username, self.user.email, self.user.first_name, self.user.last_name)

        self.provisioner.provision(
            self.command(
                profile=self.profile(
                    email="different-443@example.invalid",
                    upn="different-upn-443@example.invalid",
                    first_name="Replacement",
                    last_name="Attempt",
                )
            )
        )

        self.user.refresh_from_db()
        self.assertEqual(
            before,
            (self.user.pk, self.user.username, self.user.email, self.user.first_name, self.user.last_name),
        )


class TenantAndLockContractTests(IdentityServiceCase):
    def test_customer_tenant_sql_is_parameterized_ordered_for_share_and_not_exclusive(self):
        role = self.role(self.customer)
        statements = []

        def wrapper(execute, sql, params, many, context):
            statements.append((sql, params))
            return execute(sql, params, many, context)

        with connection.execute_wrapper(wrapper):
            self.provisioner.provision(self.command())

        tenant_locks = [
            (sql, params)
            for sql, params in statements
            if "organization_tenant" in sql.lower() and "for share" in sql.lower()
        ]
        self.assertEqual(len(tenant_locks), 1)
        sql, params = tenant_locks[0]
        self.assertIn("ORDER BY", sql.upper())
        self.assertNotIn("FOR UPDATE", sql.upper())
        self.assertNotIn("FOR KEY SHARE", sql.upper())
        self.assertIn("%s", sql)
        self.assertEqual(list(params), [self.customer.pk])
        self.assertEqual(role.pk, Role.objects.get(tenant=self.customer, name="Member").pk)

    def test_provider_tenant_sql_has_exact_sorted_ids_and_liveness_columns(self):
        provider_role = self.role(self.provider, name="ProviderStaff")
        statements = []

        def wrapper(execute, sql, params, many, context):
            statements.append((sql, params))
            return execute(sql, params, many, context)

        with connection.execute_wrapper(wrapper):
            self.provisioner.provision(self.command(provider_staff=self.provider_intent(role_name=provider_role.name)))

        tenant_locks = [
            (sql, params)
            for sql, params in statements
            if "organization_tenant" in sql.lower() and "for share" in sql.lower()
        ]
        self.assertEqual(len(tenant_locks), 1)
        sql, params = tenant_locks[0]
        self.assertEqual(list(params), sorted([self.customer.pk, self.provider.pk]))
        self.assertIn('SELECT "id", "deleted_at", "is_provider", "managed_by_id"', sql)
        self.assertIn("ORDER BY", sql.upper())
        self.assertIn("FOR SHARE", sql.upper())
        self.assertNotIn("FOR UPDATE", sql.upper())
        self.assertNotIn("FOR KEY SHARE", sql.upper())

    def test_missing_deleted_duplicate_and_contradictory_tenant_sets_fail_before_any_write(self):
        deleted = Tenant.objects.create(name="Deleted 443", slug="deleted-443")
        Tenant._base_manager.filter(pk=deleted.pk).update(deleted_at=timezone.now())
        wrong_provider = Tenant.objects.create(name="Wrong Provider 443", slug="wrong-provider-443", is_provider=True)
        cases = (
            ("missing customer", self.command(tenant=SimpleNamespace(pk=99999991))),
            ("deleted customer", self.command(tenant=deleted)),
            (
                "duplicate customer/provider id",
                self.command(provider_staff=self.provider_intent(provider=self.customer, role_name="Missing")),
            ),
            (
                "contradictory provider relationship",
                self.command(provider_staff=self.provider_intent(provider=wrong_provider, role_name="Missing")),
            ),
        )
        for label, command in cases:
            with self.subTest(label=label):
                before_hash, before = self.organization_fingerprint()
                with self.assertRaises(IdentityProvisioningError):
                    self.provisioner.provision(command)
                after_hash, after = self.organization_fingerprint()
                self.assertEqual(before_hash, after_hash)
                self.assertEqual(before, after)

    def test_ordinary_customer_path_queries_group_membership_zero_times(self):
        self.role(self.customer)
        statements = []

        def wrapper(execute, sql, params, many, context):
            statements.append(sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(wrapper):
            self.provisioner.provision(self.command())

        self.assertEqual(sum("users_groupmembership" in sql.lower() for sql in statements), 0)

    def test_provider_path_uses_one_combined_membership_and_one_query_per_lock_table(self):
        self.role(self.customer)
        self.role(self.provider, name="ProviderStaff")
        Membership.objects.create(user=self.user, tenant=self.customer)
        statements = []

        def wrapper(execute, sql, params, many, context):
            statements.append(sql)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(wrapper):
            self.provisioner.provision(self.command(provider_staff=self.provider_intent()))

        counts = {
            "tenant": sum("for share" in sql.lower() and "organization_tenant" in sql.lower() for sql in statements),
            "membership": sum(
                "organization_membership" in sql.lower() and "for update" in sql.lower() for sql in statements
            ),
            "group_membership": sum(
                "users_groupmembership" in sql.lower() and "for update" in sql.lower() for sql in statements
            ),
            "grant": sum("organization_rolegrant" in sql.lower() and "for update" in sql.lower() for sql in statements),
            "role": sum(
                'from "organization_role"' in sql.lower() and "for update" in sql.lower() for sql in statements
            ),
            "scope": sum(
                "organization_rolegrantscope" in sql.lower() and "for update" in sql.lower() for sql in statements
            ),
            "holder": sum(
                "organization_assetholder" in sql.lower() and "for update" in sql.lower() for sql in statements
            ),
        }
        self.assertEqual(counts["tenant"], 1)
        self.assertEqual(counts["membership"], 1)
        self.assertEqual(counts["group_membership"], 1)
        self.assertLessEqual(counts["grant"], 1)
        self.assertEqual(counts["role"], 1)
        self.assertLessEqual(counts["scope"], 1)
        self.assertEqual(counts["holder"], 1)

    def test_query_work_does_not_scale_with_unrelated_users_or_managed_tenants(self):
        self.role(self.customer)
        base_result, base_queries = self.queries_for(self.command())
        for index in range(12):
            other = User.objects.create_user(username=f"other-{index}", email=f"other-{index}@example.invalid")
            Membership.objects.create(user=other, tenant=self.customer)
        for index in range(12):
            Tenant.objects.create(
                name=f"Managed {index}",
                slug=f"managed-{index}",
                managed_by=self.provider,
            )

        repeat_result, repeat_queries = self.queries_for(self.command())
        self.assertEqual(base_result.membership_id, repeat_result.membership_id)
        self.assertLessEqual(len(repeat_queries), len(base_queries) + 2)
        self.assertLessEqual(
            sum("organization_membership" in query["sql"].lower() for query in repeat_queries),
            1,
        )


class HolderReconciliationTests(IdentityServiceCase):
    def test_existing_linked_user_wins_over_upn_and_email_candidates(self):
        linked = AssetHolder.objects.create(
            user=self.user,
            tenant=self.customer,
            upn="linked-upn-443@example.invalid",
            email="linked-443@example.invalid",
            first_name="Linked",
            last_name="Holder",
        )
        AssetHolder.objects.create(
            user=None,
            tenant=self.customer,
            upn="incoming-upn-443@example.invalid",
            email="incoming-443@example.invalid",
            first_name="UPN",
            last_name="Candidate",
        )

        result = self.provisioner.provision(
            self.command(
                profile=self.profile(
                    email="incoming-443@example.invalid",
                    upn="incoming-upn-443@example.invalid",
                )
            )
        )

        self.assertEqual(result.holder_id, linked.pk)
        linked.refresh_from_db()
        self.assertEqual(linked.user_id, self.user.pk)

    def test_exact_unlinked_upn_is_linked(self):
        candidate = AssetHolder.objects.create(
            user=None,
            tenant=self.customer,
            upn="exact-upn-443@example.invalid",
            email="old-443@example.invalid",
            first_name="Old",
            last_name="Directory",
        )

        result = self.provisioner.provision(
            self.command(profile=self.profile(email="new-443@example.invalid", upn=candidate.upn))
        )

        self.assertEqual(result.holder_id, candidate.pk)
        candidate.refresh_from_db()
        self.assertEqual(candidate.user_id, self.user.pk)

    def test_email_only_hint_with_different_upn_is_not_adopted_but_grants_continue(self):
        candidate = AssetHolder.objects.create(
            user=None,
            tenant=self.customer,
            upn=CANARY_UPN,
            email=CANARY_EMAIL,
            first_name="Ambiguous",
            last_name="Holder",
        )

        with self.assertLogs("itambox.organization.identity", level="WARNING") as logs:
            result = self.provisioner.provision(
                self.command(
                    profile=self.profile(
                        email=CANARY_EMAIL,
                        upn="different-authoritative-upn-443@example.invalid",
                    )
                )
            )

        self.assertIsNone(result.holder_id)
        candidate.refresh_from_db()
        self.assertIsNone(candidate.user_id)
        self.assertEqual(Membership.objects.filter(pk=result.membership_id).count(), 1)
        self.assertEqual(RoleGrant.objects.filter(membership_id=result.membership_id).count(), 1)
        self.assert_no_sensitive_log_text(logs.records)

    def test_linked_other_user_is_never_stolen(self):
        other = User.objects.create_user(username="other-holder-443", email="other-holder-443@example.invalid")
        candidate = AssetHolder.objects.create(
            user=other,
            tenant=self.customer,
            upn=CANARY_UPN,
            email=CANARY_EMAIL,
            first_name="Other",
            last_name="Owner",
        )

        result = self.provisioner.provision(self.command(profile=self.profile(email=CANARY_EMAIL, upn=CANARY_UPN)))

        self.assertIsNone(result.holder_id)
        candidate.refresh_from_db()
        self.assertEqual(candidate.user_id, other.pk)

    def test_holder_integrity_conflict_rereads_only_user_or_upn_and_links_same_upn(self):
        exact = AssetHolder.objects.create(
            user=None,
            tenant=self.customer,
            upn="race-upn-443@example.invalid",
            email="race-443@example.invalid",
            first_name="Race",
            last_name="Holder",
        )
        real_save = AssetHolder.save
        calls = {"count": 0}

        def conflict_once(instance, *args, **kwargs):
            if instance.pk == exact.pk and calls["count"] == 0:
                calls["count"] += 1
                raise IntegrityError(CANARY_DRIVER)
            return real_save(instance, *args, **kwargs)

        with mock.patch.object(AssetHolder, "save", new=conflict_once):
            result = self.provisioner.provision(
                self.command(profile=self.profile(email="race-443@example.invalid", upn=exact.upn))
            )

        exact.refresh_from_db()
        self.assertEqual(result.holder_id, exact.pk)
        self.assertEqual(exact.user_id, self.user.pk)


class GrantConvergenceTests(IdentityServiceCase):
    def test_duplicate_same_role_own_grants_converge_and_nonown_scopes_survive(self):
        role = self.role(self.customer)
        membership = Membership.objects.create(user=self.user, tenant=self.customer)
        first = RoleGrant.objects.create(membership=membership, role=role)
        second = RoleGrant.objects.create(membership=membership, role=role)
        RoleGrantScope.objects.create(role_grant=first, scope_type=RoleGrantScope.SCOPE_OWN)
        RoleGrantScope.objects.create(role_grant=second, scope_type=RoleGrantScope.SCOPE_OWN)
        RoleGrantScope._base_manager.bulk_create(
            [
                RoleGrantScope(
                    role_grant_id=second.pk,
                    scope_type=RoleGrantScope.SCOPE_TENANT,
                    tenant_id=self.customer.pk,
                )
            ]
        )

        result = self.provisioner.provision(self.command())

        grants = list(RoleGrant.objects.filter(membership=membership, role=role).order_by("pk"))
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0].pk, first.pk)
        self.assertEqual(
            set(RoleGrantScope.objects.filter(role_grant=grants[0]).values_list("scope_type", flat=True)),
            {RoleGrantScope.SCOPE_OWN, RoleGrantScope.SCOPE_TENANT},
        )
        self.assertEqual(result.membership_id, membership.pk)

    def test_same_role_nonown_only_historical_grants_converge_and_keep_every_scope(self):
        role = self.role(self.customer)
        membership = Membership.objects.create(user=self.user, tenant=self.customer)
        legacy = RoleGrant.objects.create(membership=membership, role=role, reason="legacy non-own")
        duplicate = RoleGrant.objects.create(membership=membership, role=role, reason="legacy own")
        RoleGrantScope._base_manager.bulk_create(
            [
                RoleGrantScope(
                    role_grant_id=legacy.pk,
                    scope_type=RoleGrantScope.SCOPE_TENANT,
                    tenant_id=self.customer.pk,
                )
            ]
        )
        RoleGrantScope.objects.create(role_grant=duplicate, scope_type=RoleGrantScope.SCOPE_OWN)

        result = self.provisioner.provision(self.command())

        grants = list(RoleGrant._base_manager.filter(membership=membership, role=role).order_by("pk"))
        self.assertEqual([grant.pk for grant in grants], [legacy.pk])
        self.assertEqual(
            set(RoleGrantScope._base_manager.filter(role_grant=legacy).values_list("scope_type", flat=True)),
            {RoleGrantScope.SCOPE_OWN, RoleGrantScope.SCOPE_TENANT},
        )
        self.assertEqual(result.membership_id, membership.pk)

    def test_customer_reconciliation_does_not_mutate_inactive_provider_membership_children(self):
        customer_role = self.role(self.customer)
        provider_role = self.role(self.provider, name="ProviderStaff")
        provider_membership = Membership.objects.create(user=self.user, tenant=self.provider, is_active=False)
        provider_grant = RoleGrant.objects.create(
            membership=provider_membership,
            role=provider_role,
            reason="historical provider grant",
        )
        RoleGrantScope.objects.create(role_grant=provider_grant, scope_type=RoleGrantScope.SCOPE_OWN)
        RoleGrantScope._base_manager.create(
            role_grant_id=provider_grant.pk,
            scope_type=RoleGrantScope.SCOPE_ALL_MANAGED,
        )
        before_grant = tuple(
            RoleGrant._base_manager.filter(pk=provider_grant.pk)
            .values_list("pk", "membership_id", "role_id", "granted_by_id", "reason", "valid_until")
            .get()
        )
        before_scopes = tuple(
            RoleGrantScope._base_manager.filter(role_grant=provider_grant)
            .order_by("pk")
            .values_list("pk", "role_grant_id", "scope_type", "tenant_id", "tenant_group_id")
        )

        result = self.provisioner.provision(self.command())

        self.assertEqual(result.mode, "customer")
        self.assertEqual(
            tuple(
                RoleGrant._base_manager.filter(pk=provider_grant.pk)
                .values_list("pk", "membership_id", "role_id", "granted_by_id", "reason", "valid_until")
                .get()
            ),
            before_grant,
        )
        self.assertEqual(
            tuple(
                RoleGrantScope._base_manager.filter(role_grant=provider_grant)
                .order_by("pk")
                .values_list("pk", "role_grant_id", "scope_type", "tenant_id", "tenant_group_id")
            ),
            before_scopes,
        )
        self.assertEqual(Role.objects.get(pk=result.role_id).pk, customer_role.pk)

    def test_conflicting_own_grant_with_nonown_scope_is_preserved_without_own_scope(self):
        target = self.role(self.customer, name="Member")
        other = self.role(self.customer, name="Other", permissions=["assets.view_asset"])
        membership = Membership.objects.create(user=self.user, tenant=self.customer)
        conflicting = RoleGrant.objects.create(membership=membership, role=other)
        RoleGrantScope.objects.create(role_grant=conflicting, scope_type=RoleGrantScope.SCOPE_OWN)
        RoleGrantScope._base_manager.bulk_create(
            [
                RoleGrantScope(
                    role_grant_id=conflicting.pk,
                    scope_type=RoleGrantScope.SCOPE_TENANT,
                    tenant_id=self.customer.pk,
                )
            ]
        )

        self.provisioner.provision(self.command())

        self.assertFalse(
            RoleGrantScope.objects.filter(role_grant=conflicting, scope_type=RoleGrantScope.SCOPE_OWN).exists()
        )
        self.assertTrue(
            RoleGrantScope.objects.filter(role_grant=conflicting, scope_type=RoleGrantScope.SCOPE_TENANT).exists()
        )
        self.assertEqual(RoleGrant.objects.filter(membership=membership, role=target).count(), 1)

    def test_own_scope_uniqueness_conflict_retries_exact_scope(self):
        role = self.role(self.customer)
        membership = Membership.objects.create(user=self.user, tenant=self.customer)
        grant = RoleGrant.objects.create(membership=membership, role=role)
        scope = RoleGrantScope.objects.create(role_grant=grant, scope_type=RoleGrantScope.SCOPE_OWN)
        real_create = RoleGrantScope._base_manager.create
        calls = {"count": 0}

        def conflict_once(**kwargs):
            if calls["count"] == 0 and kwargs.get("role_grant_id") == grant.pk:
                calls["count"] += 1
                raise IntegrityError("scope conflict")
            return real_create(**kwargs)

        with mock.patch.object(RoleGrantScope._base_manager, "create", side_effect=conflict_once):
            result = self.provisioner.provision(self.command())

        self.assertEqual(result.membership_id, membership.pk)
        self.assertEqual(RoleGrantScope.objects.filter(pk=scope.pk).count(), 1)


class ProviderContractTests(IdentityServiceCase):
    def provider_setup(self):
        provider_role = self.role(self.provider, name="ProviderStaff", permissions=["assets.view_asset"])
        customer_role = self.role(self.customer)
        customer_membership = Membership.objects.create(user=self.user, tenant=self.customer)
        customer_grant = RoleGrant.objects.create(membership=customer_membership, role=customer_role)
        RoleGrantScope.objects.create(role_grant=customer_grant, scope_type=RoleGrantScope.SCOPE_OWN)
        group = UserGroup.objects.create(tenant=self.customer, name=CANARY_GROUP)
        GroupMembership.objects.create(
            user_group=group,
            membership=customer_membership,
            source=GroupMembership.SOURCE_OIDC,
        )
        holder = AssetHolder.objects.create(
            user=self.user,
            tenant=self.customer,
            upn="provider-transition-443@example.invalid",
            email="provider-transition-443@example.invalid",
            first_name="Transition",
            last_name="Holder",
        )
        provider_membership = Membership.objects.create(user=self.user, tenant=self.provider, is_active=False)
        provider_other_grant = RoleGrant.objects.create(membership=provider_membership, role=provider_role)
        RoleGrantScope.objects.create(role_grant=provider_other_grant, scope_type=RoleGrantScope.SCOPE_OWN)
        unrelated = User.objects.create_user(username="unrelated-443", email="unrelated-443@example.invalid")
        unrelated_membership = Membership.objects.create(user=unrelated, tenant=self.customer)
        unrelated_holder = AssetHolder.objects.create(
            user=unrelated,
            tenant=self.customer,
            upn="unrelated-443@example.invalid",
            email="unrelated-443@example.invalid",
            first_name="Unrelated",
            last_name="Holder",
        )
        return (
            provider_role,
            customer_membership,
            group,
            holder,
            provider_membership,
            unrelated,
            unrelated_membership,
            unrelated_holder,
        )

    def test_invalid_provider_mapping_is_terminal_zero_write_existing_user(self):
        self.role(self.customer)
        before_hash, before = self.organization_fingerprint()
        result = self.provisioner.provision(
            self.command(provider_staff=self.provider_intent(role_name="MissingProviderRole"))
        )
        after_hash, after = self.organization_fingerprint()

        self.assertEqual(result.mode, "provider_mapping_rejected")
        self.assertEqual(before_hash, after_hash)
        self.assertEqual(before, after)
        self.assertEqual(Membership.objects.filter(user=self.user).count(), 0)

    def test_invalid_provider_mapping_is_terminal_for_a_new_canonical_user_too(self):
        new_user = User.objects.create_user(username="new-rejected-443", email="new-rejected-443@example.invalid")
        before_hash, _ = self.organization_fingerprint()

        result = self.provisioner.provision(
            self.command(
                user=new_user,
                provider_staff=self.provider_intent(role_name="MissingProviderRole"),
            )
        )

        after_hash, _ = self.organization_fingerprint()
        self.assertEqual(result.mode, "provider_mapping_rejected")
        self.assertEqual(before_hash, after_hash)
        self.assertEqual(Membership.objects.filter(user=new_user).count(), 0)
        self.assertEqual(AssetHolder.objects.filter(user=new_user).count(), 0)

    def test_provider_transition_deletes_customer_aggregate_unlinks_holders_and_keeps_unrelated_identity(self):
        (
            provider_role,
            customer_membership,
            group,
            holder,
            provider_membership,
            unrelated,
            unrelated_membership,
            unrelated_holder,
        ) = self.provider_setup()

        result = self.provisioner.provision(
            self.command(provider_staff=self.provider_intent(role_name=provider_role.name))
        )

        self.assertEqual(result.mode, "provider_staff")
        provider_membership.refresh_from_db()
        self.assertTrue(provider_membership.is_active)
        self.assertFalse(RoleGrant.objects.filter(membership=provider_membership).exists())
        self.assertFalse(Membership.objects.filter(pk=customer_membership.pk).exists())
        holder.refresh_from_db()
        self.assertIsNone(holder.user_id)
        self.assertFalse(GroupMembership.objects.filter(user_group=group).exists())
        self.assertTrue(Membership.objects.filter(pk=unrelated_membership.pk).exists())
        unrelated_holder.refresh_from_db()
        self.assertEqual(unrelated_holder.user_id, unrelated.pk)

    def test_customer_command_is_sticky_provider_only_after_provider_membership_exists(self):
        (
            provider_role,
            customer_membership,
            group,
            holder,
            provider_membership,
            unrelated,
            unrelated_membership,
            unrelated_holder,
        ) = self.provider_setup()
        provider_membership.is_active = True
        provider_membership.save(update_fields=["is_active"])
        before_customer = Membership.objects.filter(user=self.user, tenant=self.customer).count()

        result = self.provisioner.provision(self.command())

        self.assertEqual(result.mode, "provider_staff")
        self.assertEqual(Membership.objects.filter(user=self.user, tenant=self.customer).count(), before_customer)
        self.assertEqual(
            AssetHolder.objects.filter(user=self.user, tenant=self.customer, user__isnull=False).count(), 1
        )

    def test_provider_transition_failure_after_write_rolls_back_exact_organization_fingerprint(self):
        self.provider_setup()
        before_hash, before = self.organization_fingerprint()

        with (
            mock.patch(
                "organization.services.identity_provisioning._stage_checkpoint",
                side_effect=lambda stage: (
                    (_ for _ in ()).throw(RuntimeError(stage)) if stage == "provider.customer_retired" else None
                ),
            ),
            self.assertRaises(RuntimeError),
        ):
            self.provisioner.provision(self.command(provider_staff=self.provider_intent(role_name="ProviderStaff")))

        after_hash, after = self.organization_fingerprint()
        self.assertEqual(before_hash, after_hash)
        self.assertEqual(before, after)


class LDAPDirectoryContractTests(IdentityServiceCase):
    def ldap_command(self, *, user=None, tenant=None):
        return LDAPDirectoryIdentityCommand(user=user or self.user, tenant=tenant or self.customer)

    def test_batch_directory_sync_has_fixed_member_role_and_no_asset_holder(self):
        before_holders = AssetHolder.objects.count()
        provision_ldap_directory_identity(self.ldap_command())

        role = Role.objects.get(tenant=self.customer, name="Member")
        membership = Membership.objects.get(user=self.user, tenant=self.customer)
        grant = RoleGrant.objects.get(membership=membership, role=role)

        self.assertEqual(role.description, "Default Standard Member")
        self.assertEqual(set(role.permissions), set(LDAP_DIRECTORY_SYNC_MEMBER_PERMISSIONS))
        self.assertEqual(grant.reason, LDAP_DIRECTORY_SYNC_REASON)
        self.assertIsNone(grant.granted_by_id)
        self.assertTrue(role_is_privileged(role))
        self.assertIsNotNone(grant.valid_until)
        self.assertEqual(AssetHolder.objects.count(), before_holders)

    def test_batch_repeat_refreshes_only_ldap_owned_grant_origin(self):
        provision_ldap_directory_identity(self.ldap_command())
        grant = RoleGrant.objects.get(membership__user=self.user, reason=LDAP_DIRECTORY_SYNC_REASON)
        old_pk = grant.pk
        old_expiry = grant.valid_until

        provision_ldap_directory_identity(self.ldap_command())

        refreshed = RoleGrant.objects.get(pk=old_pk)
        self.assertEqual(
            RoleGrant.objects.filter(membership=grant.membership, reason=LDAP_DIRECTORY_SYNC_REASON).count(), 1
        )
        self.assertGreater(refreshed.valid_until, old_expiry)
        self.assertEqual(refreshed.granted_by_id, None)

    def test_active_manual_equivalent_is_reused_without_creating_ldap_owned_row(self):
        role = self.role(self.customer, name="Member", permissions=list(LDAP_DIRECTORY_SYNC_MEMBER_PERMISSIONS))
        membership = Membership.objects.create(user=self.user, tenant=self.customer)
        manual = RoleGrant.objects.create(
            membership=membership,
            role=role,
            reason="manual equivalent 443",
            valid_until=timezone.now() + timedelta(days=2),
            granted_by=self.user,
        )
        RoleGrantScope.objects.create(role_grant=manual, scope_type=RoleGrantScope.SCOPE_OWN)

        provision_ldap_directory_identity(self.ldap_command())

        self.assertFalse(RoleGrant.objects.filter(membership=membership, reason=LDAP_DIRECTORY_SYNC_REASON).exists())
        manual.refresh_from_db()
        self.assertEqual(manual.reason, "manual equivalent 443")
        self.assertEqual(manual.granted_by_id, self.user.pk)

    def test_expired_manual_equivalent_is_retained_and_new_ldap_grant_is_created(self):
        role = self.role(self.customer, name="Member", permissions=["assets.view_asset"])
        membership = Membership.objects.create(user=self.user, tenant=self.customer)
        manual = RoleGrant.objects.create(
            membership=membership,
            role=role,
            reason="manual expired 443",
            valid_until=timezone.now() - timedelta(minutes=1),
            granted_by=self.user,
        )
        RoleGrantScope.objects.create(role_grant=manual, scope_type=RoleGrantScope.SCOPE_OWN)

        provision_ldap_directory_identity(self.ldap_command())

        manual.refresh_from_db()
        self.assertLess(manual.valid_until, timezone.now())
        ldap_grant = RoleGrant.objects.get(membership=membership, reason=LDAP_DIRECTORY_SYNC_REASON)
        self.assertIsNone(ldap_grant.granted_by_id)

    def test_ambiguous_ldap_owned_rows_are_untouched(self):
        role = self.role(self.customer, name="Member", permissions=list(LDAP_DIRECTORY_SYNC_MEMBER_PERMISSIONS))
        membership = Membership.objects.create(user=self.user, tenant=self.customer)
        first = RoleGrant.objects.create(
            membership=membership,
            role=role,
            reason=LDAP_DIRECTORY_SYNC_REASON,
            valid_until=timezone.now() + timedelta(days=1),
            granted_by=None,
        )
        second = RoleGrant.objects.create(
            membership=membership,
            role=role,
            reason=LDAP_DIRECTORY_SYNC_REASON,
            valid_until=timezone.now() + timedelta(days=1),
            granted_by=None,
        )
        RoleGrantScope.objects.create(role_grant=first, scope_type=RoleGrantScope.SCOPE_OWN)
        RoleGrantScope.objects.create(role_grant=second, scope_type=RoleGrantScope.SCOPE_OWN)
        before_hash, before = self.organization_fingerprint()

        provision_ldap_directory_identity(self.ldap_command())

        after_hash, after = self.organization_fingerprint()
        self.assertEqual(before_hash, after_hash)
        self.assertEqual(before, after)


class LoggingAndFailureContractTests(IdentityServiceCase):
    def test_holder_integrity_conflict_is_sanitized_and_grants_continue(self):
        self.role(self.customer)
        with (
            mock.patch.object(AssetHolder, "save", side_effect=IntegrityError(CANARY_DRIVER)),
            self.assertLogs("itambox.organization.identity", level="WARNING") as logs,
        ):
            result = self.provisioner.provision(
                self.command(
                    profile=self.profile(email=CANARY_EMAIL, upn=CANARY_UPN),
                )
            )

        self.assertIsNone(result.holder_id)
        self.assertEqual(Membership.objects.filter(pk=result.membership_id).count(), 1)
        self.assertEqual(RoleGrant.objects.filter(membership_id=result.membership_id).count(), 1)
        self.assert_no_sensitive_log_text(logs.records)

    def test_missing_or_unsaved_canonical_user_fails_before_any_organization_write(self):
        unsaved = User(username="unsaved-443")
        before_hash, before = self.organization_fingerprint()

        with self.assertRaises(IdentityProvisioningError):
            self.provisioner.provision(self.command(user=unsaved))

        after_hash, after = self.organization_fingerprint()
        self.assertEqual(before_hash, after_hash)
        self.assertEqual(before, after)

    def test_service_contract_has_no_direct_any_and_documents_oidc_binding_handoff(self):
        source = inspect.getsource(identity_service)
        tree = ast.parse(source)
        self.assertNotIn("from typing import Any", source)
        self.assertFalse(any(isinstance(node, ast.Name) and node.id == "Any" for node in ast.walk(tree)))
        signature = inspect.signature(OrganizationIdentityProvisioner.provision)
        self.assertEqual(tuple(signature.parameters), ("self", "command"))
        docstring = inspect.getdoc(OrganizationIdentityProvisioner.provision) or ""
        self.assertIn("Tenant FOR SHARE", docstring)
        self.assertIn("must not pre-lock User or Tenant", docstring)
        self.assertNotIn("skip_user_lock", docstring)
        self.assertNotIn("skip_tenant_lock", docstring)

    def test_structured_log_fields_and_rendered_output_are_redacted(self):
        self.role(self.customer)
        AssetHolder.objects.create(
            user=None,
            tenant=self.customer,
            upn="structured-log-other-upn-443@example.invalid",
            email=CANARY_EMAIL,
            first_name="Structured",
            last_name="Canary",
        )
        with self.assertLogs("itambox.organization.identity", level="WARNING") as logs:
            result = self.provisioner.provision(
                self.command(
                    profile=self.profile(
                        email=CANARY_EMAIL,
                        upn=CANARY_UPN,
                    )
                )
            )

        self.assertEqual(result.mode, "customer")
        self.assertTrue(logs.records)
        for record in logs.records:
            self.assertEqual(record.__dict__.get("reason_code"), "holder_email_hint_rejected")
        self.assert_no_sensitive_log_text(logs.records)

    def test_task_and_oidc_context_produce_truthful_redacted_audit_for_success(self):
        actor = User.objects.create_superuser(
            username="audit-actor-443",
            email="audit-actor-443@example.invalid",
            password="not-used-443",
        )
        before_user = tuple(
            User._base_manager.filter(pk=self.user.pk).values_list("pk", "username", "email", "first_name", "last_name")
        )
        before_changes = ObjectChange._base_manager.count()
        before_events = Event._base_manager.count()

        with TaskContext(tenant_id=self.customer.pk, user_id=actor.pk), oidc_sensitive_audit():
            result = self.provisioner.provision(
                self.command(
                    profile=self.profile(email=CANARY_EMAIL, upn=CANARY_UPN),
                )
            )

        self.assertEqual(result.mode, "customer")
        changes = list(ObjectChange._base_manager.filter(pk__gt=before_changes).order_by("pk"))
        events = list(Event._base_manager.filter(pk__gt=before_events).order_by("pk"))
        self.assertTrue(changes)
        self.assertTrue(all(change.user_id == actor.pk for change in changes))
        self.assertTrue(all(change.tenant_id == self.customer.pk for change in changes))
        audit_text = json.dumps(
            [
                {
                    "object_repr": change.object_repr,
                    "prechange_data": change.prechange_data,
                    "postchange_data": change.postchange_data,
                }
                for change in changes
            ],
            default=str,
            sort_keys=True,
        )
        event_text = json.dumps([event.data for event in events], default=str, sort_keys=True)
        self.assertNotIn(CANARY_EMAIL, audit_text)
        self.assertNotIn(CANARY_UPN, audit_text)
        self.assertNotIn(CANARY_EMAIL, event_text)
        self.assertNotIn(CANARY_UPN, event_text)
        self.assertEqual(
            before_user,
            tuple(
                User._base_manager.filter(pk=self.user.pk).values_list(
                    "pk", "username", "email", "first_name", "last_name"
                )
            ),
        )

    def test_provider_rejection_is_zero_write_in_full_task_oidc_fingerprint(self):
        actor = User.objects.create_superuser(
            username="reject-actor-443",
            email="reject-actor-443@example.invalid",
            password="not-used-443",
        )
        before_hash, before = self.organization_fingerprint()
        with TaskContext(tenant_id=self.customer.pk, user_id=actor.pk), oidc_sensitive_audit():
            result = self.provisioner.provision(
                self.command(provider_staff=self.provider_intent(role_name=CANARY_ROLE))
            )
        after_hash, after = self.organization_fingerprint()
        self.assertEqual(result.mode, "provider_mapping_rejected")
        self.assertEqual(before_hash, after_hash)
        self.assertEqual(before, after)

    def test_tenant_relationship_validation_is_before_all_writes(self):
        invalid_provider = Tenant.objects.create(name="Not Provider", slug="not-provider")
        before_hash, before = self.organization_fingerprint()

        with self.assertRaises(IdentityProvisioningError):
            self.provisioner.provision(
                self.command(
                    provider_staff=self.provider_intent(provider=invalid_provider, role_name="Missing"),
                )
            )

        after_hash, after = self.organization_fingerprint()
        self.assertEqual(before_hash, after_hash)
        self.assertEqual(before, after)


@pytest.mark.django_db(transaction=True)
def test_service_module_exposes_identity_provisioner_contract_and_separate_ldap_api():
    from core.identity_provisioning import IdentityProvisioner
    from organization.services.identity_provisioning import OrganizationIdentityProvisioner

    assert issubclass(OrganizationIdentityProvisioner, object)
    assert hasattr(IdentityProvisioner, "provision")
    assert LDAPDirectoryIdentityCommand is not ExternalIdentityProvisioningCommand
    assert callable(provision_ldap_directory_identity)
