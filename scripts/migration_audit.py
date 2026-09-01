#!/usr/bin/env python3
"""Build a deterministic migration inventory without importing Django."""

import argparse
import ast
import copy
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCHEMA_VERSION = 3
TRUSTED_PRE_CLEANUP_REVISION = "49444b4c81abf4d2fe225892ef75a210aa60f13e"
OPERATION_TYPES = (
    "RunPython",
    "RunSQL",
    "SeparateDatabaseAndState",
    "BtreeGistExtension",
)
POST_TRANSITION_MIGRATIONS = {
    "assets.0101_seed_canonical_missing_status",
    "compliance.0101_alter_custodyreceipt_signed_at",
    "compliance.0102_clear_unsigned_receipt_timestamps",
    "compliance.0103_alter_custodyreceipt_options",
    "compliance.0104_custodysigningsession",
    "compliance.0105_custodyhandoffdelivery",
    "extras.0101_issue88_drop_legacy_webhook_name_like",
    "extras.0102_alter_event_action",
    "extras.0103_remove_reporttemplate_advanced_mode_and_more",
    "extras.0104_issue183_alert_tenant_reconciliation",
    "extras.0105_reporttemplate_advanced_mode_and_more",
    "extras.0106_scheduledreportscopeauthorization",
    "extras.0107_scheduledreportscopeauthorization_revocation",
    "extras.0108_alertlog_delivery_outcome",
    "extras.0109_webhookdelivery",
    "extras.0110_issue445_task_paths",
    "extras.0111_webhookdelivery_target_claim",
    "extras.0112_backfill_webhookdelivery_targets",
    "extras.0113_upgrade_legacy_webhook_retry_schedules",
    "inventory.0101_alter_accessoryassignment_options_and_more",
    "organization.0101_membership_external_id_and_more",
    "organization.0102_alter_tenantresourcegrant_options",
    "organization.0103_tenant_resource_grant_expiry",
    "procurement.0101_alter_purchaseorder_options",
    "subscriptions.0101_remove_subscription_auto_renewal_and_more",
    "users.0101_user_scim_id_usergroup_external_id_usergroup_scim_id_and_more",
    "users.0102_token_updated_at",
    "users.0103_oidcidentity",
}
ISSUE88_SHARD_RE = re.compile(r"issue88_shard_(\d{2})(?:_|$)")
ALLOWED_DISPOSITIONS = {
    "required-fresh",
    "upgrade-only",
    "safely-replaced-by-final-schema",
    "review-blocker",
}
NORMALIZED_POST_TRANSITION_DISPOSITIONS = frozenset({"RETAIN", "FOLD_INTO_BASELINE", "RETIRE_UPGRADE_ONLY"})
POST_TRANSITION_DISPOSITION_GROUPS = {
    "RETAIN": frozenset(POST_TRANSITION_MIGRATIONS),
    "FOLD_INTO_BASELINE": frozenset(),
    "RETIRE_UPGRADE_ONLY": frozenset(),
}
EXPECTED_BLOCKERS = {
    "organization.0026_remove_tenantrole_tenant_provider_tenant_provider_and_more",
    "organization.0027_drop_legacy_role_models",
    "organization.0034_roleassignment_remove_provider_internal_tenant_and_more",
    "organization.0035_delete_provider_and_more",
    "users.0008_alter_usergroup_options_token_provider_and_more",
    "users.0010_remove_usergroup_users_usergroup_unique_provider_name_active_and_more",
}


def _dispositions(disposition, rationale, migration_ids):
    return {migration_id: {"disposition": disposition, "rationale": rationale} for migration_id in migration_ids}


# This is a checked, human-reviewed policy. It is intentionally independent of
# migration/function names, reversibility syntax, and operation implementation.
SEMANTIC_DISPOSITIONS = {
    **_dispositions(
        "upgrade-only",
        (
            "Retains physical legacy report-designer columns while removing them from the historical ORM state "
            "before the durable 1.x contract is restored."
        ),
        {"extras.0103_remove_reporttemplate_advanced_mode_and_more"},
    ),
    **_dispositions(
        "upgrade-only",
        (
            "Restores durable report-designer fields, recovers serialized legacy values, stamps only the bounded "
            "live scheduled set, and reports out-of-bound custom HTML templates."
        ),
        {"extras.0105_reporttemplate_advanced_mode_and_more"},
    ),
    **_dispositions(
        "upgrade-only",
        "Preserves subscription renewal-term values while normalizing removed legacy lifecycle states during 1.0 upgrade.",
        {"subscriptions.0101_remove_subscription_auto_renewal_and_more"},
    ),
    **_dispositions(
        "upgrade-only",
        (
            "Populates stable opaque SCIM IDs idempotently and adds scoped group/user "
            "correlation fields while preserving legacy principals for the 1.x dual-read window."
        ),
        {"users.0101_user_scim_id_usergroup_external_id_usergroup_scim_id_and_more"},
    ),
    **_dispositions(
        "upgrade-only",
        (
            "Rewrites the twelve canonical django-q Schedule.func values to their issue-#445 domain-owner "
            "paths forward and back, preserving every other schedule field, PK and row multiplicity."
        ),
        {"extras.0110_issue445_task_paths"},
    ),
    **_dispositions(
        "upgrade-only",
        (
            "Copies endpoint configuration into immutable webhook delivery target snapshots; endpoint-less "
            "history remains unbound because exact legacy rule provenance cannot be reconstructed safely."
        ),
        {"extras.0112_backfill_webhookdelivery_targets"},
    ),
    **_dispositions(
        "upgrade-only",
        (
            "Validates and upgrades delayed legacy webhook retry schedules to assertion-only payloads, moves exact "
            "endpoint-less targets into encrypted durable snapshots, and irreversibly removes queue secrets."
        ),
        {"extras.0113_upgrade_legacy_webhook_retry_schedules"},
    ),
    **_dispositions(
        "required-fresh",
        "Enables the PostgreSQL btree_gist extension required by the asset reservation exclusion constraint.",
        {
            "assets.0051_assetreservation_assetreservation_no_overlap",
            "assets.0100_issue88_shard_42_assets_relations",
        },
    ),
    **_dispositions(
        "required-fresh",
        "Deterministically recreates the two required asset seed datasets on the replacement path.",
        {"assets.0100_issue88_shard_43_assets_seed"},
    ),
    **_dispositions(
        "required-fresh",
        "Pre-provisions the canonical global Missing status outside tenant-scoped audit mutation paths.",
        {"assets.0101_seed_canonical_missing_status"},
    ),
    **_dispositions(
        "required-fresh",
        "Creates application data required on an empty installation.",
        {
            "assets.0003_seed_status_labels",
            "assets.0043_seed_depreciation_policies",
        },
    ),
    **_dispositions(
        "safely-replaced-by-final-schema",
        "Database/state transition is superseded by the final schema on an empty installation.",
        {
            "assets.0033_remove_customfieldset_fields_and_more",
            "assets.0036_remove_installedsoftware",
            "assets.0037_remove_auditsession_created_by_and_more",
            "compliance.0011_remove_assetmaintenance",
            "core.0023_remove_event_eventrule_webhookendpoint",
            "core.0024_remove_exporttemplate_labeltemplate",
            "core.0025_remove_journalentry_bookmark_attachments",
            "core.0026_remove_reporttemplate_scheduledreport_reportgenerationarchive",
            "core.0027_remove_notificationchannel_alertrule_alertlog",
            "extras.0019_align_report_field_metadata",
            "extras.0022_fix_scheduledreport_channels_ref",
            "extras.0023_align_alerting_field_metadata",
            "organization.0025_delete_usergroup",
        },
    ),
    **_dispositions(
        "review-blocker",
        "Known greenfield and upgrade-support blocker; do not claim snapshot support.",
        {"organization.0027_drop_legacy_role_models"},
    ),
    **_dispositions(
        "upgrade-only",
        "Clears misleading signed_at values on non-accepted custody receipts; pending receipts must stay unsigned.",
        {"compliance.0102_clear_unsigned_receipt_timestamps"},
    ),
    **_dispositions(
        "upgrade-only",
        "Preserves or transforms data/content types for an existing installation.",
        {
            "assets.0020_alter_asset_requestable",
            "assets.0038_assetmaintenance",
            "assets.0039_repoint_assetmaintenance_contenttype",
            "assets.0040_null_to_empty_strings",
            "assets.0042_depreciation_v2",
            "assets.0044_assetrole_allows_components",
            "assets.0049_supplier_contacts_unification",
            "compliance.0009_auditsession_assetaudit",
            "compliance.0010_repoint_audit_contenttypes",
            "compliance.0012_null_to_empty_strings",
            "compliance.0014_auditsession_tenant",
            "core.0028_encrypt_emailsettings_smtp_password",
            "core.0029_null_to_empty_strings",
            "extras.0003_alter_dashboard_options_dashboard_is_default_and_more",
            "extras.0008_customfield_customfieldset",
            "extras.0009_repoint_customfield_contenttype",
            "extras.0010_customfield_object_types",
            "extras.0011_event_eventrule_webhookendpoint",
            "extras.0012_repoint_event_contenttypes",
            "extras.0013_exporttemplate_labeltemplate",
            "extras.0014_repoint_exporttemplate_contenttypes",
            "extras.0015_journalentry_bookmark_attachments",
            "extras.0016_repoint_group3_contenttypes",
            "extras.0017_reporttemplate_scheduledreport_reportgenerationarchive",
            "extras.0018_repoint_report_contenttypes",
            "extras.0020_notificationchannel_alertrule_alertlog",
            "extras.0021_repoint_alerting_contenttypes",
            "extras.0024_null_to_empty_strings",
            "extras.0025_objectwatch",
            "extras.0026_disable_script_event_rules",
            "extras.0028_encrypt_webhookendpoint_secret",
            "extras.0033_alertlog_uniq_open_alert_per_object",
            "extras.0034_journalentry_tenant",
            "extras.0101_issue88_drop_legacy_webhook_name_like",
            "inventory.0013_backfill_stock_tenant_and_provenance",
            "licenses.0008_remove_licenseseatassignment_chk_assignment_to_one_target_and_more",
            "organization.0014_null_to_empty_strings",
            "organization.0039_backfill_phase5_rbac",
            "organization.0040_remove_rolegrant_legacy_assignment_and_more",
            "procurement.0004_setup_groups",
            "software.0007_installedsoftware",
            "software.0008_repoint_installedsoftware_contenttype",
            "subscriptions.0006_remove_provider_contact_email_and_more",
            "users.0005_remove_token_key_token_digest_token_key_preview_and_more",
            "users.0007_usergroup",
            "users.0013_remove_usergroup_users_usergroup_unique_tenant_name_active_and_more",
        },
    ),
    **_dispositions(
        "upgrade-only",
        (
            "Backfills tenant attribution for legacy tenant-less alerts while marking "
            "ambiguous or unresolved targets for operator review."
        ),
        {"extras.0104_issue183_alert_tenant_reconciliation"},
    ),
    **_dispositions(
        "required-fresh",
        (
            "Adds additive WP-13 delivery observability fields and idempotently derives "
            "the filterable delivery_outcome from existing per-channel payloads without "
            "mutating delivery_status; fully reversible."
        ),
        {"extras.0108_alertlog_delivery_outcome"},
    ),
}


