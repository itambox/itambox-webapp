from __future__ import annotations

import hashlib
import inspect
import re
from collections import Counter
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.context import (
    get_current_all_accessible,
    get_current_membership,
    get_current_tenant,
    get_current_tenant_group,
    set_current_all_accessible,
    set_current_membership,
    set_current_tenant,
    set_current_tenant_group,
)
from core.management.commands import sync_tenant_ldap as command_module
from organization.models import AssetHolder, Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.services import identity_provisioning as organization_identity
from users.models import User

BATCH_ADAPTER_QUERY_CEILING = 80
_QUERY_TABLE_REFERENCE = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO)\s+(?:ONLY\s+)?([\"`]?[^\s,()]+[\"`]?)",
    re.IGNORECASE,
)


def _query_evidence(queries):
    table_counts = Counter()
    verb_table_sequence = []
    for query in queries:
        normalized_sql = " ".join(query["sql"].split())
        verb_match = re.search(r"\b(SELECT|INSERT|UPDATE|DELETE)\b", normalized_sql, re.IGNORECASE)
        verb = verb_match.group(1).upper() if verb_match else "OTHER"
        tables = tuple(table.strip('"`').lower() for table in _QUERY_TABLE_REFERENCE.findall(normalized_sql))
        verb_table_sequence.append((verb, tables))
        for table in tables:
            table_counts[f"{verb}:{table}"] += 1

    sequence = tuple(verb_table_sequence)
    return {
        "query_count": len(queries),
        "table_count_signature": tuple(sorted(table_counts.items())),
        "verb_table_sequence_hash": hashlib.sha256(repr(sequence).encode("utf-8")).hexdigest(),
    }


class _FakeLDAPConnection:
    def __init__(self, *, username="batch-user", email="batch-user@example.invalid", groups=None):
        self.username = username
        self.email = email
        self.groups = groups or ["cn=directory-users,ou=groups,dc=invalid"]
        self.options = []
        self.bound = None
        self.search_args = None
        self.unbound = False
        self._result_calls = 0

    def set_option(self, option, value):
        self.options.append((option, value))

    def simple_bind_s(self, bind_dn, password):
        self.bound = (bind_dn, password)

    def search(self, *args):
        self.search_args = args
        return 41

    def result(self, result_id, timeout):
        del result_id, timeout
        if self._result_calls == 0:
            self._result_calls += 1
            return (
                command_module.ldap.RES_SEARCH_ENTRY,
                [
                    (
                        f"uid={self.username},ou=users,dc=invalid",
                        {
                            "uid": [self.username.encode()],
                            "mail": [self.email.encode()],
                            "givenName": [b"Batch"],
                            "sn": [b"User"],
                            "memberOf": [group.encode() for group in self.groups],
                        },
                    )
                ],
            )
        return None, None

    def unbind_s(self):
        self.unbound = True


class LDAPBatchRestartTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Batch tenant", slug="batch-tenant")
        self.addCleanup(self._clear_context)

    @staticmethod
    def _clear_context():
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(None)
        set_current_all_accessible(False)

    @staticmethod
    def _config(tenant):
        return {
            tenant.slug: {
                "SERVER_URI": "ldap://directory.invalid",
                "BIND_DN": "cn=service,dc=invalid",
                "BIND_PASSWORD": "directory-secret",
                "USER_SEARCH_BASE": "ou=users,dc=invalid",
                "USER_SEARCH_FILTER": "(uid=%(user)s)",
            }
        }

    def _run(self, *, connection=None, username="batch-user", email="batch-user@example.invalid"):
        connection = connection or _FakeLDAPConnection(username=username, email=email)
        output = StringIO()
        with (
            patch.object(command_module, "django_auth_ldap_installed", True),
            patch.object(command_module.ldap, "initialize", return_value=connection),
            override_settings(
                ITAMBOX_TENANT_LDAP_CONFIGS=self._config(self.tenant),
                AUTH_LDAP_USER_SEARCH=None,
                AUTH_LDAP_USER_DN_TEMPLATE=None,
            ),
        ):
            call_command("sync_tenant_ldap", tenant=self.tenant.slug, stdout=output)
        return output.getvalue(), connection

    def test_command_owner_is_batch_only_and_calls_concrete_service_module(self):
        source = inspect.getsource(command_module)
        assert "from organization.services import identity_provisioning" in source
        assert "provision_ldap_directory_identity" in source
        for forbidden in (
            "core.identity_provisioning",
            "core.auth.provisioning",
            "MultiTenantLDAPBackend",
            "Membership",
            "RoleGrant",
            "RoleGrantScope",
            "Permission",
            "LDAP_GRANT_REASON",
            "role_is_privileged",
            "provision_external_identity",
        ):
            assert forbidden not in source

        connection = _FakeLDAPConnection()
        with (
            patch.object(command_module, "django_auth_ldap_installed", True),
            patch.object(command_module.ldap, "initialize", return_value=connection),
            patch.object(organization_identity, "provision_ldap_directory_identity") as provision,
            override_settings(
                ITAMBOX_TENANT_LDAP_CONFIGS=self._config(self.tenant),
                AUTH_LDAP_USER_SEARCH=None,
                AUTH_LDAP_USER_DN_TEMPLATE=None,
            ),
        ):
            command_module.Command(stdout=StringIO())._run_sync(self.tenant)

        provision.assert_called_once()
        command = provision.call_args.args[0]
        assert isinstance(command, organization_identity.LDAPDirectoryIdentityCommand)
        assert command.tenant is self.tenant
        assert isinstance(command.user, User)
        assert command.user.username == "batch-user"
        assert User.objects.filter(username="batch-user").count() == 1
        assert Membership.objects.filter(user__username="batch-user", tenant=self.tenant).count() == 0
        assert Role.objects.filter(tenant=self.tenant).count() == 0
        assert connection.unbound is True

    def test_real_batch_first_repeat_preserve_counts_provenance_and_no_holder(self):
        first_output, first_connection = self._run()
        second_output, second_connection = self._run()

        user = User.objects.get(username="batch-user")
        membership = Membership.objects.get(user=user, tenant=self.tenant)
        role = Role.objects.get(tenant=self.tenant, name="Member")
        grants = list(RoleGrant.objects.filter(membership=membership, role=role))
        assert len(grants) == 1
        grant = grants[0]
        assert grant.reason == organization_identity.LDAP_DIRECTORY_SYNC_REASON
        assert grant.granted_by_id is None
        assert grant.valid_until is not None
        assert not AssetHolder.objects.filter(user=user, tenant=self.tenant).exists()
        assert RoleGrantScope.objects.filter(role_grant=grant, scope_type=RoleGrantScope.SCOPE_OWN).count() == 1
        assert "Created: 1, Updated: 0" in first_output
        assert "Created: 0, Updated: 1" in second_output
        for output in (first_output, second_output):
            assert "batch-user" not in output
            assert "batch-user@example.invalid" not in output
            assert "directory-users" not in output
            assert "Member" not in output
            assert "directory-secret" not in output
        assert first_connection.unbound is True
        assert second_connection.unbound is True

    def test_active_manual_equivalent_is_reused_without_ldap_owned_row(self):
        user = User.objects.create_user(username="batch-user", email="batch-user@example.invalid")
        role = Role.objects.create(
            tenant=self.tenant,
            name="Member",
            permissions=list(organization_identity.LDAP_DIRECTORY_SYNC_MEMBER_PERMISSION_LIST),
        )
        membership = Membership.objects.create(user=user, tenant=self.tenant)
        manual = RoleGrant.objects.create(
            membership=membership,
            role=role,
            granted_by=user,
            reason="operator-approved",
            valid_until=timezone.now() + timedelta(days=2),
        )
        RoleGrantScope.objects.create(role_grant=manual, scope_type=RoleGrantScope.SCOPE_OWN)

        self._run()

        manual.refresh_from_db()
        assert manual.reason == "operator-approved"
        assert manual.granted_by_id == user.pk
        assert not RoleGrant.objects.filter(
            membership=membership,
            reason=organization_identity.LDAP_DIRECTORY_SYNC_REASON,
        ).exists()

    def test_ambiguous_ldap_owned_rows_are_untouched_by_batch(self):
        user = User.objects.create_user(username="batch-user", email="batch-user@example.invalid")
        role = Role.objects.create(
            tenant=self.tenant,
            name="Member",
            permissions=list(organization_identity.LDAP_DIRECTORY_SYNC_MEMBER_PERMISSION_LIST),
        )
        membership = Membership.objects.create(user=user, tenant=self.tenant)
        expiry = timezone.now() + timedelta(hours=3)
        rows = []
        for _ in range(2):
            grant = RoleGrant.objects.create(
                membership=membership,
                role=role,
                reason=organization_identity.LDAP_DIRECTORY_SYNC_REASON,
                granted_by=None,
                valid_until=expiry,
            )
            RoleGrantScope.objects.create(role_grant=grant, scope_type=RoleGrantScope.SCOPE_OWN)
            rows.append(grant.pk)
        before = list(RoleGrant.objects.filter(pk__in=rows).order_by("pk").values_list("pk", "valid_until", "reason"))

        self._run()

        after = list(RoleGrant.objects.filter(pk__in=rows).order_by("pk").values_list("pk", "valid_until", "reason"))
        assert after == before
        assert (
            RoleGrant.objects.filter(
                membership=membership,
                reason=organization_identity.LDAP_DIRECTORY_SYNC_REASON,
            ).count()
            == 2
        )

    def test_expired_manual_equivalent_is_retained_and_new_ldap_row_is_created(self):
        user = User.objects.create_user(username="batch-user", email="batch-user@example.invalid")
        role = Role.objects.create(
            tenant=self.tenant,
            name="Member",
            permissions=list(organization_identity.LDAP_DIRECTORY_SYNC_MEMBER_PERMISSION_LIST),
        )
        membership = Membership.objects.create(user=user, tenant=self.tenant)
        expired = timezone.now() - timedelta(minutes=1)
        manual = RoleGrant.objects.create(
            membership=membership,
            role=role,
            granted_by=user,
            reason="expired-operator-approval",
            valid_until=timezone.now() + timedelta(minutes=1),
        )
        RoleGrant._base_manager.filter(pk=manual.pk).update(valid_until=expired)
        manual.refresh_from_db()
        RoleGrantScope.objects.create(role_grant=manual, scope_type=RoleGrantScope.SCOPE_OWN)

        self._run()

        manual.refresh_from_db()
        assert manual.valid_until == expired
        assert manual.reason == "expired-operator-approval"
        ldap_grants = RoleGrant.objects.filter(
            membership=membership,
            reason=organization_identity.LDAP_DIRECTORY_SYNC_REASON,
            granted_by__isnull=True,
        )
        assert ldap_grants.count() == 1
        assert ldap_grants.first().pk != manual.pk

    def test_batch_query_ceiling_does_not_scale_with_group_count(self):
        short_connection = _FakeLDAPConnection(username="batch-query-short", groups=["g0"])
        many_connection = _FakeLDAPConnection(
            username="batch-query-many",
            groups=[f"g{index}" for index in range(40)],
        )
        self._run(username="batch-query-warmup")
        with CaptureQueriesContext(connection) as short_queries:
            self._run(connection=short_connection, username="batch-query-short")
        with CaptureQueriesContext(connection) as many_queries:
            self._run(connection=many_connection, username="batch-query-many")

        # CaptureQueriesContext measures application PostgreSQL queries only;
        # it does not measure or imply anything about external LDAP traffic.
        short_evidence = _query_evidence(short_queries)
        many_evidence = _query_evidence(many_queries)
        assert short_evidence["query_count"] <= BATCH_ADAPTER_QUERY_CEILING
        assert many_evidence["query_count"] <= BATCH_ADAPTER_QUERY_CEILING
        assert many_evidence["query_count"] == short_evidence["query_count"]
        assert many_evidence["table_count_signature"] == short_evidence["table_count_signature"]
        assert many_evidence["verb_table_sequence_hash"] == short_evidence["verb_table_sequence_hash"]

    def test_task_context_restores_prior_context_when_service_fails(self):
        prior_tenant = SimpleNamespace(pk=1201)
        prior_membership = SimpleNamespace(pk=1202)
        prior_group = SimpleNamespace(pk=1203)
        set_current_tenant(prior_tenant)
        set_current_membership(prior_membership)
        set_current_tenant_group(prior_group)
        set_current_all_accessible(True)
        expected = (
            prior_tenant,
            prior_membership,
            prior_group,
            True,
        )
        connection = _FakeLDAPConnection()
        with (
            patch.object(command_module, "django_auth_ldap_installed", True),
            patch.object(command_module.ldap, "initialize", return_value=connection),
            patch.object(
                organization_identity,
                "provision_ldap_directory_identity",
                side_effect=RuntimeError("service-canary"),
            ),
            override_settings(
                ITAMBOX_TENANT_LDAP_CONFIGS=self._config(self.tenant),
                AUTH_LDAP_USER_SEARCH=None,
                AUTH_LDAP_USER_DN_TEMPLATE=None,
            ),
            pytest.raises(RuntimeError, match="service-canary"),
        ):
            call_command("sync_tenant_ldap", tenant=self.tenant.slug, stdout=StringIO())

        assert (
            get_current_tenant(),
            get_current_membership(),
            get_current_tenant_group(),
            get_current_all_accessible(),
        ) == expected
        assert connection.unbound is True

    def test_batch_keeps_directory_normalization_and_require_group_filter(self):
        connection = _FakeLDAPConnection(groups=["cn=other,ou=groups,dc=invalid"])
        config = self._config(self.tenant)
        config[self.tenant.slug]["REQUIRE_GROUP"] = "cn=required,ou=groups,dc=invalid"
        output = StringIO()
        with (
            patch.object(command_module, "django_auth_ldap_installed", True),
            patch.object(command_module.ldap, "initialize", return_value=connection),
            override_settings(ITAMBOX_TENANT_LDAP_CONFIGS=config),
        ):
            command_module.Command(stdout=output)._run_sync(self.tenant)

        assert not User.objects.filter(username="batch-user").exists()
        assert "Created: 0, Updated: 0" in output.getvalue()
        assert connection.unbound is True
