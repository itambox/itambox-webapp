from __future__ import annotations

import hashlib
import inspect
import re
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import authenticate as django_authenticate
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from core import identity_provisioning, tenant_scope
from core.auth import PasswordLoginOnlyBackend
from core.auth import ldap as ldap_module
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
from core.identity_provisioning import ExternalIdentityProvisioningCommand
from organization.models import AssetHolder, Membership, RoleGrant, RoleGrantScope, Tenant
from organization.services.identity_provisioning import organization_identity_provisioner

User = get_user_model()

INTERACTIVE_ADAPTER_QUERY_CEILING = 60
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


class _CapturingIdentityProvisioner:
    def __init__(self):
        self.commands = []

    def provision(self, command):
        self.commands.append(command)
        return identity_provisioning.ExternalIdentityProvisioningResult(mode="customer")


class _LDAPUserProxy:
    def __init__(self, attrs, groups=None, *, group_error=None):
        self.attrs = attrs
        self._groups = groups or []
        self._group_error = group_error

    @property
    def group_names(self):
        if self._group_error is not None:
            raise self._group_error
        return self._groups

    @property
    def group_dns(self):
        return self._groups


class LDAPAdapterRestartTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Adapter tenant", slug="adapter-tenant")
        self.user = User.objects.create_user(
            username="directory-user",
            email="local@example.invalid",
            password="local-password",
        )
        self.addCleanup(self._clear_context)

    @staticmethod
    def _clear_context():
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(None)
        set_current_all_accessible(False)

    @staticmethod
    def _config(tenant, *, mapping=None):
        config = {
            tenant.slug: {
                "SERVER_URI": "ldap://directory.invalid",
                "BIND_DN": "cn=service,dc=invalid",
                "BIND_PASSWORD": "directory-secret",
                "USER_SEARCH": {
                    "base_dn": "ou=users,dc=invalid",
                    "filter": "(uid=%(user)s)",
                    "scope": "SUBTREE",
                },
            }
        }
        if mapping is not None:
            config[tenant.slug]["LDAP_GROUP_ROLE_MAPPING"] = mapping
        return config

    @staticmethod
    def _context_values():
        return (
            get_current_tenant(),
            get_current_membership(),
            get_current_tenant_group(),
            get_current_all_accessible(),
        )

    def _authenticate(self, *, user=None, username=None, config=None, groups=None, attrs=None, group_error=None):
        user = user or self.user
        user.ldap_user = _LDAPUserProxy(
            attrs
            or {
                "userPrincipalName": [b"directory-user@adapter.invalid"],
                "mail": [b"directory-user@adapter.invalid"],
                "givenName": [b"Directory"],
                "sn": [b"User"],
            },
            groups=groups,
            group_error=group_error,
        )
        backend = ldap_module.MultiTenantLDAPBackend()
        username = username or user.username
        config = config if config is not None else self._config(self.tenant)
        with (
            override_settings(
                ITAMBOX_TENANT_LDAP_CONFIGS=config,
                AUTH_LDAP_USER_SEARCH=None,
                AUTH_LDAP_USER_DN_TEMPLATE=None,
            ),
            patch.object(ldap_module.LDAPBackend, "authenticate", return_value=user) as parent_authenticate,
        ):
            result = backend.authenticate(
                request=None,
                username=username,
                password="directory-password",
            )
            return backend, parent_authenticate, result

    def test_production_owner_is_sdk_free_and_has_no_legacy_organization_edges(self):
        source = inspect.getsource(ldap_module)

        assert "from core import identity_provisioning, tenant_scope" in source
        for forbidden in (
            "organization.models",
            "organization.forms",
            "organization.services",
            "core.auth.provisioning",
            "django.contrib.auth.models import Permission",
            "AssetHolder",
            "Membership",
            "RoleGrant",
            "get_permissions_for_role",
        ):
            assert forbidden not in source
        assert not hasattr(ldap_module.MultiTenantLDAPBackend, "get_permissions_for_role")

    def test_operation_time_tenant_model_owner_is_used_for_suffix_inference(self):
        set_current_tenant(None)
        with (
            patch.object(ldap_module.tenant_scope, "tenant_model", wraps=tenant_scope.tenant_model) as model_owner,
            patch.object(ldap_module.LDAPBackend, "authenticate", return_value=None),
            override_settings(
                ITAMBOX_TENANT_LDAP_CONFIGS={},
                AUTH_LDAP_USER_SEARCH=None,
                AUTH_LDAP_USER_DN_TEMPLATE=None,
            ),
        ):
            result = ldap_module.MultiTenantLDAPBackend().authenticate(
                request=None,
                username="unknown@adapter-tenant.invalid",
                password="directory-password",
            )

        assert result is None
        assert model_owner.call_count == 1
        assert get_current_tenant() is None

    def test_malformed_config_is_unconfigured_and_restores_complete_prior_context(self):
        prior_tenant = SimpleNamespace(pk=914, slug="prior")
        prior_membership = SimpleNamespace(pk=915)
        prior_group = SimpleNamespace(pk=916)
        set_current_tenant(prior_tenant)
        set_current_membership(prior_membership)
        set_current_tenant_group(prior_group)
        set_current_all_accessible(True)
        expected = self._context_values()
        with (
            patch.object(ldap_module.LDAPBackend, "authenticate", return_value=self.user),
            patch.object(identity_provisioning, "provision_external_identity") as provision,
            override_settings(
                ITAMBOX_TENANT_LDAP_CONFIGS=["not-a-config-map"],
                AUTH_LDAP_USER_SEARCH=None,
                AUTH_LDAP_USER_DN_TEMPLATE=None,
            ),
        ):
            result = ldap_module.MultiTenantLDAPBackend().authenticate(
                request=None,
                username=self.user.username,
                password="directory-password",
            )

        assert result is None
        assert self._context_values() == expected
        provision.assert_not_called()

    def test_normalized_facts_build_exact_sdk_free_command_and_call_wrapper_once(self):
        set_current_tenant(self.tenant)
        groups = [b"group-member", b"group-admin", b"group-manager"]
        mapping = {
            "group-member": "Member",
            "group-admin": "Admin",
            "group-manager": "Manager",
        }
        port_module = getattr(ldap_module, "identity_provisioning", identity_provisioning)
        with patch.object(port_module, "provision_external_identity") as provision:
            _, parent_authenticate, result = self._authenticate(
                config=self._config(self.tenant, mapping=mapping),
                groups=groups,
            )

        assert result is self.user
        parent_authenticate.assert_called_once()
        provision.assert_called_once()
        command = provision.call_args.args[0]
        assert isinstance(command, ExternalIdentityProvisioningCommand)
        assert command.user is self.user
        assert command.customer_tenant is self.tenant
        assert command.provider_staff is None
        assert command.customer_role_name == "Admin"
        assert command.profile.source == "LDAP"
        assert command.profile.email == "directory-user@adapter.invalid"
        assert command.profile.upn == "directory-user@adapter.invalid"
        assert command.profile.first_name == "Directory"
        assert command.profile.last_name == "User"
        assert not hasattr(command, "groups")
        assert not hasattr(command, "settings")
        assert not hasattr(command, "credentials")
        assert not hasattr(command, "claims")

    def test_public_authenticate_uses_group_dns_fallback_and_normalizes_proxy_bytes(self):
        set_current_tenant(self.tenant)
        provider = _CapturingIdentityProvisioner()
        group_error = RuntimeError("provider group details")
        mapping = {"directory-fallback-admin": "Admin"}

        with (
            identity_provisioning.override_identity_provisioner(provider),
            override_settings(
                ITAMBOX_TENANT_LDAP_CONFIGS=self._config(self.tenant, mapping=mapping),
                AUTH_LDAP_USER_SEARCH=None,
                AUTH_LDAP_USER_DN_TEMPLATE=None,
            ),
        ):
            result = self._authenticate(
                config=self._config(self.tenant, mapping=mapping),
                groups=[b"directory-fallback-admin"],
                group_error=group_error,
                attrs={
                    "userPrincipalName": [b"directory-fallback@adapter.invalid"],
                    "mail": [b"directory-fallback@example.invalid"],
                    "givenName": [b"Fallback"],
                    "sn": [b"User"],
                },
            )[2]

        assert result is self.user
        assert len(provider.commands) == 1
        command = provider.commands[0]
        assert command.profile.email == "directory-fallback@example.invalid"
        assert command.profile.upn == "directory-fallback@adapter.invalid"
        assert command.profile.first_name == "Fallback"
        assert command.profile.last_name == "User"
        assert command.customer_role_name == "Admin"
        assert self._context_values() == (self.tenant, None, None, False)

    def test_role_mapping_falls_back_to_member_without_matching_group(self):
        set_current_tenant(self.tenant)
        port_module = getattr(ldap_module, "identity_provisioning", identity_provisioning)
        with patch.object(port_module, "provision_external_identity") as provision:
            self._authenticate(
                config=self._config(self.tenant, mapping={"known-group": "Admin"}),
                groups=[b"unknown-group"],
            )

        assert provision.call_args.args[0].customer_role_name == "Member"

    def test_success_preserves_inferred_tenant_and_prior_other_context(self):
        set_current_tenant(None)
        prior_membership = SimpleNamespace(pk=901)
        prior_group = SimpleNamespace(pk=902)
        set_current_membership(prior_membership)
        set_current_tenant_group(prior_group)
        set_current_all_accessible(False)
        with patch.object(identity_provisioning, "provision_external_identity"):
            result = self._authenticate(
                username="directory-user@adapter-tenant.invalid",
                config=self._config(self.tenant),
            )[2]

        assert result is self.user
        assert get_current_tenant().pk == self.tenant.pk
        assert get_current_membership() is prior_membership
        assert get_current_tenant_group() is prior_group
        assert get_current_all_accessible() is False

    def test_every_unsuccessful_path_restores_complete_prior_context(self):
        prior_tenant = SimpleNamespace(pk=903, slug="prior")
        prior_membership = SimpleNamespace(pk=904)
        prior_group = SimpleNamespace(pk=905)
        set_current_tenant(prior_tenant)
        set_current_membership(prior_membership)
        set_current_tenant_group(prior_group)
        set_current_all_accessible(True)

        cases = {
            "unconfigured": ({}, None, None),
            "auth_none": (self._config(self.tenant), None, self.tenant),
            "can_login_false": (self._config(self.tenant), User.objects.create_user(username="blocked"), self.tenant),
        }
        cases["can_login_false"][1].can_login = False
        for name, (config, user, current_tenant) in cases.items():
            with self.subTest(name=name):
                set_current_tenant(current_tenant or prior_tenant)
                set_current_membership(prior_membership)
                set_current_tenant_group(prior_group)
                set_current_all_accessible(True)
                expected_case = self._context_values()
                if name == "unconfigured":
                    with patch.object(ldap_module.LDAPBackend, "authenticate", return_value=self.user):
                        with override_settings(
                            ITAMBOX_TENANT_LDAP_CONFIGS=config,
                            AUTH_LDAP_USER_SEARCH=None,
                            AUTH_LDAP_USER_DN_TEMPLATE=None,
                        ):
                            result = ldap_module.MultiTenantLDAPBackend().authenticate(
                                request=None,
                                username="blocked@adapter-tenant.invalid",
                                password="directory-password",
                            )
                elif name == "auth_none":
                    with (
                        patch.object(ldap_module.LDAPBackend, "authenticate", return_value=None),
                        override_settings(
                            ITAMBOX_TENANT_LDAP_CONFIGS=config,
                            AUTH_LDAP_USER_SEARCH=None,
                            AUTH_LDAP_USER_DN_TEMPLATE=None,
                        ),
                    ):
                        result = ldap_module.MultiTenantLDAPBackend().authenticate(
                            request=None,
                            username=self.user.username,
                            password="directory-password",
                        )
                else:
                    with (
                        patch.object(ldap_module.LDAPBackend, "authenticate", return_value=user),
                        override_settings(
                            ITAMBOX_TENANT_LDAP_CONFIGS=config,
                            AUTH_LDAP_USER_SEARCH=None,
                            AUTH_LDAP_USER_DN_TEMPLATE=None,
                        ),
                    ):
                        result = ldap_module.MultiTenantLDAPBackend().authenticate(
                            request=None,
                            username=user.username,
                            password="directory-password",
                        )
                assert result is None
                assert self._context_values() == expected_case

    def test_suffix_miss_and_deleted_suffix_restore_context_without_port_call(self):
        prior_tenant = SimpleNamespace(pk=906, slug="prior")
        prior_membership = SimpleNamespace(pk=907)
        prior_group = SimpleNamespace(pk=908)
        set_current_tenant(prior_tenant)
        set_current_membership(prior_membership)
        set_current_tenant_group(prior_group)
        set_current_all_accessible(True)
        expected = self._context_values()
        deleted = Tenant.objects.create(name="Deleted suffix", slug="deleted-suffix")
        deleted.delete()
        port_module = getattr(ldap_module, "identity_provisioning", identity_provisioning)

        with patch.object(port_module, "provision_external_identity") as provision:
            for username in ("user@missing-suffix.invalid", "user@deleted-suffix.invalid"):
                with (
                    self.subTest(username=username),
                    override_settings(
                        ITAMBOX_TENANT_LDAP_CONFIGS={},
                        AUTH_LDAP_USER_SEARCH=None,
                        AUTH_LDAP_USER_DN_TEMPLATE=None,
                    ),
                ):
                    result = ldap_module.MultiTenantLDAPBackend().authenticate(
                        request=None,
                        username=username,
                        password="directory-password",
                    )
                    assert result is None
                    assert self._context_values() == expected
            provision.assert_not_called()

    def test_optional_dependency_unavailable_restores_context_and_never_calls_port(self):
        prior_tenant = SimpleNamespace(pk=909)
        prior_membership = SimpleNamespace(pk=910)
        prior_group = SimpleNamespace(pk=911)
        set_current_tenant(prior_tenant)
        set_current_membership(prior_membership)
        set_current_tenant_group(prior_group)
        set_current_all_accessible(True)
        expected = self._context_values()
        port_module = getattr(ldap_module, "identity_provisioning", identity_provisioning)

        with (
            patch.object(ldap_module, "django_auth_ldap_installed", False),
            patch.object(port_module, "provision_external_identity") as provision,
        ):
            result = ldap_module.MultiTenantLDAPBackend().authenticate(
                request=None,
                username="user@adapter-tenant.invalid",
                password="directory-password",
            )

        assert result is None
        assert self._context_values() == expected
        provision.assert_not_called()

    def test_can_login_false_returns_none_without_port_call_and_restores_context(self):
        blocked = User.objects.create_user(username="blocked-user", can_login=False)
        blocked.ldap_user = _LDAPUserProxy({"mail": [b"blocked@adapter.invalid"]}, [b"admin-group"])
        set_current_tenant(None)
        set_current_membership(SimpleNamespace(pk=912))
        set_current_tenant_group(SimpleNamespace(pk=913))
        set_current_all_accessible(True)
        expected = self._context_values()
        port_module = getattr(ldap_module, "identity_provisioning", identity_provisioning)

        with (
            patch.object(ldap_module.LDAPBackend, "authenticate", return_value=blocked),
            patch.object(port_module, "provision_external_identity") as provision,
            override_settings(
                ITAMBOX_TENANT_LDAP_CONFIGS={"adapter-tenant": self._config(self.tenant)["adapter-tenant"]},
                AUTH_LDAP_USER_SEARCH=None,
                AUTH_LDAP_USER_DN_TEMPLATE=None,
            ),
        ):
            set_current_tenant(self.tenant)
            expected = self._context_values()
            result = ldap_module.MultiTenantLDAPBackend().authenticate(
                request=None,
                username=blocked.username,
                password="directory-password",
            )

        assert result is None
        assert self._context_values() == expected
        provision.assert_not_called()

    def test_provisioning_exception_restores_context_without_logging_exception_text(self):
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(None)
        set_current_all_accessible(False)
        port_module = getattr(ldap_module, "identity_provisioning", identity_provisioning)
        with (
            patch.object(ldap_module.LDAPBackend, "authenticate", return_value=self.user),
            patch.object(port_module, "provision_external_identity", side_effect=RuntimeError("provider-canary")),
            override_settings(
                ITAMBOX_TENANT_LDAP_CONFIGS=self._config(self.tenant),
                AUTH_LDAP_USER_SEARCH=None,
                AUTH_LDAP_USER_DN_TEMPLATE=None,
            ),
        ):
            try:
                ldap_module.MultiTenantLDAPBackend().authenticate(
                    request=None,
                    username="directory-user@adapter-tenant.invalid",
                    password="directory-password",
                )
            except RuntimeError as exc:
                assert str(exc) == "provider-canary"
            else:
                raise AssertionError("expected provisioning failure")

        assert self._context_values() == (None, None, None, False)

    def test_improper_configuration_log_contains_only_exception_type(self):
        set_current_tenant(self.tenant)
        canary = "settings-canary-value"
        with (
            patch.object(
                ldap_module.LDAPBackend,
                "authenticate",
                side_effect=ImproperlyConfigured(canary),
            ),
            self.assertLogs("django_auth_ldap", level="DEBUG") as logs,
            override_settings(
                ITAMBOX_TENANT_LDAP_CONFIGS=self._config(self.tenant),
                AUTH_LDAP_USER_SEARCH=None,
                AUTH_LDAP_USER_DN_TEMPLATE=None,
            ),
        ):
            result = ldap_module.MultiTenantLDAPBackend().authenticate(
                request=None,
                username=self.user.username,
                password="directory-password",
            )

        assert result is None
        rendered = " ".join(logs.output)
        assert canary not in rendered
        assert logs.records[0].__dict__["exception_type"] == "ImproperlyConfigured"
        assert "directory-user" not in rendered

    def test_holder_lifecycle_is_service_owned_and_adapter_delegates_once(self):
        set_current_tenant(self.tenant)
        port_module = getattr(ldap_module, "identity_provisioning", identity_provisioning)
        with (
            identity_provisioning.override_identity_provisioner(organization_identity_provisioner),
            patch.object(
                port_module,
                "provision_external_identity",
                wraps=port_module.provision_external_identity,
            ) as provision,
        ):
            result = self._authenticate(
                config=self._config(self.tenant, mapping={"directory-admin": "Admin"}),
                groups=[b"directory-admin"],
            )[2]

        assert result is self.user
        provision.assert_called_once()
        assert AssetHolder.objects.filter(user=self.user, tenant=self.tenant).count() == 1
        membership = Membership.objects.get(user=self.user, tenant=self.tenant)
        assert RoleGrant.objects.filter(membership=membership).count() == 1
        grant = RoleGrant.objects.get(membership=membership)
        assert list(grant.scopes.values_list("scope_type", flat=True)) == [RoleGrantScope.SCOPE_OWN]

    def test_interactive_query_ceiling_does_not_scale_with_group_count(self):
        set_current_tenant(self.tenant)
        warmup_user = User.objects.create_user(username="query-warmup")
        short_user = User.objects.create_user(username="query-short")
        many_user = User.objects.create_user(username="query-many")
        warmup_user.ldap_user = _LDAPUserProxy({"mail": [b"query-warmup@example.invalid"]}, [b"warmup"])
        short_user.ldap_user = _LDAPUserProxy({"mail": [b"query-short@example.invalid"]}, [b"g0"])
        many_user.ldap_user = _LDAPUserProxy(
            {"mail": [b"query-many@example.invalid"]},
            [f"g{index}".encode() for index in range(40)],
        )
        config = self._config(self.tenant)
        with (
            identity_provisioning.override_identity_provisioner(organization_identity_provisioner),
            patch.object(ldap_module.LDAPBackend, "authenticate", side_effect=[warmup_user, short_user, many_user]),
            override_settings(
                ITAMBOX_TENANT_LDAP_CONFIGS=config,
                AUTH_LDAP_USER_SEARCH=None,
                AUTH_LDAP_USER_DN_TEMPLATE=None,
            ),
        ):
            ldap_module.MultiTenantLDAPBackend().authenticate(
                request=None,
                username=warmup_user.username,
                password="directory-password",
            )
            with CaptureQueriesContext(connection) as short_queries:
                ldap_module.MultiTenantLDAPBackend().authenticate(
                    request=None,
                    username=short_user.username,
                    password="directory-password",
                )
            with CaptureQueriesContext(connection) as many_queries:
                ldap_module.MultiTenantLDAPBackend().authenticate(
                    request=None,
                    username=many_user.username,
                    password="directory-password",
                )

        # CaptureQueriesContext measures application PostgreSQL queries only;
        # it does not measure or imply anything about external LDAP traffic.
        short_evidence = _query_evidence(short_queries)
        many_evidence = _query_evidence(many_queries)
        assert short_evidence["query_count"] <= INTERACTIVE_ADAPTER_QUERY_CEILING
        assert many_evidence["query_count"] <= INTERACTIVE_ADAPTER_QUERY_CEILING
        assert many_evidence["query_count"] == short_evidence["query_count"]
        assert many_evidence["table_count_signature"] == short_evidence["table_count_signature"]
        assert many_evidence["verb_table_sequence_hash"] == short_evidence["verb_table_sequence_hash"]

        local_user = User.objects.create_user(
            username="local@chain-tenant.example",
            email="local-chain@example.invalid",
            password="chain-password",
        )
        chain_tenant = Tenant.objects.create(name="Chain tenant", slug="chain-tenant")
        set_current_tenant(None)
        set_current_membership(None)
        set_current_tenant_group(None)
        set_current_all_accessible(False)
        port_module = getattr(ldap_module, "identity_provisioning", identity_provisioning)

        with (
            patch.object(port_module, "provision_external_identity") as provision,
            patch.object(ldap_module.LDAPBackend, "authenticate") as parent_authenticate,
            override_settings(
                ITAMBOX_TENANT_LDAP_CONFIGS={chain_tenant.slug: {"SERVER_URI": "ldap://invalid"}},
                AUTH_LDAP_USER_SEARCH=None,
                AUTH_LDAP_USER_DN_TEMPLATE=None,
            ),
        ):
            result = django_authenticate(
                request=None,
                username=local_user.username,
                password="chain-password",
            )

        assert result is not None
        assert result.pk == local_user.pk
        assert get_current_tenant() is None
        assert get_current_membership() is None
        assert get_current_tenant_group() is None
        assert get_current_all_accessible() is False
        parent_authenticate.assert_not_called()
        provision.assert_not_called()

    def test_full_chain_none_restores_context_before_real_password_backend(self):
        local_user = User.objects.create_user(username="user@acme", password="local-password")
        acme = Tenant.objects.create(name="Acme", slug="acme")
        prior_membership = SimpleNamespace(pk=1001)
        prior_group = SimpleNamespace(pk=1002)
        set_current_tenant(None)
        set_current_membership(prior_membership)
        set_current_tenant_group(prior_group)
        set_current_all_accessible(True)
        expected_context = (None, prior_membership, prior_group, True)
        parent_context = []
        password_context = []

        def parent_authenticate(*args, **kwargs):
            del args, kwargs
            parent_context.append((get_current_tenant().pk, prior_membership, prior_group, True))
            local_user.ldap_user = _LDAPUserProxy({"mail": [b"user@acme"]})
            return None

        real_password_authenticate = PasswordLoginOnlyBackend.authenticate

        def password_authenticate(password_backend, request, username=None, password=None, **kwargs):
            password_context.append(self._context_values())
            return real_password_authenticate(
                password_backend,
                request,
                username=username,
                password=password,
                **kwargs,
            )

        port_module = getattr(ldap_module, "identity_provisioning", identity_provisioning)
        with (
            patch.object(ldap_module, "django_auth_ldap_installed", True),
            patch.object(
                ldap_module.LDAPBackend, "authenticate", side_effect=parent_authenticate
            ) as parent_authenticate_mock,
            patch.object(
                PasswordLoginOnlyBackend,
                "authenticate",
                autospec=True,
                side_effect=password_authenticate,
            ) as password_authenticate_mock,
            patch.object(port_module, "provision_external_identity") as provision,
            override_settings(
                AUTHENTICATION_BACKENDS=[
                    "core.auth.ldap.MultiTenantLDAPBackend",
                    "core.auth.PasswordLoginOnlyBackend",
                ],
                ITAMBOX_TENANT_LDAP_CONFIGS=self._config(acme),
                AUTH_LDAP_USER_SEARCH=None,
                AUTH_LDAP_USER_DN_TEMPLATE=None,
            ),
        ):
            result = django_authenticate(request=None, username=local_user.username, password="local-password")

        assert result.pk == local_user.pk
        assert parent_authenticate_mock.call_count == 1
        assert parent_context == [(acme.pk, prior_membership, prior_group, True)]
        assert password_authenticate_mock.call_count == 1
        assert password_context == [expected_context]
        assert self._context_values() == expected_context
        provision.assert_not_called()

    def test_full_chain_provider_error_is_typed_restored_and_local_password_still_works(self):
        local_user = User.objects.create_user(username="error@acme", password="local-password")
        acme = Tenant.objects.create(name="Acme error", slug="acme")
        prior_membership = SimpleNamespace(pk=1011)
        prior_group = SimpleNamespace(pk=1012)
        set_current_tenant(None)
        set_current_membership(prior_membership)
        set_current_tenant_group(prior_group)
        set_current_all_accessible(True)
        expected_context = (None, prior_membership, prior_group, True)
        provider_error = ldap_module.ldap.SERVER_DOWN("provider-secret")

        def parent_authenticate(*args, **kwargs):
            del args, kwargs
            assert get_current_tenant().pk == acme.pk
            local_user.ldap_user = _LDAPUserProxy({"mail": [b"error@acme"]})
            raise provider_error

        port_module = getattr(ldap_module, "identity_provisioning", identity_provisioning)
        with (
            patch.object(ldap_module, "django_auth_ldap_installed", True),
            patch.object(
                ldap_module.LDAPBackend, "authenticate", side_effect=parent_authenticate
            ) as parent_authenticate_mock,
            patch.object(port_module, "provision_external_identity") as provision,
            override_settings(
                ITAMBOX_TENANT_LDAP_CONFIGS=self._config(acme),
                AUTH_LDAP_USER_SEARCH=None,
                AUTH_LDAP_USER_DN_TEMPLATE=None,
            ),
            self.assertRaises(ldap_module.LDAPUnavailableError) as raised,
        ):
            ldap_module.MultiTenantLDAPBackend().authenticate(
                request=None,
                username=local_user.username,
                password="local-password",
            )

        assert parent_authenticate_mock.call_count == 1
        assert isinstance(raised.exception, ldap_module.LDAPUnavailableError)
        assert raised.exception.cause_type == type(provider_error).__name__
        assert "provider-secret" not in str(raised.exception)
        assert self._context_values() == expected_context
        provision.assert_not_called()

        result = PasswordLoginOnlyBackend().authenticate(
            request=None,
            username=local_user.username,
            password="local-password",
        )
        assert result.pk == local_user.pk
        assert self._context_values() == expected_context
