from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import authenticate as django_authenticate
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from core import identity_provisioning, tenant_scope
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

    def _authenticate(self, *, user=None, username=None, config=None, groups=None, attrs=None):
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
        short_user = User.objects.create_user(username="query-short")
        many_user = User.objects.create_user(username="query-many")
        short_user.ldap_user = _LDAPUserProxy({"mail": [b"query-short@example.invalid"]}, [b"g0"])
        many_user.ldap_user = _LDAPUserProxy(
            {"mail": [b"query-many@example.invalid"]},
            [f"g{index}".encode() for index in range(40)],
        )
        config = self._config(self.tenant)
        with (
            identity_provisioning.override_identity_provisioner(organization_identity_provisioner),
            patch.object(ldap_module.LDAPBackend, "authenticate", side_effect=[short_user, many_user]),
            override_settings(
                ITAMBOX_TENANT_LDAP_CONFIGS=config,
                AUTH_LDAP_USER_SEARCH=None,
                AUTH_LDAP_USER_DN_TEMPLATE=None,
            ),
        ):
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

        assert len(short_queries) <= 60
        assert len(many_queries) <= 60
        assert len(many_queries) <= len(short_queries) + 2

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
