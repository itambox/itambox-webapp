"""Compare fresh-install and upgraded schema evidence.

Structural sections (columns, constraints, indexes, extensions) must match
exactly. Content types and permissions must be a superset on the upgrade side:
Django never removes content-type rows, so a database that ran the predecessor
release keeps rows for models that later migrations deleted (for example
subscriptions.provider, removed by subscriptions.0103). Every upgrade-only
content type must therefore have no backing table in the fresh capture; anything
else is a hard failure.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def canon(rows: list) -> list:
    return sorted(json.dumps(row, sort_keys=True) for row in rows)


def main(fresh_path: str, upgrade_path: str, label: str) -> int:
    fresh = load(Path(fresh_path))
    upgrade = load(Path(upgrade_path))
    failures: list[str] = []

    for key in ("columns", "constraints", "indexes", "extensions"):
        a, b = canon(fresh[key]), canon(upgrade[key])
        if a != b:
            only_fresh = [x for x in a if x not in set(b)][:5]
            only_upg = [x for x in b if x not in set(a)][:5]
            failures.append(
                f"{key}: fresh={len(a)} upgrade={len(b)}; only-in-fresh={only_fresh}; only-in-upgrade={only_upg}"
            )
        else:
            print(f"{label} {key}: identical ({len(a)} entries)")

    fresh_ct = {tuple(row) for row in fresh["content_types"]}
    upg_ct = {tuple(row) for row in upgrade["content_types"]}
    if not fresh_ct <= upg_ct:
        failures.append(f"content_types missing on upgrade side: {sorted(fresh_ct - upg_ct)[:5]}")
    tables = {row[0] for row in fresh["columns"]}
    for app_label, model in sorted(upg_ct - fresh_ct):
        expected_table = f"{app_label}_{model}"
        if expected_table in tables:
            failures.append(f"upgrade-only content type {app_label}.{model} still has table {expected_table}")
        else:
            print(f"{label} content_types: allowed upgrade-only {app_label}.{model} (model removed, no table)")

    fresh_perm = {tuple(row) for row in fresh["permissions"]}
    upg_perm = {tuple(row) for row in upgrade["permissions"]}
    if not fresh_perm <= upg_perm:
        failures.append(f"permissions missing on upgrade side: {sorted(fresh_perm - upg_perm)[:5]}")
    stale_ct_models = upg_ct - fresh_ct
    unexplained = {p for p in upg_perm - fresh_perm if (p[0], p[1]) not in stale_ct_models}
    if unexplained:
        failures.append(f"unexplained upgrade-only permissions: {sorted(unexplained)[:5]}")
    print(
        f"{label} permissions: fresh={len(fresh_perm)} upgrade={len(upg_perm)} "
        f"(upgrade-only explained by removed models: {len(upg_perm - fresh_perm)})"
    )

    if failures:
        print(f"{label} SCHEMA_EVIDENCE_MISMATCH")
        for failure in failures:
            print("  FAIL:", failure)
        return 1
    print(f"{label} SCHEMA_EVIDENCE_MATCH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
