from django.apps import AppConfig

from core.features import settings_probe
from itambox.capabilities import (
    CONTRACT_VERSION,
    EXPERIMENTAL,
    OPT_IN,
    SOURCE_OPERATOR_FLAG,
    Capability,
    registry,
)


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
                # ITAMBOX_PLUGINS is the real operator flag: an empty PLUGINS
                # list is the shipped default, so the platform stays inert
                # until somebody names a plugin.
                activation_probe=settings_probe("PLUGINS"),
                activation_source=SOURCE_OPERATOR_FLAG,
                owns=("itambox.plugins",),
                docs_url="development/capability-registry.md",
                limitations=(
                    "Lifecycle hooks are still being defined; a plugin that loads today "
                    "may need changes to keep loading.",
                    "Plugin code runs in-process with full database access and is not sandboxed.",
                ),
                contract_version=CONTRACT_VERSION,
            ),
        )
