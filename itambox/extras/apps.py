from django.apps import AppConfig

from core.features import object_enabled_probe, report_designer_probe
from itambox.capabilities import (
    ALWAYS_ON,
    BETA,
    CONTRACT_VERSION,
    ENABLED,
    OPT_IN,
    SOURCE_ALWAYS,
    SOURCE_OBJECT_ENABLED,
    SOURCE_OPERATOR_FLAG,
    STABLE,
    ActivationState,
    Capability,
    registry,
)

DOCS = "development/capability-registry.md"


def _scheduled_reports_probe():
    """Report schedules only when the designer gate and a live row agree."""
    designer = report_designer_probe()
    scheduled = object_enabled_probe("extras", "ScheduledReport", "is_active")()
    return ActivationState(
        active=designer.active and scheduled.active,
        value_present=designer.value_present or scheduled.value_present,
    )


class ExtrasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "extras"

    def ready(self):
        # Import search indexes to register them
        import extras.search

        self._register_capabilities()

    def _register_capabilities(self):
        """Declare the reporting, alerting, and automation slices.

        ``extras`` is the clearest case for a capability registry: one Django
        app holds a Stable alert inbox, a Stable curated report catalogue, and
        four separately-graded Beta slices. A single app-level grade could only
        ever be wrong in one direction or the other.

        ``register_all`` rather than a loop: ``ready()`` runs again whenever a
        test swaps ``INSTALLED_APPS``, and with six entries a failure partway
        through has to be finishable on the next run.
        """
        registry.register_all(self._capabilities())

    def _capabilities(self):
        return (
            Capability(
                key="reporting.curated",
                title="Curated Reports",
                owning_area="area:operations",
                maturity=STABLE,
                security_critical=False,
                activation=ALWAYS_ON,
                activation_probe=None,
                activation_source=SOURCE_ALWAYS,
                owns=("core.reports",),
                docs_url=DOCS,
                limitations=(),
                contract_version=CONTRACT_VERSION,
            ),
            Capability(
                key="reporting.designer",
                title="Report Designer",
                owning_area="area:operations",
                maturity=BETA,
                security_critical=False,
                activation=OPT_IN,
                activation_probe=report_designer_probe,
                activation_source=SOURCE_OPERATOR_FLAG,
                owns=("extras.ReportTemplate",),
                docs_url=DOCS,
                limitations=(
                    "The designer's column, filter, and grouping model is expected to change; "
                    "saved templates may need to be rebuilt.",
                ),
                contract_version=CONTRACT_VERSION,
            ),
            Capability(
                key="reporting.scheduled",
                title="Scheduled Reports",
                owning_area="area:operations",
                maturity=BETA,
                security_critical=False,
                activation=OPT_IN,
                activation_probe=_scheduled_reports_probe,
                activation_source=SOURCE_OPERATOR_FLAG,
                owns=("extras.ReportGenerationArchive", "extras.ScheduledReport"),
                docs_url=DOCS,
                limitations=(
                    "The scheduled capability requires the operator flag ITAMBOX_FEATURE_REPORT_DESIGNER and an active "
                    "schedule row; disabling the flag pauses delivery for non-grandfathered templates without deleting "
                    "saved schedules, while the migration-managed bounded grandfathered set keeps rendering.",
                    "Delivery depends on a running qcluster worker; a stopped worker silently skips runs.",
                    "Archive retention is not yet configurable per schedule.",
                ),
                contract_version=CONTRACT_VERSION,
            ),
            Capability(
                key="alerting.inbox",
                title="Alerts and Notifications",
                owning_area="area:operations",
                maturity=STABLE,
                security_critical=False,
                activation=ALWAYS_ON,
                activation_probe=None,
                activation_source=SOURCE_ALWAYS,
                owns=("core.Notification", "extras.AlertLog"),
                docs_url=DOCS,
                limitations=(),
                contract_version=CONTRACT_VERSION,
            ),
            Capability(
                key="alerting.rules",
                title="Alert Rules and Channels",
                owning_area="area:operations",
                maturity=BETA,
                security_critical=False,
                activation=ENABLED,
                activation_probe=object_enabled_probe("extras", "AlertRule", "is_active"),
                activation_source=SOURCE_OBJECT_ENABLED,
                owns=("extras.AlertRule", "extras.NotificationChannel"),
                docs_url=DOCS,
                limitations=(
                    "Rule evaluation is daily, not continuous; thresholds are not evaluated on write.",
                    "Channel delivery failures are logged, not retried.",
                ),
                contract_version=CONTRACT_VERSION,
            ),
            Capability(
                key="automation.webhooks",
                title="Webhooks and Event Rules",
                owning_area="area:operations",
                maturity=BETA,
                security_critical=False,
                activation=OPT_IN,
                activation_probe=object_enabled_probe("extras", "EventRule", "enabled"),
                activation_source=SOURCE_OBJECT_ENABLED,
                owns=("extras.EventRule", "extras.WebhookEndpoint"),
                docs_url=DOCS,
                limitations=(
                    "The outbound payload schema is not frozen and may change between minor releases.",
                    "Deliveries are fire-and-forget; there is no delivery log or replay.",
                ),
                contract_version=CONTRACT_VERSION,
            ),
        )
