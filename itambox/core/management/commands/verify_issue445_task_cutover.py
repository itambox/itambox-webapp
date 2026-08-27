"""Strict executable-surface verifier for the issue #445 task-path cutover.

Phases::

    forward-preflight     every executable surface must be a canonical
                          predecessor path (cutover not yet applied)
    forward-postmigrate   every executable surface must be the cutover path
    rollback-preflight    every executable surface must be the cutover path
                          (reverse migration is about to run)
    reverse-postmigrate   every executable surface must be the predecessor
                          path again

Inventories Schedule ``func``/``hook``, every supported ORM-broker row
regardless of lock, executable ``q_options`` and every nested chain entry.
Packages are decoded only with django-q2's ``SignedPackage.loads``
and resolved with ``django_q.utils.get_func_repr``. Any bad signature,
decompression/pickle/shape failure, invalid ``q_options``, unknown executable
form, noncanonical alias, phase mismatch, undeclared cluster, or unsupported
broker is a strict failure.

Output is a sorted ``surface | path | count`` table plus undecodable counts.
It never prints IDs, packages, payloads, args, kwargs, results, URLs, headers,
tokens, secrets, DSNs, or raw exception text.
"""

import ast
import inspect
from collections import Counter, defaultdict

from django.apps import apps as django_apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connections
from django_q.signing import SignedPackage
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
            "mismatches": Counter(),
            "problems": Counter(),
        }

        clusters = self._cluster_matrix()
        self._inventory_schedules(clusters, expected, state)
        self._inventory_ormq(clusters, expected, state)

        failures = self._report(state)
        if failures and options["strict"]:
            raise CommandError(
                f"strict failure: {sum(state['mismatches'].values())} phase mismatch(es), "
                f"{sum(state['problems'].values())} invalid form(s), "
                f"{sum(state['undecodable'].values())} undecodable"
            )
        if failures:
            raise CommandError("surface verification failed")

    # ------------------------------------------------------------------
    # Inventory helpers
    # ------------------------------------------------------------------

    def _inventory_schedules(self, clusters, expected, state):
        database_aliases = set(connections)
        database_aliases.update(cluster[0] for cluster in clusters)
        for db_alias in sorted(database_aliases):
            if db_alias not in connections:
                raise CommandError("undeclared database alias in queue configuration")
            try:
                self._scan_schedules(db_alias, expected, state)
            except DatabaseError:
                self._problem("database.schedule", "inventory-failed", state)

    def _inventory_ormq(self, clusters, expected, state):
        clusters_by_database = defaultdict(set)
        for alias, key in clusters:
            clusters_by_database[alias].add(key)
        for db_alias, declared_keys in sorted(clusters_by_database.items()):
            OrmQ = django_apps.get_model("django_q", "OrmQ")
            try:
                packages = list(OrmQ.objects.using(db_alias).values_list("key", "payload"))
            except DatabaseError:
                self._problem("database.ormq", "inventory-failed", state)
                continue
            for key, package in packages:
                if key not in declared_keys:
                    self._problem("ormq.cluster", "undeclared-cluster", state)
                    continue
                self._inspect_package(package, "ormq.package", expected, state)

    def _scan_schedules(self, database, expected, state):
        Schedule = django_apps.get_model("django_q", "Schedule")
        for func, hook, args, kwargs in Schedule.objects.using(database).values_list("func", "hook", "args", "kwargs"):
            self._classify_surface(func, "schedule.func", expected, state)
            if hook:
                self._inspect_hook(hook, "schedule.hook", expected, state)
            if args:
                try:
                    ast.literal_eval(args)
                except (SyntaxError, TypeError, ValueError):
                    self._problem("schedule.args", "invalid-literal", state)
            parsed_kwargs = self._parse_schedule_kwargs(kwargs)
            if parsed_kwargs is None:
                self._problem("schedule.kwargs", "invalid-literal", state)
                continue
            self._inspect_kwargs(parsed_kwargs, "schedule.kwargs", expected, state)

    def _cluster_matrix(self):
        q_cluster = dict(getattr(settings, "Q_CLUSTER", {}) or {})
        nested_alternates = q_cluster.pop("ALT_CLUSTERS", {})
        legacy_alternates = getattr(settings, "ALT_Q_CLUSTERS", {}) or {}
        if not isinstance(nested_alternates, dict) or not isinstance(legacy_alternates, dict):
            raise CommandError("invalid alternate queue-cluster configuration")
        configs = [(None, q_cluster)]
        configs.extend(nested_alternates.items())
        configs.extend(legacy_alternates.items())
        clusters = []
        for alternate_name, overrides in configs:
            if not isinstance(overrides, dict):
                raise CommandError("invalid queue-cluster configuration")
            config = dict(q_cluster)
            config.update(overrides)
            if self._broker_kind(config) != "orm":
                raise CommandError("unsupported broker type in queue configuration")
            database = config.get("orm")
            if not isinstance(database, str) or not database:
                raise CommandError("invalid ORM broker database alias")
            if alternate_name is None:
                queue_key = config.get("cluster_name") or config.get("name") or "default"
            else:
                queue_key = alternate_name
            if not isinstance(queue_key, str) or not queue_key:
                raise CommandError("invalid queue-cluster name")
            clusters.append((database, queue_key))
        return clusters

    def _broker_kind(self, config):
        if config.get("broker_class"):
            return "custom"
        if config.get("iron_mq"):
            return "iron-mq"
        if isinstance(config.get("sqs"), dict):
            return "sqs"
        if config.get("orm"):
            return "orm"
        if config.get("mongo"):
            return "mongo"
        return "redis"

    def _parse_schedule_kwargs(self, value):
        if not value:
            return {}
        if not isinstance(value, str):
            return None
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            parsed = self._parse_schedule_keyword_syntax(value)
        if not isinstance(parsed, dict) or any(not isinstance(key, str) for key in parsed):
            return None
        return parsed

    def _parse_schedule_keyword_syntax(self, value):
        try:
            call = ast.parse(f"f({value})", mode="eval").body
            if not isinstance(call, ast.Call) or call.args:
                return None
            parsed = {}
            for keyword in call.keywords:
                if keyword.arg is None or keyword.arg in parsed:
                    return None
                parsed[keyword.arg] = ast.literal_eval(keyword.value)
            return parsed
        except (SyntaxError, TypeError, ValueError):
            return None

    def _inspect_package(self, package, surface, expected, state):
        try:
            decoded = SignedPackage.loads(package)
        # broad except: task-isolation: undecodable packages are counted as a surface, never raised
        except Exception:
            state["undecodable"][surface] += 1
            return
        if not self._valid_package_shape(decoded):
            self._problem(surface, "invalid-package-shape", state)
            return
        self._classify_surface(decoded["func"], surface, expected, state)
        if "hook" in decoded:
            self._inspect_hook(decoded["hook"], f"{surface}.hook", expected, state)
        if "chain" in decoded:
            self._inspect_chain(decoded["chain"], f"{surface}.chain", expected, state, set())
        self._inspect_kwargs(decoded["kwargs"], f"{surface}.kwargs", expected, state)

    def _valid_package_shape(self, decoded):
        required = ("id", "name", "func", "args", "kwargs", "started")
        return (
            isinstance(decoded, dict)
            and all(key in decoded for key in required)
            and isinstance(decoded["id"], str)
            and bool(decoded["id"])
            and isinstance(decoded["name"], str)
            and self._is_executable(decoded["func"])
            and isinstance(decoded["args"], (list, tuple))
            and self._valid_kwargs(decoded["kwargs"])
        )

    def _inspect_kwargs(self, kwargs, surface, expected, state, ancestors=None):
        if not self._valid_kwargs(kwargs):
            self._problem(surface, "invalid-kwargs", state)
            return
        ancestors = set() if ancestors is None else ancestors
        if id(kwargs) in ancestors:
            self._problem(surface, "recursive-options", state)
            return
        ancestors = ancestors | {id(kwargs)}
        if "hook" in kwargs:
            self._inspect_hook(kwargs["hook"], f"{surface}.hook", expected, state)
        if "chain" in kwargs:
            self._inspect_chain(kwargs["chain"], f"{surface}.chain", expected, state, ancestors)
        if "q_options" not in kwargs:
            return
        q_options = kwargs["q_options"]
        if not self._valid_kwargs(q_options):
            self._problem(f"{surface}.q_options", "invalid-q-options", state)
            return
        self._inspect_kwargs(q_options, f"{surface}.q_options", expected, state, ancestors)

    def _inspect_hook(self, hook, surface, expected, state):
        if not hook:
            return
        if not self._is_executable(hook):
            self._problem(surface, "invalid-hook", state)
            return
        self._classify_surface(hook, surface, expected, state)

    def _inspect_chain(self, chain, surface, expected, state, ancestors):
        if not isinstance(chain, list):
            self._problem(surface, "invalid-chain", state)
            return
        if id(chain) in ancestors:
            self._problem(surface, "recursive-chain", state)
            return
        ancestors = ancestors | {id(chain)}
        for entry in chain:
            if self._is_executable(entry):
                self._classify_surface(entry, surface, expected, state)
                continue
            if not isinstance(entry, tuple) or len(entry) != 3:
                self._problem(surface, "invalid-chain-entry", state)
                continue
            func, args, kwargs = entry
            if not self._is_executable(func) or not isinstance(args, (list, tuple)) or not self._valid_kwargs(kwargs):
                self._problem(surface, "invalid-chain-entry", state)
                continue
            self._classify_surface(func, surface, expected, state)
            self._inspect_kwargs(kwargs, f"{surface}.kwargs", expected, state, ancestors)

    def _valid_kwargs(self, value):
        return isinstance(value, dict) and all(isinstance(key, str) for key in value)

    def _is_executable(self, value):
        return (isinstance(value, str) and bool(value)) or inspect.isfunction(value) or inspect.ismethod(value)

    def _problem(self, surface, code, state, path=None):
        state["problems"][(surface, code, path)] += 1

    def _classify_surface(self, path, surface, expected, state):
        resolved = self._resolve(path)
        classification = self._classify(resolved)
        if classification in ("legacy", "cutover"):
            state["surfaces"][surface][resolved] += 1
            if classification != expected:
                state["mismatches"][(surface, resolved)] += 1
            return
        if classification == "alias":
            self._problem(surface, "noncanonical-alias", state, resolved)
            return
        if classification == "unknown-form":
            state["undecodable"][surface] += 1
            return
        # other: unchanged repository tasks pass in every phase
        state["surfaces"][surface][resolved] += 1

    def _report(self, state):
        failures = (
            sum(state["mismatches"].values()) + sum(state["problems"].values()) + sum(state["undecodable"].values())
        )
        for surface in sorted(state["surfaces"]):
            for path, count in sorted(state["surfaces"][surface].items()):
                self.stdout.write(f"{surface} | {path} | {count}")
        for (surface, path), count in sorted(state["mismatches"].items()):
            self.stdout.write(f"{surface} | <phase-mismatch:{path}> | {count}")
        for (surface, code, path), count in sorted(state["problems"].items()):
            identity = f"{code}:{path}" if path else code
            self.stdout.write(f"{surface} | <{identity}> | {count}")
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
