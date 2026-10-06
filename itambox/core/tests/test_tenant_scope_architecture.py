"""Issue #100 contracts for the domain-blind tenancy/auth substrate."""

from django.test import SimpleTestCase

from core.tests.test_import_boundaries import _edges, _imports


class Issue100TenancyBoundaryTests(SimpleTestCase):
    def test_core_tenant_scope_leaf_does_not_import_domain_modules(self):
        domain_prefixes = (
            "assets",
            "compliance",
            "extras",
            "inventory",
            "licenses",
            "organization",
            "procurement",
            "software",
            "subscriptions",
            "users",
        )
        self.assertFalse(
            any(
                name == prefix or name.startswith(f"{prefix}.")
                for name in _edges("core.tenant_scope", False)
                for prefix in domain_prefixes
            )
        )

    def test_core_auth_does_not_import_organization_access(self):
        self.assertFalse(
            _imports("core.auth.__init__", "organization.access"),
            "core auth must resolve through a registered provider, not organization access",
        )

    def test_core_managers_do_not_import_organization_access(self):
        self.assertFalse(
            _imports("core.managers", "organization.access"),
            "kernel managers must use the tenant-scope contract",
        )

    def test_core_mfa_does_not_import_organization_rbac(self):
        self.assertFalse(
            _imports("core.mfa", "organization.rbac"),
            "MFA policy must not depend on the concrete RBAC implementation",
        )