def _call_name(node):
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_noop(node):
    return (isinstance(node, ast.Name) and node.id == "noop") or (
        isinstance(node, ast.Attribute)
        and node.attr == "noop"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr in {"RunPython", "RunSQL"}
    )


def _literal_string(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _migration_class(tree):
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Migration":
            return node
    raise ValueError("missing Migration class")


def _assignment(class_node, name):
    for node in class_node.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return node.value
    return None


def _dependencies(node):
    dependencies = []
    special_bootstrap = False
    if not isinstance(node, (ast.List, ast.Tuple)):
        return dependencies, special_bootstrap
    for dependency in node.elts:
        if isinstance(dependency, ast.Tuple) and len(dependency.elts) == 2:
            app = _literal_string(dependency.elts[0])
            name = _literal_string(dependency.elts[1])
            if app and name:
                dependencies.append(f"{app}.{name}")
        elif isinstance(dependency, ast.Call) and _call_name(dependency.func) == "swappable_dependency":
            special_bootstrap = True
    return sorted(dependencies), special_bootstrap


def _replaces(node):
    if node is None:
        return []
    if not isinstance(node, (ast.List, ast.Tuple)):
        raise ValueError("replaces must be a list or tuple of migration targets")
    targets = []
    for target in node.elts:
        if not isinstance(target, ast.Tuple) or len(target.elts) != 2:
            raise ValueError("replaces contains a malformed migration target")
        app = _literal_string(target.elts[0])
        name = _literal_string(target.elts[1])
        if not app or not name:
            raise ValueError("replaces contains a non-literal migration target")
        targets.append(f"{app}.{name}")
    return targets


def _graph_summary(node_ids, edges):
    node_ids = set(node_ids)
    edges = sorted(set(edges))
    targets = {target for _, target in edges}
    sources = {source for source, _ in edges}
    return {
        "edges": [list(edge) for edge in edges],
        "leaves": sorted(node_ids - sources),
        "nodes": sorted(node_ids),
        "roots": sorted(node_ids - targets),
    }


def _has_cycle(node_ids, edges):
    successors = {node_id: [] for node_id in node_ids}
    indegrees = {node_id: 0 for node_id in node_ids}
    for source, target in edges:
        successors[source].append(target)
        indegrees[target] += 1
    ready = sorted(node_id for node_id, degree in indegrees.items() if degree == 0)
    visited = 0
    while ready:
        node_id = ready.pop(0)
        visited += 1
        for target in sorted(successors[node_id]):
            indegrees[target] -= 1
            if indegrees[target] == 0:
                ready.append(target)
                ready.sort()
    return visited != len(indegrees)


def _validate_issue88_shard_chain(replacement_migrations):
    issue88_shards = []
    for migration in replacement_migrations:
        match = ISSUE88_SHARD_RE.search(migration["id"])
        if match:
            issue88_shards.append((int(match.group(1)), migration))
    if not issue88_shards:
        return 0
    if len(issue88_shards) != len(replacement_migrations):
        raise ValueError("issue88 replacements must not mix with unnumbered shards")

    issue88_shards.sort(key=lambda item: item[0])
    ordinals = [ordinal for ordinal, _ in issue88_shards]
    expected_ordinals = list(range(1, len(issue88_shards) + 1))
    if ordinals != expected_ordinals:
        raise ValueError(f"issue88 shard ordinals must be contiguous: expected={expected_ordinals}, actual={ordinals}")

    previous_by_app = {}
    for index, (_, migration) in enumerate(issue88_shards):
        dependencies = set(migration["dependencies"])
        if index:
            predecessor = issue88_shards[index - 1][1]["id"]
            if predecessor not in dependencies:
                raise ValueError(f"issue88 shard lacks immediate predecessor: {migration['id']} -> {predecessor}")
        app = migration["id"].split(".", 1)[0]
        previous_same_app = previous_by_app.get(app)
        if previous_same_app and previous_same_app not in dependencies:
            raise ValueError(f"issue88 shard lacks previous same-app shard: {migration['id']} -> {previous_same_app}")
        previous_by_app[app] = migration["id"]
    return max(0, len(issue88_shards) - 1)


def _validate_post_transition_dependencies(
    post_transition_migrations,
    effective_graph,
    first_party_apps,
):
    if not post_transition_migrations:
        return
    effective_nodes = set(effective_graph["nodes"])
    effective_leaves = set(effective_graph["leaves"])
    post_transition_ids = {migration["id"] for migration in post_transition_migrations}
    known_predecessors = effective_nodes | post_transition_ids
    for migration in post_transition_migrations:
        dependencies = set(migration["dependencies"])
        unknown_dependencies = sorted(
            dependency
            for dependency in dependencies
            if dependency.split(".", 1)[0] in first_party_apps and dependency not in known_predecessors
        )
        if unknown_dependencies:
            raise ValueError(f"unknown first-party post-transition dependency targets: {unknown_dependencies}")
        missing_leaves = sorted(effective_leaves - dependencies)
        if missing_leaves:
            raise ValueError(
                f"post-transition migration lacks effective leaf dependency: {migration['id']} -> {missing_leaves}"
            )


def _reverse_argument(call, keyword, position):
    for item in call.keywords:
        if item.arg == keyword:
            return item.value
    if len(call.args) > position:
        return call.args[position]
    return None


def _operation_summary(operations_node):
    summary = {
        operation: {"with_noop_reverse": 0, "with_reverse": 0, "without_reverse": 0}
        for operation in OPERATION_TYPES[:2]
    }
    summary["SeparateDatabaseAndState"] = {"count": 0}
    summary["BtreeGistExtension"] = {"count": 0}

    if operations_node is None:
        return summary

    for call in (node for node in ast.walk(operations_node) if isinstance(node, ast.Call)):
        operation = _call_name(call.func)
        if operation in {"SeparateDatabaseAndState", "BtreeGistExtension"}:
            summary[operation]["count"] += 1
            continue
        if operation not in {"RunPython", "RunSQL"}:
            continue
        reverse = _reverse_argument(
            call,
            "reverse_code" if operation == "RunPython" else "reverse_sql",
            1,
        )
        if reverse is None:
            classification = "without_reverse"
        elif _is_noop(reverse):
            classification = "with_noop_reverse"
        else:
            classification = "with_reverse"
        summary[operation][classification] += 1

    return summary


def _is_strict_swappable_dependency(node):
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "swappable_dependency"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "migrations"
        and len(node.args) == 1
        and not node.keywords
        and isinstance(node.args[0], ast.Attribute)
        and node.args[0].attr == "AUTH_USER_MODEL"
        and isinstance(node.args[0].value, ast.Name)
        and node.args[0].value.id == "settings"
    )


def _strict_migration_pairs(node, field_name):
    if not isinstance(node, (ast.List, ast.Tuple)):
        raise ValueError(f"normalized migration {field_name} must be a literal list or tuple")
    result = []
    for pair in node.elts:
        if field_name == "dependencies" and _is_strict_swappable_dependency(pair):
            result.append({"swappable": "settings.AUTH_USER_MODEL"})
            continue
        if not isinstance(pair, ast.Tuple) or len(pair.elts) != 2:
            raise ValueError(f"normalized migration {field_name} contains an unparsed reference")
        values = [_literal_string(part) for part in pair.elts]
        if not all(values):
            raise ValueError(f"normalized migration {field_name} contains a non-literal reference")
        result.append(values)
    return result


