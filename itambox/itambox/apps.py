from django.apps import AppConfig
from django.conf import settings

from core.features import settings_probe
from itambox.capabilities import (
    CONTRACT_VERSION,
    EXPERIMENTAL,
    OPT_IN,
    SOURCE_OPERATOR_FLAG,
    ActivationState,
    Capability,
    registry,
)


def _plugin_activation_probe():
    configured = settings_probe("PLUGINS")()
    active_plugins = settings_probe("PLUGINS_ACTIVE")()
    failed_plugins = {row["plugin"] for row in getattr(settings, "PLUGINS_DIAGNOSTICS", ())}
    configured_plugins = set(getattr(settings, "PLUGINS", ()) or ())
    effective_active = active_plugins.active or (
        configured.active and configured.value_present and not configured_plugins <= failed_plugins
    )
    return ActivationState(active=effective_active, value_present=configured.value_present)


class ITAMBoxConfig(AppConfig):
    name = "itambox"
    verbose_name = "ITAMbox System Framework"

    def ready(self):
        self._register_capabilities()

    def _register_capabilities(self):
        registry.register_all(self._capabilities())

    def _capabilities(self):
        return (
            Capability(
                key="platform.plugins",
                title="Plugin System",
                owning_area="area:plugins",
                maturity=EXPERIMENTAL,
                security_critical=False,
                activation=OPT_IN,
                # PLUGINS is the operator activation source. The effective
                # state comes from PLUGINS_ACTIVE so an isolated failure does
                # not make the capability appear active.
                activation_probe=_plugin_activation_probe,
                activation_source=SOURCE_OPERATOR_FLAG,
                owns=("itambox.plugins",),
                docs_url="plugins/api_reference.md",
                limitations=(
                    "Only the bounded extension points documented for plugin API 1.0 are supported; "
                    "Experimental interfaces may change in any release.",
                    "Plugin code runs in-process with full database access and is not sandboxed.",
                ),
                contract_version=CONTRACT_VERSION,
            ),
        )
