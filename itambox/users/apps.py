from django.apps import AppConfig
from django.apps import apps as app_registry
from django.db.models import Q
from django.utils import timezone

from itambox.capabilities import (
    BETA,
    CONTRACT_VERSION,
    OPT_IN,
    SOURCE_OBJECT_ENABLED,
    ActivationState,
    Capability,
    registry,
)


def scim_credential_probe():
    """Observe the bearer credential SCIM provisioning actually runs on.

    There is no SCIM settings key in this project. Both mounts --
    ``/api/tenants/<slug>/scim/v2/`` and ``/api/providers/<slug>/scim/v2/`` --
    authenticate an API ``Token`` scoped to the tenant named in the URL, so the
    token *is* the activation source. ``value_present`` reports that an operator
    has minted that credential; ``active`` reports that one of them could drive a
    provisioning write right now, which is what ``SCIMBearerTokenAuthentication``
    requires of it: ``write_enabled``, unexpired, and held by an active account.

    Only row counts leave this function. The digest, its pepper, and the
    ``key_preview`` are never read, so no part of a credential can reach a
    diagnostics row. Counting runs through ``_base_manager`` because the question
    is about the deployment rather than whichever tenant happens to be in request
    scope -- the same reason the SCIM authenticators resolve their tenant that
    way -- and tokens belonging to a soft-deleted tenant are excluded, so a
    recycled workspace cannot keep the capability looking configured.
    """
    tokens = app_registry.get_model("users", "Token")._base_manager.filter(tenant__deleted_at__isnull=True)
    usable = tokens.filter(write_enabled=True, user__is_active=True).filter(
        Q(expires__isnull=True) | Q(expires__gt=timezone.now())
    )
    return ActivationState(active=usable.exists(), value_present=tokens.exists())


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "users"

    def ready(self):
        import users.search  # noqa
        import users.signals  # noqa

        self._register_capabilities()

    def _register_capabilities(self):
        registry.register_all(self._capabilities())

    def _capabilities(self):
        return (
            Capability(
                key="users.scim_provisioning",
                title="SCIM Provisioning",
                owning_area="area:auth-rbac",
                maturity=BETA,
                security_critical=False,
                activation=OPT_IN,
                # Observation only: the probe counts the tenant-bound tokens the
                # SCIM endpoints already authenticate. Nothing here gates them,
                # and a deployment that has never minted one is simply reported
                # as not having switched SCIM on.
                activation_probe=scim_credential_probe,
                activation_source=SOURCE_OBJECT_ENABLED,
                owns=("users.api.scim",),
                docs_url="development/capability-registry.md",
                limitations=(
                    "Spec compliance gaps remain: PATCH semantics and filtering are partial.",
                    "Tenant endpoints provision Users and expose Groups read-only; "
                    "only provider-scoped endpoints provision Groups.",
                ),
                contract_version=CONTRACT_VERSION,
            ),
        )
