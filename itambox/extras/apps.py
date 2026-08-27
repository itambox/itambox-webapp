from django.apps import AppConfig
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.recorder import MigrationRecorder
from django.db.models.signals import post_migrate

from core.features import object_enabled_probe, report_designer_probe
from core.schedules import register_schedule
from itambox.capabilities import (
    ALWAYS_ON,
    BETA,
    CAPABILITY_REGISTRY_DOC_URL,
    CONTRACT_VERSION,
    ENABLED,
    OPT_IN,
    SOURCE_ALWAYS,
    SOURCE_OBJECT_ENABLED,
    SOURCE_OPERATOR_FLAG,
    STABLE,
    ActivationState,
    Capability,
)
from itambox.capabilities import (
    registry as capability_registry,
)
from itambox.registry import registry as generic_presentation_registry

DOCS = CAPABILITY_REGISTRY_DOC_URL

# The daily alert schedule may only be registered once the cutover migration that
# rewrites persisted task paths has actually been applied on the database being
# migrated; before that, the canonical path does not exist for this database.
ISSUE445_CUTOVER = ("extras", "0110_issue445_task_paths")
ALERT_DISPATCH_UID = "extras.issue445.register_daily_alert_schedule.v1"
ALERT_TASK_PATH = "extras.tasks.alerts.evaluate_alert_rules_task"


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
        # inline imports: app-registry: extras.signals, extras.search and extras.feature_views
        # register after app population
        import extras.search
        import extras.signals  # noqa: F401 -- side-effect import connects the event/watcher receivers
        from extras.feature_views import EXTRAS_GENERIC_PRESENTATION_PROVIDER

        self._register_capabilities()
        self._register_generic_presentation(EXTRAS_GENERIC_PRESENTATION_PROVIDER)
        # ready() must stay query-free: the applied-migration probe below belongs
        # to the post_migrate handler, never to app loading.
        post_migrate.connect(
            self._register_alert_schedule,
            sender=self,
            dispatch_uid=ALERT_DISPATCH_UID,
            weak=False,
        )

    def _register_alert_schedule(self, sender, using=DEFAULT_DB_ALIAS, **kwargs):
        """Ensure the daily alert evaluation schedule exists on the migrated database.

        Registration is deliberately gated on the cutover migration being applied:
        a database still on a predecessor state (or one just reversed back to it)
        must not gain a schedule row pointing at the cutover task path.
        """
        # inline import: app-registry: avoid AppRegistryNotReady at app-load time
        from django_q.models import Schedule

        connection = connections[using]
        recorder = MigrationRecorder(connection)
        if not recorder.has_table():
            return
        app, name = ISSUE445_CUTOVER
        if not recorder.migration_qs.filter(app=app, name=name).exists():
            return
        register_schedule(
            ALERT_TASK_PATH,
            defaults={
                "name": "Daily Alert Rule Evaluation",
                "schedule_type": Schedule.DAILY,
                "repeats": -1,
            },
            using=using,
        )

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
        capability_registry.register_all(self._capabilities())

    def _register_generic_presentation(self, provider):
        job_model = self.apps.get_model("core", "Job")
        generic_presentation_registry.register_feature(job_model, "job_file_attachments")
        generic_presentation_registry.register_generic_presentation(
            "extras",
            provider,
            detail_features=(
                "bookmarkable",
                "custom_field_data",
                "file_attachments",
                "image_attachments",
                "job_file_attachments",
                "journaling",
                "watchable",
            ),
            list_params=True,
            list_filter=True,
            list_context=True,
            priority=100,
        )

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
                    "Event-specific data contents are not frozen. Slack and Teams use reduced vendor-specific "
                    "envelopes without X-Hub-Signature-256 but retain schema_version, event_id, delivery_id, "
                    "attempt, and tenant.",
                    "Delivery is at-least-once: one durable row keeps the current attempt count and latest "
                    "outcome, consumers must deduplicate, and manual redelivery requires a retained source event "
                    "with no pending or future-retry work live.",
                ),
                contract_version=CONTRACT_VERSION,
            ),
        )