def _operation_call_name(call, allowed_operation_names):
    if (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "migrations"
    ):
        return call.func.attr
    if isinstance(call.func, ast.Name) and call.func.id in allowed_operation_names:
        return call.func.id
    return None


def _semantic_ast(node):
    return ast.dump(node, annotate_fields=True, include_attributes=False)


def _strict_migration_operations(  # noqa: C901 - strict syntax validation enumerates every rejected call shape
    node,
    allowed_operation_names=frozenset({"BtreeGistExtension"}),
):
    if not isinstance(node, (ast.List, ast.Tuple)):
        raise ValueError("normalized migration operations must be a literal list or tuple")
    result = []
    for operation in node.elts:
        if not isinstance(operation, ast.Call):
            raise ValueError("normalized migration operations contains an unparsed operation")
        operation_name = _operation_call_name(operation, allowed_operation_names)
        if operation_name is None:
            raise ValueError("normalized migration operations contains a dynamic or unparsed operation")
        if any(isinstance(argument, ast.Starred) for argument in operation.args):
            raise ValueError("normalized migration operation must not use dynamic *args")
        if any(keyword.arg is None for keyword in operation.keywords):
            raise ValueError("normalized migration operation must not use dynamic **kwargs")
        keyword_names = [keyword.arg for keyword in operation.keywords]
        if len(keyword_names) != len(set(keyword_names)):
            raise ValueError("normalized migration operation has duplicate keyword arguments")

        representation = {
            "name": operation_name,
            "args": [_semantic_ast(argument) for argument in operation.args],
            "kwargs": [[keyword.arg, _semantic_ast(keyword.value)] for keyword in operation.keywords],
        }
        if operation_name == "SeparateDatabaseAndState":
            allowed = {"database_operations", "state_operations"}
            unknown = sorted(set(keyword_names) - allowed)
            if unknown:
                raise ValueError(f"normalized SeparateDatabaseAndState has unknown keywords: {unknown}")
            if len(operation.args) > 2:
                raise ValueError("normalized SeparateDatabaseAndState accepts at most two positional arguments")
            nested = {}
            for index, argument in enumerate(operation.args):
                field_name = ("database_operations", "state_operations")[index]
                if field_name in keyword_names:
                    raise ValueError(f"normalized SeparateDatabaseAndState supplies {field_name} twice")
                nested[field_name] = _strict_migration_operations(argument, allowed_operation_names)
            for keyword in operation.keywords:
                nested[keyword.arg] = _strict_migration_operations(keyword.value, allowed_operation_names)
            representation["nested"] = nested
        result.append(representation)
    return result


def _is_module_docstring(node, index):
    return (
        index == 0
        and isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _validate_allowlisted_module_statements(tree):
    """Admit declarations only; the trusted whole-module AST binds their exact semantics."""
    for index, node in enumerate(tree.body):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.FunctionDef)):
            continue
        if isinstance(node, ast.ClassDef):
            continue
        if _is_module_docstring(node, index):
            continue
        if (
            isinstance(node, ast.Assign)
            and node.targets
            and all(isinstance(target, ast.Name) for target in node.targets)
        ):
            continue
        raise ValueError(
            "normalized migration module-level statement is not allowlisted; "
            "only trusted imports, helpers, constants, and Migration declarations are permitted"
        )


def _strict_field_assignments(migration_class, field_name):
    direct_assignments = [
        node
        for node in migration_class.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == field_name for target in node.targets)
    ]
    if len(direct_assignments) > 1:
        raise ValueError(f"normalized migration {field_name} has duplicate assignments")
    for assignment in direct_assignments:
        if len(assignment.targets) != 1:
            raise ValueError(f"normalized migration {field_name} must use one direct assignment")
    return direct_assignments


def _validate_normalized_ast_declarations(path, tree, migration_class, *, allow_replaces=False):
    representation = {}
    allowed_operation_names = {"BtreeGistExtension"}
    allowed_operation_names.update(
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name != "Migration"
        and len(node.bases) == 1
        and isinstance(node.bases[0], ast.Name)
        and node.bases[0].id == "Operation"
        and not node.decorator_list
        and not node.keywords
    )
    for field_name in ("dependencies", "run_before", "replaces", "operations"):
        assignments = _strict_field_assignments(migration_class, field_name)
        if not assignments:
            if field_name in {"dependencies", "operations"}:
                raise ValueError(f"{path}: normalized migration must directly declare {field_name}")
            representation[field_name] = []
            continue
        if field_name == "operations":
            representation[field_name] = _strict_migration_operations(
                assignments[0].value,
                frozenset(allowed_operation_names),
            )
        else:
            representation[field_name] = _strict_migration_pairs(assignments[0].value, field_name)
        if field_name == "replaces" and not allow_replaces:
            raise ValueError(f"{path}: normalized migration layout must not contain a replaces declaration")
    return representation


def _validate_normalized_tree(path, tree, *, allow_replaces=False):
    _validate_allowlisted_module_statements(tree)
    all_migration_classes = [
        node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == "Migration"
    ]
    direct_migration_classes = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Migration"
    ]
    if len(all_migration_classes) != 1 or len(direct_migration_classes) != 1:
        raise ValueError(f"{path}: normalized migration must define exactly one direct Migration class")
    migration_class = direct_migration_classes[0]
    expected_base = (
        len(migration_class.bases) == 1
        and isinstance(migration_class.bases[0], ast.Attribute)
        and migration_class.bases[0].attr == "Migration"
        and isinstance(migration_class.bases[0].value, ast.Name)
        and migration_class.bases[0].value.id == "migrations"
    )
    if not expected_base:
        raise ValueError(f"{path}: Migration must directly inherit only migrations.Migration")
    if migration_class.decorator_list:
        raise ValueError(f"{path}: Migration class must be undecorated")
    if migration_class.keywords:
        raise ValueError(f"{path}: Migration class must not use metaclass or class keywords")
    representation = _validate_normalized_ast_declarations(
        path,
        tree,
        migration_class,
        allow_replaces=allow_replaces,
    )
    return migration_class, representation


def _assignment_field_name(node):
    if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
        return None
    return node.targets[0].id


def _literal_migration_reference(node):
    if not isinstance(node, ast.Tuple) or len(node.elts) != 2:
        return None
    values = [_literal_string(part) for part in node.elts]
    if any(value is None for value in values):
        return None
    return ".".join(values)


