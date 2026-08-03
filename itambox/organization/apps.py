from django.apps import AppConfig

from itambox.capabilities import ALWAYS_ON, CONTRACT_VERSION, SOURCE_ALWAYS, STABLE, Capability, registry


class OrganizationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "organization"

    def ready(self):
        # Import search indexes to register them
        import organization.search
        import organization.signals

        self._register_capabilities()

    def _register_capabilities(self):
        registry.register_all(self._capabilities())

    def _capabilities(self):
        return (
            Capability(
                key="organization.role_grants",
                title="Role Grants",
                owning_area="area:auth-rbac",
                maturity=STABLE,
                security_critical=True,
                # Security-critical by declaration, and therefore always-on by
                # construction: the registry refuses a probe here, so no
                # deployment state can ever report the authorization path off.
                activation=ALWAYS_ON,
                activation_probe=None,
                activation_source=SOURCE_ALWAYS,
                owns=("organization.RoleGrant",),
                docs_url="development/capability-registry.md",
                limitations=(),
                contract_version=CONTRACT_VERSION,
            ),
            Capability(
                key="organization.resource_grants",
                title="Tenant Resource Grants",
                owning_area="area:auth-rbac",
                maturity=STABLE,
                security_critical=True,
                activation=ALWAYS_ON,
                activation_probe=None,
                activation_source=SOURCE_ALWAYS,
                owns=("organization.TenantResourceGrant",),
                docs_url="development/tenant-resource-grant-security.md",
                limitations=(),
                contract_version=CONTRACT_VERSION,
            ),
        )
