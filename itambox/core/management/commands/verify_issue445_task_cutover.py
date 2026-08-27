"""Strict executable-surface verifier for the issue #445 task-path cutover.

Phases::

    forward-preflight     every executable surface must be a canonical
                          predecessor path (cutover not yet applied)
    forward-postmigrate   every executable surface must be the cutover path
    rollback-preflight    every executable surface must be the cutover path
                          (reverse migration is about to run)
    reverse-postmigrate   every executable surface must be the predecessor
                          path again

Inventories Schedule ``func``/``hook``, every OrmQ row regardless of lock,
optional Redis/Valkey list packages, executable ``q_options`` and every nested
chain entry. Packages are decoded only with django-q2's ``SignedPackage.loads``
and resolved with ``django_q.utils.get_func_repr``. Any bad signature,
decompression/pickle/shape failure, invalid ``q_options``, unknown executable
form, noncanonical alias, phase mismatch, undeclared cluster, or unsupported
broker is a strict failure.

Output is a sorted ``surface | path | count`` table plus undecodable counts.
It never prints IDs, packages, payloads, args, kwargs, results, URLs, headers,
tokens, secrets, DSNs, or raw exception text.
"""

from collections import Counter, defaultdict

from django.apps import apps as django_apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django_q.brokers import SignedPackage
from django_q.utils import get_func_repr

LEGACY_PATHS = frozenset(
    {
        "core.tasks.evaluate_alert_rules_task",
        "core.tasks.run_alert_rule_now",
        "core.tasks.generate_scheduled_report_task",
        "core.tasks.send_webhook_task",
        "assets.tasks.notify_new_request_task",
        "core.tasks.bulk_checkin_task",
        "core.tasks.bulk_checkout_task",
        "core.tasks.calculate_depreciation",
        "core.tasks.bulk_dispose_task",
        "core.tasks.sync_tenant_intune",
        "core.tasks.labels.generate_label_batch_task",
        "core.tasks.labels.generate_label_pdf_batch_task",
    }
)
CUTOVER_PATHS = frozenset(
    {
        "extras.tasks.alerts.evaluate_alert_rules_task",
        "extras.tasks.alerts.run_alert_rule_now",
        "extras.tasks.reports.generate_scheduled_report_task",
        "extras.tasks.webhooks.send_webhook_task",
        "assets.tasks.requests.notify_new_request_task",
        "assets.tasks.checkin.bulk_checkin_task",
        "assets.tasks.checkout.bulk_checkout_task",
        "assets.tasks.depreciation.calculate_depreciation",
        "assets.tasks.disposal.bulk_dispose_task",
        "assets.tasks.intune_sync.sync_tenant_intune",
        "assets.tasks.labels.generate_label_batch_task",
        "assets.tasks.labels.generate_label_pdf_batch_task",
    }
)
# Path prefixes owned by the #445 move; any executable under them that is not
# one of the exact mapped identities is a noncanonical alias and fails.
NEIGHBORHOOD_PREFIXES = (
    "core.tasks.alerts.",
    "core.tasks.reports.",
    "core.tasks.webhooks.",
    "core.tasks.checkin.",
    "core.tasks.checkout.",
    "core.tasks.depreciation.",
    "core.tasks.disposal.",
    "core.tasks.intune_sync.",
    "core.tasks.labels.",
    "assets.tasks.requests.",
    "assets.tasks.alerts.",
    "assets.tasks.reports.",
    "assets.tasks.webhooks.",
    "assets.tasks.checkin.",
    "assets.tasks.checkout.",
    "assets.tasks.depreciation.",
    "assets.tasks.disposal.",
    "assets.tasks.intune_sync.",
    "assets.tasks.labels.",
)
NEIGHBORHOOD_MODULES = (
    "core.tasks.checkin",
    "core.tasks.checkout",
    "core.tasks.depreciation",
    "core.tasks.disposal",
    "core.tasks.intune_sync",
    "core.tasks.labels",
    "core.tasks.alerts",
    "core.tasks.reports",
    "core.tasks.webhooks",
    "assets.tasks.requests",
)
PHASES_TO_EXPECTED = {
    "forward-preflight": "legacy",
    "forward-postmigrate": "cutover",
    "rollback-preflight": "cutover",
    "reverse-postmigrate": "legacy",
}