def _normalized_whole_module_ast(tree, *, remove_replaces=False, removed_reference_ids=frozenset()):
    """Return the trusted semantic contract with only reviewed transition edits normalized."""
    normalized = copy.deepcopy(tree)
    migration_class = next(
        node for node in normalized.body if isinstance(node, ast.ClassDef) and node.name == "Migration"
    )
    normalized_body = []
    for node in migration_class.body:
        field_name = _assignment_field_name(node)
        if field_name == "replaces" and remove_replaces:
            continue
        if (
            field_name in {"dependencies", "run_before"}
            and removed_reference_ids
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            node.value.elts = [
                reference
                for reference in node.value.elts
                if _literal_migration_reference(reference) not in removed_reference_ids
            ]
            if field_name == "run_before" and not node.value.elts:
                continue
        normalized_body.append(node)
    migration_class.body = normalized_body
    return _semantic_ast(normalized)


def _validate_normalized_migration_paths(source_root):
    fixture_root = Path(source_root).resolve()
    if not fixture_root.is_dir():
        raise ValueError(f"normalized fixture root is not a directory: {source_root}")
    for path in Path(source_root).glob("*/migrations/*.py"):
        if path.name == "__init__.py":
            continue
        resolved_path = path.resolve()
        if not resolved_path.is_file() or fixture_root not in resolved_path.parents:
            raise ValueError(f"normalized migration source escapes the fixture root: {path}")
        if not path.stem[:1].isdigit():
            raise ValueError(f"normalized migration module must have a numeric prefix: {path}")


def _git_show(repository_root, revision, path):
    result = subprocess.run(
        ["git", "-C", str(repository_root), "show", f"{revision}:{path}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise ValueError(f"trusted pre-cleanup evidence is unavailable: {revision}:{path}")
    return result.stdout


@functools.lru_cache(maxsize=4)
def _load_trusted_normalized_evidence(  # noqa: C901 - validates every independent evidence invariant
    repository_root_string,
):
    repository_root = Path(repository_root_string)
    object_check = subprocess.run(
        ["git", "-C", str(repository_root), "cat-file", "-e", f"{TRUSTED_PRE_CLEANUP_REVISION}^{{commit}}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if object_check.returncode:
        raise ValueError("trusted pre-cleanup migration evidence commit is unavailable")
    try:
        audit = json.loads(
            _git_show(
                repository_root,
                TRUSTED_PRE_CLEANUP_REVISION,
                "scripts/migration_audit.json",
            ).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("trusted pre-cleanup migration audit is malformed") from error
    if audit.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("trusted pre-cleanup migration audit schema is unsupported")

    replacements = [migration for migration in audit.get("migrations", []) if migration.get("is_replacement") is True]
    ordered = []
    for migration in replacements:
        migration_id = migration.get("id")
        if not isinstance(migration_id, str):
            raise ValueError("trusted pre-cleanup migration audit has a malformed replacement ID")
        match = ISSUE88_SHARD_RE.search(migration_id)
        if match is None:
            raise ValueError(f"trusted replacement lacks an issue88 ordinal: {migration_id}")
        ordered.append((int(match.group(1)), migration))
    ordered.sort(key=lambda item: item[0])
    if [ordinal for ordinal, _ in ordered] != list(range(1, len(ordered) + 1)):
        raise ValueError("trusted replacement shard ordinals are not exhaustive")

    baseline_ids = [migration["id"] for _, migration in ordered]
    if len(baseline_ids) != len(set(baseline_ids)) or not baseline_ids:
        raise ValueError("trusted replacement IDs are empty or duplicated")
    trusted_trees = {}
    replacement_targets = set()
    for _, migration in ordered:
        migration_id = migration["id"]
        app, name = migration_id.split(".", 1)
        expected_path = f"itambox/{app}/migrations/{name}.py"
        if migration.get("path") != expected_path:
            raise ValueError(f"trusted replacement path does not match its ID: {migration_id}")
        source = _git_show(repository_root, TRUSTED_PRE_CLEANUP_REVISION, expected_path).decode("utf-8")
        try:
            tree = ast.parse(source, filename=f"{TRUSTED_PRE_CLEANUP_REVISION}:{expected_path}")
            _, representation = _validate_normalized_tree(
                expected_path,
                tree,
                allow_replaces=True,
            )
        except (SyntaxError, ValueError) as error:
            raise ValueError(f"trusted replacement source is malformed: {migration_id}: {error}") from error
        trusted_replaces = set(migration.get("replaces", []))
        static_replaces = {".".join(pair) for pair in representation["replaces"]}
        if not trusted_replaces or trusted_replaces != static_replaces:
            raise ValueError(f"trusted replacement targets disagree with source: {migration_id}")
        replacement_targets.update(trusted_replaces)
        trusted_trees[migration_id] = tree

    historical_ids = audit.get("historical_graph", {}).get("nodes")
    if not isinstance(historical_ids, list) or set(historical_ids) != replacement_targets:
        raise ValueError("trusted replacements do not exhaust the pre-cleanup historical graph")
    historical_id_set = set(historical_ids)
    module_semantics = {
        migration_id: _normalized_whole_module_ast(
            tree,
            remove_replaces=True,
            removed_reference_ids=historical_id_set,
        )
        for migration_id, tree in trusted_trees.items()
    }
    effective_graph = audit.get("effective_graph", {})
    if set(effective_graph.get("nodes", [])) != set(baseline_ids):
        raise ValueError("trusted effective graph does not contain exactly the replacement IDs")
    migrations_by_id = {migration.get("id"): migration for migration in audit.get("migrations", [])}
    trusted_post_ids = set(audit.get("post_transition_migrations", []))
    if trusted_post_ids != POST_TRANSITION_MIGRATIONS:
        raise ValueError("trusted pre-cleanup post-transition migration universe differs from the reviewed set")
    post_module_semantics = {}
    for migration_id in sorted(trusted_post_ids):
        migration = migrations_by_id.get(migration_id)
        path = migration.get("path") if isinstance(migration, dict) else None
        if not isinstance(path, str):
            raise ValueError(f"trusted post-transition migration has no source path: {migration_id}")
        source = _git_show(repository_root, TRUSTED_PRE_CLEANUP_REVISION, path).decode("utf-8")
        try:
            tree = ast.parse(source, filename=f"{TRUSTED_PRE_CLEANUP_REVISION}:{path}")
            _validate_normalized_tree(path, tree)
            post_module_semantics[migration_id] = _normalized_whole_module_ast(tree)
        except (SyntaxError, ValueError) as error:
            raise ValueError(f"trusted post-transition source is malformed: {migration_id}: {error}") from error
    return {
        "baseline_ids": baseline_ids,
        "deleted_historical_ids": sorted(historical_ids),
        "root_ids": sorted(effective_graph.get("roots", [])),
        "baseline_leaf_ids": sorted(effective_graph.get("leaves", [])),
        "first_party_apps": sorted({migration_id.split(".", 1)[0] for migration_id in baseline_ids}),
        "module_semantics": module_semantics,
        "post_module_semantics": post_module_semantics,
    }


def _normalized_contract_list(contract, key):
    value = contract.get(key)
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"normalized migration contract field {key} must be a non-empty list of strings")
    if len(value) != len(set(value)):
        raise ValueError(f"normalized migration contract field {key} must be unique")
    return value


def _normalized_post_transition_leaves(contract):
    value = contract.get("post_transition_leaf_ids")
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError("normalized migration contract field post_transition_leaf_ids must be a list of strings")
    if len(value) != len(set(value)):
        raise ValueError("normalized migration contract field post_transition_leaf_ids must be unique")
    return value


def _validate_normalized_shard_ordinals(baseline_ids):
    ordinals = []
    for migration_id in baseline_ids:
        match = ISSUE88_SHARD_RE.search(migration_id)
        if match is None:
            raise ValueError(f"normalized baseline migration is missing an issue88 shard ordinal: {migration_id}")
        ordinals.append(int(match.group(1)))
    if ordinals != list(range(1, len(ordinals) + 1)):
        raise ValueError(f"normalized baseline shard ordinals must be contiguous: {ordinals}")


def _validate_normalized_source_ids(migrations, baseline_ids, deleted_historical_ids, first_party_apps):
    actual_by_id = {migration["id"]: migration for migration in migrations}
    if len(actual_by_id) != len(migrations):
        raise ValueError("normalized migration IDs must be unique")
    actual_ids = set(actual_by_id)
    baseline_id_set = set(baseline_ids)
    actual_post_ids = actual_ids - baseline_id_set
    if actual_post_ids != POST_TRANSITION_MIGRATIONS:
        raise ValueError(
            "normalized post-transition IDs must exactly match the closed reviewed set: "
            f"missing={sorted(POST_TRANSITION_MIGRATIONS - actual_post_ids)}, "
            f"unknown={sorted(actual_post_ids - POST_TRANSITION_MIGRATIONS)}"
        )
    if actual_ids - actual_post_ids != baseline_id_set:
        raise ValueError(
            "normalized baseline IDs do not match the current non-post-transition migration files: "
            f"expected={sorted(baseline_ids)}, actual={sorted(actual_ids - actual_post_ids)}"
        )
    if set(first_party_apps) != {migration_id.split(".", 1)[0] for migration_id in actual_ids}:
        raise ValueError("normalized migration contract first_party_apps do not match the current source")
    if any(migration["is_replacement"] for migration in migrations):
        raise ValueError("normalized migration layout must not contain a reintroduced replaces declaration")
    if set(deleted_historical_ids) & actual_ids:
        raise ValueError("normalized migration contract deleted_historical_ids are present in the current source")
    _validate_normalized_shard_ordinals(baseline_ids)
    return actual_by_id, actual_ids, actual_post_ids


def _normalized_post_transition_dispositions():
    if set(POST_TRANSITION_DISPOSITION_GROUPS) != NORMALIZED_POST_TRANSITION_DISPOSITIONS:
        raise ValueError("normalized post-transition disposition policy has unknown or missing groups")
    seen = set()
    dispositions = {}
    for disposition, migration_ids in POST_TRANSITION_DISPOSITION_GROUPS.items():
        overlap = seen & migration_ids
        if overlap:
            raise ValueError(f"normalized post-transition dispositions overlap: {sorted(overlap)}")
        for migration_id in migration_ids:
            dispositions[migration_id] = disposition
        seen.update(migration_ids)
    if seen != POST_TRANSITION_MIGRATIONS:
        raise ValueError(
            "normalized post-transition dispositions must cover exactly the closed reviewed ID set: "
            f"missing={sorted(POST_TRANSITION_MIGRATIONS - seen)}, "
            f"unknown={sorted(seen - POST_TRANSITION_MIGRATIONS)}"
        )
    return dispositions


def _validate_normalized_contract_shape(  # noqa: C901 - contract fields deliberately fail closed one by one
    contract,
    migrations,
    trusted,
):
    if not isinstance(contract, dict) or contract.get("mode") != "normalized":
        raise ValueError("normalized migration contract must explicitly set mode=normalized")
    allowed_fields = {
        "mode",
        "fixture_only",
        "trusted_pre_cleanup_revision",
        "first_party_apps",
        "baseline_ids",
        "deleted_historical_ids",
        "root_ids",
        "baseline_leaf_ids",
        "post_transition_leaf_ids",
        "special_users_bootstrap",
    }
    unsupported_fields = sorted(set(contract) - allowed_fields)
    if unsupported_fields:
        raise ValueError(f"normalized migration contract has unsupported self-authorizing fields: {unsupported_fields}")
    if contract.get("fixture_only") is not True:
        raise ValueError("normalized migration contract must be explicitly fixture_only")
    if contract.get("trusted_pre_cleanup_revision") != TRUSTED_PRE_CLEANUP_REVISION:
        raise ValueError("normalized migration contract must name the trusted pre-cleanup revision")
    baseline_ids = _normalized_contract_list(contract, "baseline_ids")
    deleted_historical_ids = _normalized_contract_list(contract, "deleted_historical_ids")
    root_ids = _normalized_contract_list(contract, "root_ids")
    baseline_leaf_ids = _normalized_contract_list(contract, "baseline_leaf_ids")
    post_transition_leaf_ids = _normalized_post_transition_leaves(contract)
    first_party_apps = contract.get("first_party_apps")
    if not isinstance(first_party_apps, list) or first_party_apps != sorted(set(first_party_apps)):
        raise ValueError("normalized migration contract first_party_apps must be sorted and unique")

    evidence_fields = {
        "baseline_ids": baseline_ids,
        "deleted_historical_ids": deleted_historical_ids,
        "root_ids": root_ids,
        "baseline_leaf_ids": baseline_leaf_ids,
    }
    for key, actual in evidence_fields.items():
        if actual != trusted[key]:
            raise ValueError(f"normalized migration contract {key} disagrees with trusted pre-cleanup evidence")
    if first_party_apps != trusted["first_party_apps"]:
        raise ValueError("normalized migration contract first_party_apps disagrees with trusted pre-cleanup evidence")

    actual_by_id, actual_ids, actual_post_ids = _validate_normalized_source_ids(
        migrations,
        baseline_ids,
        deleted_historical_ids,
        first_party_apps,
    )
    if not set(post_transition_leaf_ids) <= actual_post_ids:
        raise ValueError(
            "normalized migration contract post_transition_leaf_ids must name current post-transition migrations"
        )
    missing_known_blockers = EXPECTED_BLOCKERS - set(deleted_historical_ids)
    if missing_known_blockers:
        raise ValueError(
            "normalized migration contract deleted_historical_ids must cover known semantic blockers: "
            f"{sorted(missing_known_blockers)}"
        )
    special_users_bootstrap = contract.get("special_users_bootstrap")
    if not isinstance(special_users_bootstrap, str) or special_users_bootstrap not in set(baseline_ids):
        raise ValueError("normalized migration contract special_users_bootstrap is not a baseline ID")
    if special_users_bootstrap != "users.0000_issue88_shard_01_users_bootstrap":
        raise ValueError("normalized migration contract must preserve the shipped users bootstrap")
    return {
        "baseline_ids": baseline_ids,
        "deleted_historical_ids": deleted_historical_ids,
        "root_ids": root_ids,
        "baseline_leaf_ids": baseline_leaf_ids,
        "post_transition_leaf_ids": post_transition_leaf_ids,
        "special_users_bootstrap": special_users_bootstrap,
        "actual_by_id": actual_by_id,
        "actual_ids": actual_ids,
        "actual_post_ids": actual_post_ids,
        "first_party_apps": set(first_party_apps),
        "post_transition_dispositions": _normalized_post_transition_dispositions(),
    }


def _validate_normalized_semantics(details, trusted):
    for migration_id in details["baseline_ids"]:
        actual = _normalized_whole_module_ast(
            details["actual_by_id"][migration_id]["_normalized_tree"],
            removed_reference_ids=set(details["deleted_historical_ids"]),
        )
        if actual != trusted["module_semantics"][migration_id]:
            raise ValueError(
                f"normalized baseline whole-module semantics disagree with trusted pre-cleanup evidence: {migration_id}"
            )
    for migration_id, disposition in details["post_transition_dispositions"].items():
        if disposition != "RETAIN":
            raise ValueError(f"normalized post-transition disposition must be RETAIN: {migration_id}")
        actual = _normalized_whole_module_ast(details["actual_by_id"][migration_id]["_normalized_tree"])
        if actual != trusted["post_module_semantics"][migration_id]:
            raise ValueError(
                f"retained post-transition whole-module semantics disagree with trusted pre-cleanup evidence: "
                f"{migration_id}"
            )


def _validate_normalized_references(details, migrations):
    deleted_ids = set(details["deleted_historical_ids"])
    first_party_apps = details["first_party_apps"]
    actual_ids = details["actual_ids"]
    for migration in migrations:
        references = (*migration["dependencies"], *migration["run_before"])
        for reference in references:
            if reference in deleted_ids:
                raise ValueError(f"normalized migration references deleted historical migration: {reference}")
            if reference.split(".", 1)[0] in first_party_apps and reference not in actual_ids:
                raise ValueError(f"unknown first-party normalized migration reference: {reference}")


def _validate_normalized_order(details):
    ordered_baseline = [details["actual_by_id"][migration_id] for migration_id in details["baseline_ids"]]
    for previous, current in zip(ordered_baseline, ordered_baseline[1:], strict=False):
        if previous["id"] not in current["dependencies"]:
            raise ValueError(f"normalized baseline order/dependency mismatch: {current['id']} -> {previous['id']}")


_RUNTIME_INSPECTOR = r"""
import ast
import hashlib
import importlib.util
import inspect
import json
import os
import sys

repository_root, payload_path = sys.argv[1:]
sys.path.insert(0, os.path.join(repository_root, "itambox"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings.dev")
os.environ.setdefault("ITAMBOX_ENV", "dev")
import django
django.setup()
from django.conf import settings
from django.db.migrations import SeparateDatabaseAndState
from django.db.migrations.writer import MigrationWriter

swappable_user_dependency = (settings.AUTH_USER_MODEL.split(".", 1)[0], "__first__")

def pairs(value):
    result = []
    for item in value:
        if tuple(item) in {("__setting__", "AUTH_USER_MODEL"), swappable_user_dependency}:
            result.append({"swappable": "settings.AUTH_USER_MODEL"})
        else:
            result.append(list(item))
    return result

def operations(value):
    result = []
    for operation in value:
        name, deconstructed_args, deconstructed_kwargs = operation.deconstruct()
        args, kwargs = operation._constructor_args
        serialized = {
            "path": name,
            "args": MigrationWriter.serialize(deconstructed_args)[0],
            "kwargs": MigrationWriter.serialize(deconstructed_kwargs)[0],
        }
        item = {
            "name": name.rsplit(".", 1)[-1],
            "arg_count": len(args),
            "keyword_names": list(kwargs),
            "value_fingerprint": hashlib.sha256(
                json.dumps(serialized, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
        }
        if item["name"] == "RunPython":
            callables = {}
            for field_name in ("code", "reverse_code"):
                function = getattr(operation, field_name)
                if function is None:
                    callables[field_name] = None
                    continue
                try:
                    source = inspect.getsource(function)
                except (OSError, TypeError) as error:
                    raise RuntimeError(
                        f"cannot inspect RunPython {field_name} source for {function!r}"
                    ) from error
                callables[field_name] = {
                    "module": function.__module__,
                    "qualname": function.__qualname__,
                    "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                }
            item["callables"] = callables
        if isinstance(operation, SeparateDatabaseAndState):
            item["nested"] = {
                "database_operations": operations(operation.database_operations),
                "state_operations": operations(operation.state_operations),
            }
        result.append(item)
    return result

payload = json.load(open(payload_path, encoding="utf-8"))
output = {}
for index, item in enumerate(payload):
    spec = importlib.util.spec_from_file_location(f"_migration_audit_fixture_{index}", item["path"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cls = module.Migration
    source = open(item["path"], encoding="utf-8").read()
    tree = ast.parse(source, filename=item["path"])
    migration_class = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Migration"
    )
    operations_assignment = next(
        node
        for node in migration_class.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "operations"
    )
    declared_operations = eval(
        compile(ast.Expression(operations_assignment.value), item["path"], "eval"),
        module.__dict__,
    )
    output[item["id"]] = {
        "dependencies": pairs(cls.dependencies),
        "run_before": pairs(cls.run_before),
        "replaces": pairs(getattr(cls, "replaces", [])),
        "operations": operations(cls.operations),
        "declared_operations": operations(declared_operations),
    }
print(json.dumps(output, sort_keys=True))
"""


def _runtime_operation_metadata(operations):
    result = []
    for operation in operations:
        item = {
            "name": operation["name"],
            "arg_count": len(operation["args"]),
            "keyword_names": [keyword[0] for keyword in operation["kwargs"]],
        }
        if operation["name"] == "SeparateDatabaseAndState":
            nested = operation.get("nested", {})
            item["nested"] = {
                "database_operations": _runtime_operation_metadata(nested.get("database_operations", [])),
                "state_operations": _runtime_operation_metadata(nested.get("state_operations", [])),
            }
        result.append(item)
    return result


def _runtime_operation_shape(operations):
    result = []
    for operation in operations:
        item = {
            "name": operation.get("name"),
            "arg_count": operation.get("arg_count"),
            "keyword_names": operation.get("keyword_names"),
        }
        if operation.get("name") == "SeparateDatabaseAndState":
            nested = operation.get("nested", {})
            item["nested"] = {
                "database_operations": _runtime_operation_shape(nested.get("database_operations", [])),
                "state_operations": _runtime_operation_shape(nested.get("state_operations", [])),
            }
        result.append(item)
    return result


def _run_runtime_inspector(repository_root, payload_path):
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if key in os.environ}
    environment.update(
        {
            "ITAMBOX_ENV": "dev",
            "DJANGO_SETTINGS_MODULE": "core.settings.dev",
        }
    )
    try:
        result = subprocess.run(
            [sys.executable, "-I", "-c", _RUNTIME_INSPECTOR, str(repository_root), str(payload_path)],
            cwd=repository_root / "itambox",
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError("normalized migration isolated runtime import timed out") from error
    except OSError as error:
        raise ValueError(f"normalized migration isolated runtime process failed: {error}") from error
    if result.returncode:
        detail = result.stderr.strip().splitlines()[-1:] or ["unknown import failure"]
        raise ValueError(f"normalized migration isolated runtime import failed: {detail[0]}")
    return result


def _validate_normalized_runtime_import(details, source_root, repository_root):
    fixture_root = Path(source_root).resolve()
    payload = []
    expected = {}
    for migration_id in sorted(details["actual_ids"]):
        migration = details["actual_by_id"][migration_id]
        source_path = (fixture_root.parent / migration["path"]).resolve()
        if fixture_root not in source_path.parents:
            raise ValueError(f"normalized runtime import source escapes the fixture root: {migration_id}")
        static = migration["_normalized_static"]
        payload.append({"id": migration_id, "path": str(source_path)})
        expected[migration_id] = {
            "dependencies": static["dependencies"],
            "run_before": static["run_before"],
            "replaces": static["replaces"],
            "operations": _runtime_operation_metadata(static["operations"]),
        }
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        json.dump(payload, handle)
        payload_path = Path(handle.name)
    try:
        result = _run_runtime_inspector(repository_root, payload_path)
    finally:
        payload_path.unlink(missing_ok=True)
    try:
        actual = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("normalized migration isolated runtime import returned malformed metadata") from error
    for migration_id, metadata in actual.items():
        runtime_operations = metadata.get("operations")
        declared_operations = metadata.pop("declared_operations", None)
        if runtime_operations != declared_operations:
            raise ValueError(
                f"normalized migration runtime operation values disagree with the trusted declaration: {migration_id}"
            )
        metadata["operations"] = _runtime_operation_shape(runtime_operations or [])
    if actual != expected:
        mismatches = sorted(
            migration_id for migration_id in expected if actual.get(migration_id) != expected[migration_id]
        )
        first = mismatches[0] if mismatches else "<missing-output>"
        raise ValueError(
            "normalized migration runtime metadata disagrees with static representation: "
            f"{mismatches}; first={first}; static={expected.get(first)!r}; runtime={actual.get(first)!r}"
        )


def _validate_normalized_contract(contract, migrations, source_root, repository_root):
    """Validate a future normalized layout without changing the live tree."""
    trusted = _load_trusted_normalized_evidence(str(Path(repository_root).resolve()))
    details = _validate_normalized_contract_shape(contract, migrations, trusted)
    _validate_normalized_references(details, migrations)
    _validate_normalized_semantics(details, trusted)
    _validate_normalized_order(details)
    _validate_normalized_runtime_import(details, source_root, Path(repository_root).resolve())
    return {
        key: details[key]
        for key in (
            "baseline_ids",
            "deleted_historical_ids",
            "root_ids",
            "baseline_leaf_ids",
            "post_transition_leaf_ids",
            "special_users_bootstrap",
            "post_transition_dispositions",
        )
    } | {"trusted_pre_cleanup_revision": TRUSTED_PRE_CLEANUP_REVISION}


def build_inventory(  # noqa: C901 - graph validation is intentionally one coordinated pass
    source_root,
    semantic_dispositions=None,
    expected_blockers=None,
    *,
    layout=None,
    normalized_contract=None,
    repository_root=None,
):
    source_root = Path(source_root)
    repository_root = Path(__file__).resolve().parents[1] if repository_root is None else Path(repository_root)
    using_default_semantic_policy = semantic_dispositions is None
    using_default_expected_blockers = expected_blockers is None
    semantic_dispositions = SEMANTIC_DISPOSITIONS if semantic_dispositions is None else semantic_dispositions
    expected_blockers = set(EXPECTED_BLOCKERS) if expected_blockers is None else set(expected_blockers)
    layout = "transitional" if layout is None else layout
    if layout not in {"transitional", "normalized"}:
        raise ValueError(f"unsupported migration audit layout: {layout}")
    if layout == "normalized":
        _validate_normalized_migration_paths(source_root)
    migrations = []
    for path in sorted(source_root.glob("*/migrations/[0-9]*.py")):
        app = path.parent.parent.name
        migration_id = f"{app}.{path.stem}"
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if layout == "normalized":
                migration_class, normalized_static = _validate_normalized_tree(path, tree)
            else:
                migration_class = _migration_class(tree)
                normalized_static = None
        except (SyntaxError, ValueError) as error:
            raise ValueError(f"{path}: {error}") from error
        dependencies, has_swappable_dependency = _dependencies(_assignment(migration_class, "dependencies"))
        run_before, _ = _dependencies(_assignment(migration_class, "run_before"))
        replaces_node = _assignment(migration_class, "replaces")
        try:
            replaces = _replaces(replaces_node)
        except ValueError as error:
            raise ValueError(f"{path}: {error}") from error
        special_bootstrap = app == "users" and "users.0001_initial" in run_before
        operations = _operation_summary(_assignment(migration_class, "operations"))
        custom_operation_presence = {
            "RunPython": sum(operations["RunPython"].values()) > 0,
            "RunSQL": sum(operations["RunSQL"].values()) > 0,
            "SeparateDatabaseAndState": operations["SeparateDatabaseAndState"]["count"] > 0,
            "BtreeGistExtension": operations["BtreeGistExtension"]["count"] > 0,
        }
        reviewed_disposition = semantic_dispositions.get(migration_id)
        migrations.append(
            {
                "dependencies": dependencies,
                "id": migration_id,
                "is_replacement": replaces_node is not None,
                "operations": operations,
                "path": path.relative_to(source_root.parent).as_posix(),
                "replaces": replaces,
                "reviewed_disposition": reviewed_disposition,
                "run_before": run_before,
                "special_users_bootstrap": special_bootstrap,
                "swappable_user_dependency": has_swappable_dependency,
                "syntactic_facts": {
                    "has_custom_operations": custom_operation_presence,
                },
                "_normalized_static": normalized_static,
                "_normalized_tree": tree if layout == "normalized" else None,
            }
        )

    replacement_migrations = [migration for migration in migrations if migration["is_replacement"]]
    post_transition_migrations = [
        migration for migration in migrations if migration["id"] in POST_TRANSITION_MIGRATIONS
    ]
    normalized_details = None
    if layout == "normalized":
        normalized_details = _validate_normalized_contract(
            normalized_contract,
            migrations,
            source_root,
            repository_root,
        )
        historical_migrations = [
            migration for migration in migrations if migration["id"] in set(normalized_details["baseline_ids"])
        ]
    else:
        historical_migrations = [
            migration
            for migration in migrations
            if not migration["is_replacement"] and migration["id"] not in POST_TRANSITION_MIGRATIONS
        ]
    node_ids = {migration["id"] for migration in historical_migrations}
    user_bootstraps = sorted(
        migration["id"] for migration in historical_migrations if migration["special_users_bootstrap"]
    )
    if layout == "normalized":
        user_bootstraps = [normalized_details["special_users_bootstrap"]]
    user_bootstrap = user_bootstraps[0] if len(user_bootstraps) == 1 else None
    edges = {
        (dependency, migration["id"])
        for migration in historical_migrations
        for dependency in migration["dependencies"]
        if dependency in node_ids
    }
    edges.update(
        (migration["id"], target)
        for migration in historical_migrations
        for target in migration["run_before"]
        if target in node_ids
    )
    if user_bootstrap:
        edges.update(
            (user_bootstrap, migration["id"])
            for migration in historical_migrations
            if (layout != "normalized" or migration["id"] != user_bootstrap) and migration["swappable_user_dependency"]
        )
    edges = sorted(edges)
    historical_graph = _graph_summary(node_ids, edges)
    first_party_apps = {migration["id"].split(".", 1)[0] for migration in migrations}
    explicit_replacement_chain_edges = 0

    if replacement_migrations:
        explicit_replacement_chain_edges = _validate_issue88_shard_chain(replacement_migrations)
        replacement_targets = [target for migration in replacement_migrations for target in migration["replaces"]]
        unknown_targets = sorted(set(replacement_targets) - node_ids)
        if unknown_targets:
            raise ValueError(f"unknown replacement targets: {unknown_targets}")
        duplicate_targets = sorted(
            target for target in set(replacement_targets) if replacement_targets.count(target) > 1
        )
        if duplicate_targets:
            raise ValueError(f"duplicate replacement targets: {duplicate_targets}")
        uncovered = sorted(node_ids - set(replacement_targets))
        if uncovered:
            raise ValueError(f"replacement coverage incomplete: {uncovered}")

        replacement_by_target = {
            target: migration["id"] for migration in replacement_migrations for target in migration["replaces"]
        }
        effective_node_ids = {migration["id"] for migration in replacement_migrations}
        unknown_dependency_targets = sorted(
            {
                dependency
                for migration in replacement_migrations
                for dependency in migration["dependencies"]
                if dependency.split(".", 1)[0] in first_party_apps and dependency not in effective_node_ids
            }
        )
        if unknown_dependency_targets:
            raise ValueError(f"unknown first-party replacement dependency targets: {unknown_dependency_targets}")
        unknown_run_before_targets = sorted(
            {
                target
                for migration in replacement_migrations
                for target in migration["run_before"]
                if target.split(".", 1)[0] in first_party_apps
                and target not in effective_node_ids
                and target not in replacement_by_target
            }
        )
        if unknown_run_before_targets:
            raise ValueError(f"unknown first-party replacement run_before targets: {unknown_run_before_targets}")
        effective_edges = {
            (replacement_by_target[source], replacement_by_target[target])
            for source, target in edges
            if replacement_by_target[source] != replacement_by_target[target]
        }
        effective_edges.update(
            (dependency, migration["id"])
            for migration in replacement_migrations
            for dependency in migration["dependencies"]
            if dependency in effective_node_ids
        )
        effective_edges.update(
            (
                migration["id"],
                replacement_by_target.get(target, target),
            )
            for migration in replacement_migrations
            for target in migration["run_before"]
            if target in effective_node_ids or target in replacement_by_target
            if migration["id"] != replacement_by_target.get(target, target)
        )
        if _has_cycle(effective_node_ids, effective_edges):
            raise ValueError("effective replacement graph contains a cycle")
        effective_graph = _graph_summary(effective_node_ids, effective_edges)
    else:
        effective_graph = historical_graph

    if layout == "normalized":
        if _has_cycle(historical_graph["nodes"], edges):
            raise ValueError("normalized baseline graph contains a cycle")
        if historical_graph["roots"] != sorted(normalized_details["root_ids"]):
            raise ValueError(
                "normalized baseline roots mismatch: "
                f"expected={sorted(normalized_details['root_ids'])}, actual={historical_graph['roots']}"
            )
        if historical_graph["leaves"] != sorted(normalized_details["baseline_leaf_ids"]):
            raise ValueError(
                "normalized baseline leaves mismatch: "
                f"expected={sorted(normalized_details['baseline_leaf_ids'])}, actual={historical_graph['leaves']}"
            )
        post_graph_edges = {tuple(edge) for edge in effective_graph["edges"]}
        post_ids = {migration["id"] for migration in post_transition_migrations}
        effective_ids = set(effective_graph["nodes"])
        for migration in post_transition_migrations:
            for dependency in migration["dependencies"]:
                if dependency in effective_ids or dependency in post_ids:
                    post_graph_edges.add((dependency, migration["id"]))
            for target in migration["run_before"]:
                if target in effective_ids or target in post_ids:
                    post_graph_edges.add((migration["id"], target))
        post_graph = _graph_summary(effective_ids | post_ids, post_graph_edges)
        if _has_cycle(post_graph["nodes"], post_graph_edges):
            raise ValueError("normalized migration graph contains a cycle")
        if len(post_graph["roots"]) != 1:
            raise ValueError(f"normalized migration graph must have one root: {post_graph['roots']}")
        if post_graph["leaves"] != sorted(normalized_details["post_transition_leaf_ids"]):
            raise ValueError(
                "normalized post-transition leaves mismatch: "
                f"expected={sorted(normalized_details['post_transition_leaf_ids'])}, actual={post_graph['leaves']}"
            )

    _validate_post_transition_dependencies(
        post_transition_migrations,
        effective_graph,
        first_party_apps,
    )

    targets = {target for _, target in edges}
    sources = {source for source, _ in edges}
    by_id = {migration["id"]: migration for migration in migrations}
    custom_operation_ids = {
        migration["id"]
        for migration in migrations
        if any(migration["syntactic_facts"]["has_custom_operations"].values())
    }
    if layout == "normalized" and using_default_semantic_policy:
        current_migration_ids = {migration["id"] for migration in migrations}
        semantic_dispositions = {
            migration_id: policy
            for migration_id, policy in semantic_dispositions.items()
            if migration_id in current_migration_ids
        }
    if layout == "normalized" and using_default_expected_blockers:
        expected_blockers = set()
    policy_ids = set(semantic_dispositions)
    malformed_policy = sorted(
        migration_id
        for migration_id, policy in semantic_dispositions.items()
        if set(policy) != {"disposition", "rationale"}
        or not isinstance(policy["rationale"], str)
        or not policy["rationale"].strip()
    )
    if malformed_policy:
        raise ValueError(f"malformed semantic policy entries: {malformed_policy}")
    invalid_dispositions = {
        migration_id: policy["disposition"]
        for migration_id, policy in semantic_dispositions.items()
        if policy["disposition"] not in ALLOWED_DISPOSITIONS
    }
    missing_blockers = expected_blockers - node_ids
    if invalid_dispositions:
        raise ValueError(f"invalid semantic dispositions: {invalid_dispositions}")
    if custom_operation_ids != policy_ids:
        raise ValueError(
            "semantic policy coverage mismatch: "
            f"unclassified={sorted(custom_operation_ids - policy_ids)}, "
            f"without_custom_operations={sorted(policy_ids - custom_operation_ids)}"
        )
    if missing_blockers:
        raise ValueError(f"unknown expected blockers: {sorted(missing_blockers)}")

    per_app = {}
    for migration in historical_migrations:
        app = migration["id"].split(".", 1)[0]
        app_ids = {item["id"] for item in historical_migrations if item["id"].startswith(f"{app}.")}
        app_edges = {(source, target) for source, target in edges if source in app_ids and target in app_ids}
        app_sources = {source for source, _ in app_edges}
        app_targets = {target for _, target in app_edges}
        per_app[app] = {
            "local_roots": sorted(app_ids - app_targets),
            "local_leaves": sorted(app_ids - app_sources),
        }
    result = {
        "schema_version": SCHEMA_VERSION,
        "historical_graph": historical_graph,
        "effective_graph": effective_graph,
        "post_transition_migrations": [migration["id"] for migration in post_transition_migrations],
        "summary": {
            "first_party_edges": len(edges),
            "first_party_nodes": len(historical_migrations),
            "replacement_shards": len(replacement_migrations),
            "replacement_targets": sum(len(migration["replaces"]) for migration in replacement_migrations),
            "post_transition_migrations": len(post_transition_migrations),
            "explicit_replacement_chain_edges": explicit_replacement_chain_edges,
            "missing_replacement_targets": 0,
            "duplicate_replacement_targets": 0,
            "effective_replacement_quotient_acyclic": True,
            "custom_operation_file_counts": {
                operation: sum(
                    migration["syntactic_facts"]["has_custom_operations"][operation]
                    for migration in historical_migrations
                )
                for operation in OPERATION_TYPES
            },
            "global_leaves": sorted(node_ids - sources),
            "global_roots": sorted(node_ids - targets),
            "per_app_local_roots_and_leaves": per_app,
        },
        "reviewed_semantics": {
            "blockers": sorted(expected_blockers),
            **{
                disposition.replace("-", "_"): sorted(
                    migration_id
                    for migration_id, policy in semantic_dispositions.items()
                    if policy["disposition"] == disposition
                )
                for disposition in sorted(ALLOWED_DISPOSITIONS)
            },
        },
        "special_users_bootstrap": {
            "migration": user_bootstrap,
            "run_before": (by_id[user_bootstrap]["run_before"] if user_bootstrap else []),
            "swappable_dependents": sorted(
                migration_id
                for migration_id, migration in by_id.items()
                if not migration["is_replacement"] and migration["swappable_user_dependency"]
            ),
        },
        "migrations": migrations,
    }
    if layout == "normalized":
        result["layout"] = "normalized"
        result["normalized_contract"] = normalized_details
    for migration in migrations:
        migration.pop("_normalized_static", None)
        migration.pop("_normalized_tree", None)
    return result


_PREFLIGHT_MANIFEST_LIST_FIELDS = (
    "first_party_apps",
    "historical_ids",
    "replacement_ids",
    "replacement_target_ids",
    "baseline_ids",
    "post_transition_ids",
    "post_transition_leaf_ids",
    "current_leaf_ids",
)
_GIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SUPPORTED_PREDECESSOR_STATES = frozenset(
    {
        "complete-old-history-no-replacement",
        "complete-replacement-recognition",
    }
)


def load_preflight_manifest(path):
    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read migration preflight manifest: {path}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("migration preflight manifest must be a JSON object")
    return manifest


def _manifest_list(manifest, key):
    value = manifest.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"migration preflight manifest field {key} must be a list of strings")
    if value != sorted(set(value)):
        raise ValueError(f"migration preflight manifest field {key} must be sorted and unique")
    return value


def _post_transition_graph(inventory):
    post_ids = set(inventory["post_transition_migrations"])
    effective_ids = set(inventory["effective_graph"]["nodes"])
    edges = {tuple(edge) for edge in inventory["effective_graph"]["edges"]}
    by_id = {migration["id"]: migration for migration in inventory["migrations"]}
    for migration_id in post_ids:
        migration = by_id[migration_id]
        for dependency in migration["dependencies"]:
            if dependency in effective_ids or dependency in post_ids:
                edges.add((dependency, migration_id))
        for target in migration["run_before"]:
            if target in effective_ids or target in post_ids:
                edges.add((migration_id, target))
    return _graph_summary(effective_ids | post_ids, edges)


def _assert_manifest_ids(manifest, key, expected):
    actual = manifest.get(key)
    if actual != expected:
        actual_count = len(actual) if isinstance(actual, list) else "invalid"
        raise ValueError(
            f"migration preflight manifest field {key} mismatch: expected {len(expected)}, got {actual_count}"
        )


def _validate_manifest_predecessors(manifest):
    transition_release_sha = manifest.get("transition_release_sha")
    if not isinstance(transition_release_sha, str) or not _GIT_SHA_RE.fullmatch(transition_release_sha):
        raise ValueError("migration preflight manifest transition_release_sha must be a lowercase 40-character Git SHA")
    predecessors = manifest.get("supported_predecessors")
    if not isinstance(predecessors, list) or not predecessors:
        raise ValueError("migration preflight manifest supported_predecessors must be a non-empty list")
    names = []
    revisions = set()
    for predecessor in predecessors:
        if not isinstance(predecessor, dict):
            raise ValueError("migration preflight manifest predecessor entries must be objects")
        name = predecessor.get("name")
        revision = predecessor.get("revision")
        state = predecessor.get("state")
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError("migration preflight manifest predecessor names must be unique and non-empty")
        if not isinstance(revision, str) or not _GIT_SHA_RE.fullmatch(revision):
            raise ValueError(
                "migration preflight manifest predecessor revisions must be lowercase 40-character Git SHAs"
            )
        if state not in SUPPORTED_PREDECESSOR_STATES:
            raise ValueError("migration preflight manifest predecessor state is not recognized")
        names.append(name)
        revisions.add(revision)
    if transition_release_sha not in revisions:
        raise ValueError("migration preflight manifest transition release is not a named predecessor revision")


def validate_preflight_manifest_git_objects(manifest, repository_root):
    """Require every declared predecessor identity to resolve to a local commit object."""

    revisions = {manifest["transition_release_sha"]}
    revisions.update(predecessor["revision"] for predecessor in manifest["supported_predecessors"])
    for revision in sorted(revisions):
        try:
            result = subprocess.run(
                ["git", "-C", str(repository_root), "cat-file", "-e", f"{revision}^{{commit}}"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise ValueError("Git is unavailable for migration preflight identity verification") from exc
        if result.returncode:
            raise ValueError(f"migration preflight manifest revision is not a local Git commit: {revision}")


def _preflight_manifest_expected_ids(inventory):
    post_ids = set(inventory["post_transition_migrations"])
    post_transition_ids = sorted(post_ids)
    current_graph = _post_transition_graph(inventory)
    post_transition_leaf_ids = sorted(post_ids - {source for source, _ in current_graph["edges"] if source in post_ids})
    first_party_apps = sorted({migration["id"].split(".", 1)[0] for migration in inventory["migrations"]})
    if inventory.get("layout") == "normalized":
        normalized = inventory["normalized_contract"]
        historical_ids = sorted(normalized["deleted_historical_ids"])
        baseline_ids = sorted(normalized["baseline_ids"])
        return {
            "first_party_apps": first_party_apps,
            "historical_ids": historical_ids,
            "replacement_ids": baseline_ids,
            "replacement_target_ids": historical_ids,
            "baseline_ids": baseline_ids,
            "post_transition_ids": post_transition_ids,
            "post_transition_leaf_ids": post_transition_leaf_ids,
            "current_leaf_ids": current_graph["leaves"],
        }

    historical_ids = sorted(
        migration["id"]
        for migration in inventory["migrations"]
        if not migration["is_replacement"] and migration["id"] not in post_ids
    )
    replacement_migrations = [migration for migration in inventory["migrations"] if migration["is_replacement"]]
    replacement_ids = sorted(migration["id"] for migration in replacement_migrations)
    replacement_target_ids = sorted(target for migration in replacement_migrations for target in migration["replaces"])
    return {
        "first_party_apps": first_party_apps,
        "historical_ids": historical_ids,
        "replacement_ids": replacement_ids,
        "replacement_target_ids": replacement_target_ids,
        "baseline_ids": replacement_ids,
        "post_transition_ids": post_transition_ids,
        "post_transition_leaf_ids": post_transition_leaf_ids,
        "current_leaf_ids": current_graph["leaves"],
    }


def render_preflight_manifest(inventory, manifest):
    """Refresh source-derived manifest fields without changing reviewed identity metadata."""

    rendered = dict(manifest)
    rendered.update(_preflight_manifest_expected_ids(inventory))
    return json.dumps(rendered, indent=2, sort_keys=True) + "\n"


def validate_preflight_manifest(inventory, manifest):
    if manifest.get("schema_version") != 1:
        raise ValueError("migration preflight manifest schema_version must be 1")
    inventory_layout = inventory.get("layout", "transitional")
    if manifest.get("layout") != inventory_layout:
        raise ValueError(f"migration preflight manifest layout must match inventory layout: {inventory_layout}")
    if inventory_layout not in {"transitional", "normalized"}:
        raise ValueError("migration preflight manifest layout is invalid")
    for key in _PREFLIGHT_MANIFEST_LIST_FIELDS:
        _manifest_list(manifest, key)
    for key, expected in _preflight_manifest_expected_ids(inventory).items():
        _assert_manifest_ids(manifest, key, expected)
    _validate_manifest_predecessors(manifest)
    return manifest


def render_inventory(inventory):
    return json.dumps(inventory, indent=2, sort_keys=True) + "\n"


def _validate_normalized_cli_paths(parser, args, repository_root, source_root, output):
    if args.layout == "transitional" and args.normalized_contract is not None:
        parser.error("--normalized-contract requires --layout normalized")
    if args.layout != "normalized":
        return
    if args.source_root is None or args.normalized_contract is None or args.manifest is None or args.output is None:
        parser.error(
            "--layout normalized requires explicit --source-root, --normalized-contract, --manifest, and --output"
        )
    repository_root = Path(repository_root).resolve()
    source_root = Path(source_root).resolve()
    contract_path = args.normalized_contract.resolve()
    manifest_path = args.manifest.resolve()
    output = Path(output).resolve()
    if repository_root == source_root or repository_root in source_root.parents:
        parser.error("normalized fixture source root must be outside the repository")
    if repository_root == output or repository_root in output.parents:
        parser.error("normalized fixture output must be outside the repository")
    if repository_root in contract_path.parents or contract_path == repository_root:
        parser.error("normalized fixture contract must be outside the repository")
    if repository_root in manifest_path.parents or manifest_path == repository_root:
        parser.error("normalized fixture manifest must be outside the repository")
    if output in {contract_path, manifest_path} or source_root in output.parents or output == source_root:
        parser.error("normalized fixture output must not overwrite an input or source root")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the audit is stale")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--manifest", type=Path, help="checked runtime preflight manifest")
    parser.add_argument(
        "--skip-git-identity-check",
        action="store_true",
        help="skip Git-object identity verification for an explicitly isolated fixture run",
    )
    parser.add_argument(
        "--write-preflight-manifest",
        action="store_true",
        help="refresh source-derived fields in the checked runtime preflight manifest",
    )
    parser.add_argument("--layout", choices=("transitional", "normalized"), default="transitional")
    parser.add_argument("--normalized-contract", type=Path, help="explicit future normalized-layout contract")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    repository_root = Path(__file__).resolve().parents[1]
    source_root = args.source_root or repository_root / "itambox"
    canonical_source_root = (repository_root / "itambox").resolve()
    output = args.output or repository_root / "scripts" / "migration_audit.json"
    _validate_normalized_cli_paths(parser, args, repository_root, source_root, output)
    normalized_contract = load_preflight_manifest(args.normalized_contract) if args.normalized_contract else None
    inventory = build_inventory(source_root, layout=args.layout, normalized_contract=normalized_contract)
    manifest_is_requested = source_root.resolve() == canonical_source_root or args.manifest is not None
    if args.write_preflight_manifest and args.check:
        parser.error("--check and --write-preflight-manifest are mutually exclusive")
    if args.write_preflight_manifest and not manifest_is_requested:
        parser.error("--write-preflight-manifest requires the canonical source root or --manifest")
    if manifest_is_requested:
        manifest_path = args.manifest or source_root / "core" / "migration_baseline_manifest.json"
        try:
            manifest = load_preflight_manifest(manifest_path)
            _validate_manifest_predecessors(manifest)
            if args.skip_git_identity_check:
                print("migration preflight manifest Git identity check explicitly skipped", file=sys.stderr)
            else:
                validate_preflight_manifest_git_objects(manifest, repository_root)
            if args.write_preflight_manifest:
                refreshed = json.loads(render_preflight_manifest(inventory, manifest))
                validate_preflight_manifest(inventory, refreshed)
                manifest_path.write_text(json.dumps(refreshed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                print(f"wrote migration preflight manifest: {manifest_path}")
                return 0
            validate_preflight_manifest(inventory, manifest)
        except ValueError as exc:
            print(f"migration preflight manifest invalid: {exc}", file=sys.stderr)
            return 1
    else:
        print("migration preflight manifest validation skipped for a non-canonical source root", file=sys.stderr)
    rendered = render_inventory(inventory)
    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != rendered:
            print(f"migration audit drift: regenerate with {Path(__file__).name}", file=sys.stderr)
            return 1
        print(f"migration audit is current: {output}")
        return 0
    output.write_text(rendered, encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
