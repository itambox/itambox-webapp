#!/usr/bin/env python3
"""Build a deterministic migration inventory without importing Django."""

import argparse
import ast
import json
import sys
from pathlib import Path


SCHEMA_VERSION = 2
OPERATION_TYPES = ("RunPython", "RunSQL", "SeparateDatabaseAndState")
ALLOWED_DISPOSITIONS = {
    "required-fresh",
    "upgrade-only",
    "safely-replaced-by-final-schema",
    "review-blocker",
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
    return {
        migration_id: {"disposition": disposition, "rationale": rationale}
        for migration_id in migration_ids
    }


# This is a checked, human-reviewed policy. It is intentionally independent of
# migration/function names, reversibility syntax, and operation implementation.
SEMANTIC_DISPOSITIONS = {
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

    if operations_node is None:
        return summary

    for call in (node for node in ast.walk(operations_node) if isinstance(node, ast.Call)):
        operation = _call_name(call.func)
        if operation == "SeparateDatabaseAndState":
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


def build_inventory(
    source_root,
    semantic_dispositions=None,
    expected_blockers=None,
):
    source_root = Path(source_root)
    semantic_dispositions = (
        SEMANTIC_DISPOSITIONS
        if semantic_dispositions is None
        else semantic_dispositions
    )
    expected_blockers = EXPECTED_BLOCKERS if expected_blockers is None else set(expected_blockers)
    migrations = []
    for path in sorted(source_root.glob("*/migrations/[0-9]*.py")):
        app = path.parent.parent.name
        migration_id = f"{app}.{path.stem}"
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            migration_class = _migration_class(tree)
        except (SyntaxError, ValueError) as error:
            raise ValueError(f"{path}: {error}") from error
        dependencies, has_swappable_dependency = _dependencies(
            _assignment(migration_class, "dependencies")
        )
        run_before, _ = _dependencies(_assignment(migration_class, "run_before"))
        special_bootstrap = (
            app == "users"
            and "users.0001_initial" in run_before
        )
        operations = _operation_summary(
            _assignment(migration_class, "operations")
        )
        custom_operation_presence = {
            "RunPython": sum(operations["RunPython"].values()) > 0,
            "RunSQL": sum(operations["RunSQL"].values()) > 0,
            "SeparateDatabaseAndState": operations["SeparateDatabaseAndState"]["count"] > 0,
        }
        reviewed_disposition = semantic_dispositions.get(migration_id)
        migrations.append(
            {
                "dependencies": dependencies,
                "id": migration_id,
                "operations": operations,
                "path": path.relative_to(source_root.parent).as_posix(),
                "reviewed_disposition": reviewed_disposition,
                "run_before": run_before,
                "special_users_bootstrap": special_bootstrap,
                "swappable_user_dependency": has_swappable_dependency,
                "syntactic_facts": {
                    "has_custom_operations": custom_operation_presence,
                },
            }
        )

    node_ids = {migration["id"] for migration in migrations}
    user_bootstraps = sorted(
        migration["id"] for migration in migrations if migration["special_users_bootstrap"]
    )
    user_bootstrap = user_bootstraps[0] if len(user_bootstraps) == 1 else None
    edges = {
        (dependency, migration["id"])
        for migration in migrations
        for dependency in migration["dependencies"]
        if dependency in node_ids
    }
    edges.update(
        (migration["id"], target)
        for migration in migrations
        for target in migration["run_before"]
        if target in node_ids
    )
    if user_bootstrap:
        edges.update(
            (user_bootstrap, migration["id"])
            for migration in migrations
            if migration["swappable_user_dependency"]
        )
    edges = sorted(edges)
    targets = {target for _, target in edges}
    sources = {source for source, _ in edges}
    by_id = {migration["id"]: migration for migration in migrations}
    custom_operation_ids = {
        migration["id"]
        for migration in migrations
        if any(migration["syntactic_facts"]["has_custom_operations"].values())
    }
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
    for migration in migrations:
        app = migration["id"].split(".", 1)[0]
        app_ids = {item["id"] for item in migrations if item["id"].startswith(f"{app}.")}
        app_edges = {
            (source, target)
            for source, target in edges
            if source in app_ids and target in app_ids
        }
        app_sources = {source for source, _ in app_edges}
        app_targets = {target for _, target in app_edges}
        per_app[app] = {
            "local_roots": sorted(app_ids - app_targets),
            "local_leaves": sorted(app_ids - app_sources),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "first_party_edges": len(edges),
            "first_party_nodes": len(migrations),
            "custom_operation_file_counts": {
                operation: sum(
                    migration["syntactic_facts"]["has_custom_operations"][operation]
                    for migration in migrations
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
            "run_before": (
                by_id[user_bootstrap]["run_before"] if user_bootstrap else []
            ),
            "swappable_dependents": sorted(
                migration_id
                for migration_id, migration in by_id.items()
                if migration["swappable_user_dependency"]
            ),
        },
        "migrations": migrations,
    }


def render_inventory(inventory):
    return json.dumps(inventory, indent=2, sort_keys=True) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the audit is stale")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    repository_root = Path(__file__).resolve().parents[1]
    source_root = args.source_root or repository_root / "itambox"
    output = args.output or repository_root / "scripts" / "migration_audit.json"
    rendered = render_inventory(build_inventory(source_root))
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