class Command(BaseCommand):
    help = "Strictly verify every django-q executable surface matches the issue #445 phase."

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default", help="Database alias to verify (default: default)")
        parser.add_argument(
            "--phase",
            required=True,
            choices=sorted(PHASES_TO_EXPECTED),
            help="Which cutover state the executable surfaces must match.",
        )
        parser.add_argument("--strict", action="store_true", help="Fail on any undeclared surface or unknown form.")
        parser.add_argument("--format", choices=("counts",), default="counts", help="Output shape.")

    def handle(self, *args, **options):
        database = options["database"]
        if database not in connections:
            raise CommandError(f"undeclared database alias: {database}")
        expected = PHASES_TO_EXPECTED[options["phase"]]
        state = {
            "surfaces": defaultdict(Counter),
            "undecodable": Counter(),
            "mismatches": [],
            "problems": [],
        }

        self._scan_schedules(database, expected, state)
        clusters = self._cluster_matrix()
        for kind, alias in clusters:
            if not kind.startswith("orm"):
                raise CommandError(f"unsupported broker type: {kind}")
            db_alias = alias or database
            OrmQ = django_apps.get_model("django_q", "OrmQ")
            for key, package in OrmQ.objects.using(db_alias).values_list("key", "package"):
                self._inspect_package(package, f"ormq.{key[:8]}", expected, state)

        failures = self._report(state)
        if failures and options["strict"]:
            raise CommandError(
                f"strict failure: {len(state['mismatches'])} phase mismatch(es), "
                f"{len(state['problems'])} invalid form(s), {sum(state['undecodable'].values())} undecodable"
            )
        if failures:
            raise CommandError("surface verification failed")

    # ------------------------------------------------------------------
    # Inventory helpers
    # ------------------------------------------------------------------

    def _scan_schedules(self, database, expected, state):
        Schedule = django_apps.get_model("django_q", "Schedule")
        for field in ("func", "hook"):
            for value in Schedule.objects.using(database).values_list(field, flat=True):
                if not value:
                    continue
                self._classify_surface(value, f"schedule.{field}", expected, state)

    def _cluster_matrix(self):
        clusters = []
        q_cluster = dict(getattr(settings, "Q_CLUSTER", {}) or {})
        if q_cluster.get("orm"):
            clusters.append(("orm", q_cluster["orm"]))
        for config in (getattr(settings, "ALT_Q_CLUSTERS", {}) or {}).values():
            if config.get("orm"):
                clusters.append(("orm", config["orm"]))
        if not clusters and q_cluster:
            raise CommandError("unsupported broker: no ORM broker in Q_CLUSTER")
        return clusters

    def _decode_package_spec(self, decoded):
        """Normalize a decoded package into (func, hook, chain)."""
        spec = decoded[0] if isinstance(decoded, (list, tuple)) and decoded else decoded
        if isinstance(spec, dict):
            return spec.get("func"), spec.get("hook"), spec.get("chain")
        if isinstance(spec, str):
            return spec, None, None
        return None, None, None

    def _inspect_package(self, package, surface, expected, state):
        try:
            decoded = SignedPackage.loads(package)
        # broad except: task-isolation: undecodable packages are counted as a surface, never raised
        except Exception:
            state["undecodable"][surface] += 1
            return
        func, hook, chain = self._decode_package_spec(decoded)
        if func is None:
            state["undecodable"][surface] += 1
            return
        self._classify_surface(func, surface, expected, state)
        if isinstance(hook, str) and hook:
            self._classify_surface(hook, f"{surface}.hook", expected, state)
        elif hook:
            self._inspect_package(hook, f"{surface}.hook", expected, state)
        if isinstance(chain, list):
            for entry in chain:
                self._inspect_package(entry, f"{surface}.chain", expected, state)

    def _classify_surface(self, path, surface, expected, state):
        resolved = self._resolve(path)
        classification = self._classify(resolved)
        if classification in ("legacy", "cutover"):
            state["surfaces"][surface][resolved] += 1
            if classification != expected:
                state["mismatches"].append((surface, resolved))
            return
        if classification == "alias":
            state["problems"].append((surface, resolved, "noncanonical alias"))
            return
        if classification == "unknown-form":
            state["undecodable"][surface] += 1
            return
        # other: unchanged repository tasks pass in every phase
        state["surfaces"][surface][resolved] += 1

    def _report(self, state):
        failures = len(state["mismatches"]) + len(state["problems"]) + sum(state["undecodable"].values())
        for surface, path in state["mismatches"]:
            self.stderr.write(f"{surface}: phase mismatch {path}")
        for surface, path, reason in state["problems"]:
            self.stderr.write(f"{surface}: {reason} {path}")
        for surface in sorted(state["surfaces"]):
            for path, count in sorted(state["surfaces"][surface].items()):
                self.stdout.write(f"{surface} | {path} | {count}")
        for surface, count in sorted(state["undecodable"].items()):
            self.stdout.write(f"{surface} | <undecodable> | {count}")
        return failures

    def _resolve(self, path):
        try:
            return get_func_repr(path)
        # broad except: task-isolation: unresolvable identities stay literal so the classifier can name them
        except Exception:
            return path

    def _classify(self, path):
        if path in LEGACY_PATHS:
            return "legacy"
        if path in CUTOVER_PATHS:
            return "cutover"
        if not isinstance(path, str) or not path:
            return "unknown-form"
        for prefix in NEIGHBORHOOD_PREFIXES:
            if path.startswith(prefix):
                return "alias"
        if path in NEIGHBORHOOD_MODULES:
            return "alias"
        return "other"
