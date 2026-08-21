from django.apps import AppConfig
from django.db.models.signals import post_migrate

from itambox.capabilities import (
    ALWAYS_ON,
    CAPABILITY_REGISTRY_DOC_URL,
    CONTRACT_VERSION,
    RESOURCE_GRANT_SECURITY_DOC_URL,
    SOURCE_ALWAYS,
    STABLE,
    Capability,
    registry,
)


class OrganizationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "organization"

    def ready(self):
        # Import search indexes to register them
        import organization.search
        import organization.signals

        # inline imports: app-registry: domain modules load only after the app registry is ready.
        from core.tenant_scope import register_tenant_scope_provider
        from organization.access import (
            accessible_tenant_ids,
            accessible_tenant_ids_with_expiry,
            managed_accessible_tenant_ids,
        )
        from organization.rbac import (
            applicable_grants,
            build_accessible_tenant_permissions_map,
            effective_permissions_with_expiry,
        )

        register_tenant_scope_provider(
            accessible_tenant_ids=accessible_tenant_ids,
            accessible_tenant_ids_with_expiry=accessible_tenant_ids_with_expiry,
            managed_accessible_tenant_ids=managed_accessible_tenant_ids,
            applicable_grants=applicable_grants,
            build_accessible_tenant_permissions_map=build_accessible_tenant_permissions_map,
            resolve_effective_permissions_with_expiry=effective_permissions_with_expiry,
        )

        post_migrate.connect(self._register_expiry_schedule, sender=self)
        self._register_capabilities()

    def _register_expiry_schedule(self, sender, **kwargs):
        # inline imports: app-registry: schedule models and helpers load after migrations/apps are ready.
        from django_q.models import Schedule

        # inline import: app-registry: schedule helpers load after migrations/apps are ready.
        from core.schedules import register_schedule

        register_schedule(
            "core.tasks.resource_grants.coordinate_resource_grant_expiry",
            defaults={
                "name": "Hourly Resource Grant Expiry Sweep",
                "schedule_type": Schedule.HOURLY,
                "repeats": -1,
            },
        )

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
                docs_url=CAPABILITY_REGISTRY_DOC_URL,
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
                owns=(
                    "organization.TenantResourceGrant",
                    "organization.TenantResourceGrantExpiryRun",
                    "organization.TenantResourceGrantExpiryRevocation",
                ),
                docs_url=RESOURCE_GRANT_SECURITY_DOC_URL,
                limitations=(),
                contract_version=CONTRACT_VERSION,
            ),
        )
