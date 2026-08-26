"""Issue #443 SAML adapter contract tests.

These tests keep the protocol adapter responsible for SAML facts and the
SDK-free identity port responsible for Organization lifecycle writes.
"""

from __future__ import annotations

import ast
import logging
import sys
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from core import identity_provisioning
from core.auth import saml as saml_module
from core.managers import set_current_tenant
from organization.models import AssetHolder, Membership, Role, RoleGrant, RoleGrantScope, Tenant
from organization.services.identity_provisioning import organization_identity_provisioner

User = get_user_model()

SAML_CONFIG = {
    "entityid": "https://saml.example.invalid/metadata/",
    "base_url": "https://saml.example.invalid",
    "metadata": {"local": []},
    "SAML_GROUP_ROLE_MAPPING": {
        "saml-admin-group": "ADMIN",
        "saml-manager-group": "manager",
        "saml-member-group": "member",
    },
}


class SAMLAdapterContractTests(TestCase):
    def setUp(self):
        set_current_tenant(None)
        self.tenant = Tenant.objects.create(name="SAML Customer", slug="saml-customer")
        self.user = User.objects.create_user(
            username="saml-adapter-user",
            email="stored@example.invalid",
            first_name="Stored",
            last_name="Name",
        )
        self.backend = saml_module.TenantSaml2Backend()
        self.addCleanup(set_current_tenant, None)

    def session_info(self, *, groups=None):
        return {
            "ava": {
                "mail": [b"assertion@example.invalid"],
                "User.FirstName": [b"Assertion"],
                "last_name": [b"User"],
                "uid": [b"assertion-upn@example.invalid"],
                "memberOf": groups if groups is not None else [b"saml-admin-group"],
            },
            "credentials": "credential-canary-must-not-cross",
        }

    def activate_tenant(self):
        set_current_tenant(self.tenant)

    @override_settings(ITAMBOX_TENANT_SAML_CONFIGS={"saml-customer": SAML_CONFIG})
    def test_operation_uses_call_time_tenant_model_seam(self):
        marker = object()

        class QuerySet:
            def filter(self, **kwargs):
                self.kwargs = kwargs
                return self

            def first(self):
                return marker

        class Manager:
            def filter(self, **kwargs):
                query = QuerySet()
                return query.filter(**kwargs)

        class TenantModel:
            _base_manager = Manager()

        with patch.object(saml_module.tenant_scope, "tenant_model", return_value=TenantModel) as tenant_model:
            self.assertIs(saml_module._live_tenant("saml-customer"), marker)

        tenant_model.assert_called_once_with()

    def test_production_adapter_has_no_domain_model_form_or_legacy_provisioning_edge(self):
        source = open(saml_module.__file__, encoding="utf-8").read()
        tree = ast.parse(source)
        imports = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertNotIn("organization.models", imports)
        self.assertNotIn("organization.forms.role_form", imports)
        self.assertNotIn("get_permissions_for_role", source)
        self.assertNotIn("AssetHolder", source)
        self.assertNotIn("Membership", source)
        self.assertNotIn("RoleGrant", source)

    @override_settings(ITAMBOX_TENANT_SAML_CONFIGS={"saml-customer": SAML_CONFIG})
    def test_normalizes_saml_facts_and_calls_identity_port_once_with_exact_dto(self):
        self.activate_tenant()
        result = identity_provisioning.ExternalIdentityProvisioningResult(
            mode="customer",
            holder_id=41,
            membership_id=42,
            role_id=43,
        )

        with patch.object(identity_provisioning, "provision_external_identity", return_value=result) as provision:
            returned = self.backend.sync_saml_user_profile_and_memberships(self.user, self.session_info())

        self.assertIs(returned, result)
        provision.assert_called_once()
        command = provision.call_args.args[0]
        self.assertIsInstance(command, identity_provisioning.ExternalIdentityProvisioningCommand)
        self.assertIs(command.user, self.user)
        self.assertIs(command.customer_tenant, self.tenant)
        self.assertIsNone(command.provider_staff)
        self.assertEqual(command.customer_role_name, "Admin")
        self.assertEqual(
            command.profile,
            identity_provisioning.ExternalIdentityProfile(
                source="SAML",
                email="assertion@example.invalid",
                upn="assertion-upn@example.invalid",
                first_name="Assertion",
                last_name="User",
            ),
        )
        rendered = repr(command)
        self.assertNotIn("saml-admin-group", rendered)
        self.assertNotIn("credential-canary-must-not-cross", rendered)

    @override_settings(ITAMBOX_TENANT_SAML_CONFIGS={"saml-customer": SAML_CONFIG})
    def test_alias_order_bytes_and_member_fallback_are_preserved(self):
        self.activate_tenant()
        result = Mock()
        cases = (
            (
                {
                    "email": [b"first@example.invalid"],
                    "mail": [b"second@example.invalid"],
                    "givenName": [b"Given"],
                    "first_name": [b"OtherGiven"],
                    "sn": [b"Surname"],
                    "upn": [b"first-upn"],
                    "userPrincipalName": [b"second-upn"],
                    "groups": [b"saml-manager-group", b"saml-admin-group"],
                },
                "Admin",
                "first@example.invalid",
                "first-upn",
            ),
            ({"User.Groups": [b"unmapped-group"]}, "Member", "stored@example.invalid", "stored@example.invalid"),
        )

        for ava, expected_role, expected_email, expected_upn in cases:
            with self.subTest(expected_role=expected_role):
                session_info = {"ava": ava}
                with patch.object(
                    identity_provisioning, "provision_external_identity", return_value=result
                ) as provision:
                    self.backend.sync_saml_user_profile_and_memberships(self.user, session_info)
                command = provision.call_args.args[0]
                self.assertEqual(command.customer_role_name, expected_role)
                self.assertEqual(command.profile.email, expected_email)
                self.assertEqual(command.profile.upn, expected_upn)

    @override_settings(ITAMBOX_TENANT_SAML_CONFIGS={"saml-customer": SAML_CONFIG})
    def test_group_count_does_not_add_adapter_queries_or_port_calls(self):
        self.activate_tenant()
        result = Mock()
        with patch.object(identity_provisioning, "provision_external_identity", return_value=result) as provision:
            with CaptureQueriesContext(connection) as one_group:
                self.backend.sync_saml_user_profile_and_memberships(
                    self.user,
                    self.session_info(groups=[b"saml-member-group"]),
                )
            with CaptureQueriesContext(connection) as many_groups:
                self.backend.sync_saml_user_profile_and_memberships(
                    self.user,
                    self.session_info(groups=[b"saml-member-group"] * 25),
                )

        self.assertEqual(len(one_group), 0)
        self.assertEqual(len(many_groups), 0)
        self.assertEqual(provision.call_count, 2)

    @override_settings(ITAMBOX_TENANT_SAML_CONFIGS={"saml-customer": SAML_CONFIG})
    def test_service_backed_holder_membership_grant_scope_parity_and_repeat_idempotence(self):
        self.activate_tenant()
        Role.objects.create(tenant=self.tenant, name="Admin", permissions=["assets.view_asset"])
        from organization.services.identity_provisioning import organization_identity_provisioner

        with identity_provisioning.override_identity_provisioner(organization_identity_provisioner):
            first = self.backend.sync_saml_user_profile_and_memberships(self.user, self.session_info())
            with CaptureQueriesContext(connection) as repeat_queries:
                second = self.backend.sync_saml_user_profile_and_memberships(self.user, self.session_info())

        self.assertEqual(first.mode, "customer")
        self.assertEqual(second.mode, "customer")
        self.assertEqual(AssetHolder.objects.filter(user=self.user, tenant=self.tenant).count(), 1)
        membership = Membership.objects.get(user=self.user, tenant=self.tenant)
        grants = list(RoleGrant.objects.filter(membership=membership))
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0].role.name, "Admin")
        self.assertEqual(
            list(RoleGrantScope.objects.filter(role_grant=grants[0]).values_list("scope_type", flat=True)),
            [RoleGrantScope.SCOPE_OWN],
        )
        verbs = [query["sql"].lstrip().split(maxsplit=1)[0].upper() for query in repeat_queries]
        self.assertFalse(any(verb in {"INSERT", "DELETE"} for verb in verbs))

    @override_settings(ITAMBOX_TENANT_SAML_CONFIGS={"saml-customer": SAML_CONFIG})
    def test_saml_customer_mode_keeps_manual_provider_membership_and_allows_dual_home(self):
        provider = Tenant.objects.create(name="SAML manual provider", slug="saml-manual-provider", is_provider=True)
        self.tenant.managed_by = provider
        self.tenant.save(update_fields=["managed_by"])
        provider_role = Role.objects.create(tenant=provider, name="ProviderStaff", permissions=["assets.view_asset"])
        provider_membership = Membership.objects.create(user=self.user, tenant=provider, is_active=True)
        provider_grant = RoleGrant.objects.create(
            membership=provider_membership,
            role=provider_role,
            granted_by=self.user,
            reason="operator provider membership",
        )
        RoleGrantScope.objects.create(role_grant=provider_grant, scope_type=RoleGrantScope.SCOPE_OWN)
        before_grant = tuple(
            RoleGrant._base_manager.filter(pk=provider_grant.pk).values_list(
                "pk", "membership_id", "role_id", "granted_by_id", "reason", "valid_until"
            )
        )
        before_scopes = tuple(
            RoleGrantScope._base_manager.filter(role_grant=provider_grant).values_list(
                "pk", "role_grant_id", "scope_type", "tenant_id", "tenant_group_id"
            )
        )
        self.activate_tenant()

        with identity_provisioning.override_identity_provisioner(organization_identity_provisioner):
            result = self.backend.sync_saml_user_profile_and_memberships(self.user, self.session_info())

        self.assertEqual(result.mode, "customer")
        self.assertTrue(Membership.objects.filter(user=self.user, tenant=self.tenant, is_active=True).exists())
        self.assertTrue(Membership.objects.filter(user=self.user, tenant=provider, is_active=True).exists())
        self.assertEqual(
            tuple(
                RoleGrant._base_manager.filter(pk=provider_grant.pk).values_list(
                    "pk", "membership_id", "role_id", "granted_by_id", "reason", "valid_until"
                )
            ),
            before_grant,
        )
        self.assertEqual(
            tuple(
                RoleGrantScope._base_manager.filter(role_grant=provider_grant).values_list(
                    "pk", "role_grant_id", "scope_type", "tenant_id", "tenant_group_id"
                )
            ),
            before_scopes,
        )

    @override_settings(ITAMBOX_TENANT_SAML_CONFIGS={"saml-customer": SAML_CONFIG})
    def test_saml_config_keeps_secure_signature_and_unsolicited_defaults(self):
        self.activate_tenant()
        with patch("saml2.sigver.get_xmlsec_binary", return_value=sys.executable):
            config = saml_module.load_saml_config()
        self.assertFalse(config._sp_allow_unsolicited)
        self.assertFalse(config._sp_authn_requests_signed)
        self.assertFalse(config._sp_logout_requests_signed)
        self.assertTrue(config._sp_want_assertions_signed)
        self.assertTrue(config._sp_want_response_signed)

        override = {
            **SAML_CONFIG,
            "allow_unsolicited": True,
            "authn_requests_signed": True,
            "logout_requests_signed": True,
            "want_assertions_signed": False,
            "want_response_signed": False,
        }
        with override_settings(ITAMBOX_TENANT_SAML_CONFIGS={"saml-customer": override}):
            with patch("saml2.sigver.get_xmlsec_binary", return_value=sys.executable):
                config = saml_module.load_saml_config()
        self.assertTrue(config._sp_allow_unsolicited)
        self.assertTrue(config._sp_authn_requests_signed)
        self.assertTrue(config._sp_logout_requests_signed)
        self.assertFalse(config._sp_want_assertions_signed)
        self.assertFalse(config._sp_want_response_signed)

    @override_settings(ITAMBOX_TENANT_SAML_CONFIGS={"saml-customer": SAML_CONFIG})
    def test_can_login_false_returns_none_without_identity_port_call(self):
        self.activate_tenant()
        self.user.can_login = False
        self.user.save(update_fields=["can_login"])
        with (
            patch("djangosaml2.backends.Saml2Backend.authenticate", return_value=self.user),
            patch.object(identity_provisioning, "provision_external_identity") as provision,
        ):
            self.assertIsNone(self.backend.authenticate(None, session_info=self.session_info()))
        provision.assert_not_called()

    def test_upstream_user_lifecycle_and_authenticate_signature_remain_unchanged(self):
        session_info = self.session_info()
        attribute_mapping = {"mail": "email"}
        with (
            patch.object(saml_module.Saml2Backend, "authenticate", return_value=self.user) as upstream,
            patch.object(self.backend, "sync_saml_user_profile_and_memberships") as sync,
        ):
            returned = self.backend.authenticate(
                "request",
                session_info,
                attribute_mapping,
                False,
                marker="value",
            )

        self.assertIs(returned, self.user)
        upstream.assert_called_once_with("request", session_info, attribute_mapping, False, marker="value")
        sync.assert_called_once_with(self.user, session_info)


class SAMLAdapterLogBoundaryTests(TestCase):
    def test_saml_adapter_does_not_log_raw_provider_error_text(self):
        logger = logging.getLogger("djangosaml2")
        records = []
        handler = logging.Handler()
        handler.emit = records.append
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            with patch.object(
                identity_provisioning,
                "provision_external_identity",
                side_effect=RuntimeError("provider-error-canary"),
            ):
                set_current_tenant(Tenant.objects.create(name="Log Tenant", slug="saml-log-tenant"))
                user = User.objects.create_user(username="saml-log-user")
                with override_settings(ITAMBOX_TENANT_SAML_CONFIGS={"saml-log-tenant": SAML_CONFIG}):
                    with self.assertRaisesRegex(RuntimeError, "provider-error-canary"):
                        saml_module.TenantSaml2Backend().sync_saml_user_profile_and_memberships(
                            user,
                            {"ava": {"groups": [b"log-group-canary"]}},
                        )
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
            set_current_tenant(None)

        rendered = " ".join(record.getMessage() for record in records)
        self.assertNotIn("provider-error-canary", rendered)
        self.assertNotIn("log-group-canary", rendered)
