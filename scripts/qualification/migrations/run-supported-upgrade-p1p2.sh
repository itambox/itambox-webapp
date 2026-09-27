#!/usr/bin/env bash
# Supported-upgrade qualification for the normalized #479 migration graph.
#
# For each supported predecessor declared in the checked baseline manifest:
#
#   1. construct the predecessor database from the exact revision in a clean
#      git worktree;
#   2. load representative pre-#479 data through the predecessor models;
#   3. upgrade with the candidate (the checkout containing this script);
#   4. verify preflight recognition, data integrity, composition, constraints,
#      tenant isolation, application-level writes, and the demo seed;
#   5. compare schema evidence and the migration recorder against a fresh
#      install of the candidate.
#
# The run writes all logs and evidence below the evidence directory, removes its
# databases and worktrees afterwards, and prints SUPPORTED_UPGRADE_PASS with
# exit code 0 only when both predecessor paths pass end to end.
#
# Environment:
#   ITAMBOX_QUALIFICATION_EVIDENCE   evidence root (default: ${TMPDIR:-/tmp}/itambox-upgrade-qualification)
#   ITAMBOX_QUALIFICATION_REPO       candidate checkout (default: this repository)
#   ITAMBOX_QUALIFICATION_WORKTREES  worktree root (default: a fresh temp directory)
#   ITAMBOX_QUALIFICATION_P1_SHA     pre-squash predecessor (default: the checked revision)
#   ITAMBOX_QUALIFICATION_P2_SHA     transition-release predecessor (default: the checked revision)
#   ITAMBOX_QUALIFICATION_PSQL       psql command (default: psql; for a container-hosted
#   ITAMBOX_QUALIFICATION_CREATEDB   createdb command   PostgreSQL use e.g.
#   ITAMBOX_QUALIFICATION_DROPDB     dropdb command     "docker exec -i itambox-dev-pg psql -U itambox")
#   ITAMBOX_DB_* / PG*               inherited by the application and the client tools
set -Eeuo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="${ITAMBOX_QUALIFICATION_REPO:-$(git -C "$here" rev-parse --show-toplevel)}"
helpers="$here/upgrade_qualification"
manifest="$repo/itambox/core/migration_baseline_manifest.json"

p1_sha="${ITAMBOX_QUALIFICATION_P1_SHA:-deef4c8bf3fe678edecacf2c523d7bd0dcb6f6ef}"
p2_sha="${ITAMBOX_QUALIFICATION_P2_SHA:-2246573fcbfc238e3878cc2eb623476da28b20dc}"

run_id="$(date -u +%Y%m%d%H%M%S)"
evidence="${ITAMBOX_QUALIFICATION_EVIDENCE:-${TMPDIR:-/tmp}/itambox-upgrade-qualification}/upgrade-$run_id"
logs="$evidence/logs"
mkdir -p "$logs"

psql_cmd=(${ITAMBOX_QUALIFICATION_PSQL:-psql})
createdb_cmd=(${ITAMBOX_QUALIFICATION_CREATEDB:-createdb})
dropdb_cmd=(${ITAMBOX_QUALIFICATION_DROPDB:-dropdb})

worktree_root="${ITAMBOX_QUALIFICATION_WORKTREES:-$(mktemp -d "${TMPDIR:-/tmp}/itambox-upgrade-worktrees-XXXXXX")}"
p1_wt="$worktree_root/p1"
p2_wt="$worktree_root/p2"
p1_db="itambox_upgrade_p1_$run_id"
p2_db="itambox_upgrade_p2_$run_id"
fresh_db="itambox_upgrade_fresh_$run_id"

psql_rows() { "${psql_cmd[@]}" -X -t -A "$@"; }
createdb_() { "${createdb_cmd[@]}" "$1"; }
dropdb_() { "${dropdb_cmd[@]}" --if-exists "$1" >/dev/null 2>&1 || true; }

cleanup() {
  rc=$?
  dropdb_ "$p1_db"
  dropdb_ "$p2_db"
  dropdb_ "$fresh_db"
  git -C "$repo" worktree remove --force "$p1_wt" >/dev/null 2>&1 || true
  git -C "$repo" worktree remove --force "$p2_wt" >/dev/null 2>&1 || true
  printf 'run_id=%s\nexit_code=%s\n' "$run_id" "$rc" > "$evidence/cleanup.txt"
  exit "$rc"
}
trap cleanup EXIT

run_manage() {  # kind worktree database label manage-args...
  local kind=$1 wt=$2 db=$3 label=$4
  shift 4
  local group_flag=()
  if [ "$kind" = candidate ]; then
    group_flag=(--group dev)
  fi
  set +e
  (cd "$wt/itambox" && ITAMBOX_ENV=dev ITAMBOX_DB_NAME="$db" \
    uv run --locked "${group_flag[@]}" python manage.py "$@") \
    > "$logs/$label.stdout" 2> "$logs/$label.stderr"
  local rc=$?
  set -e
  printf '%s' "$rc" > "$logs/$label.rc"
  printf '%-46s rc=%s\n' "$label" "$rc"
}

assert_rc() {  # label expected
  local rc
  rc="$(cat "$logs/$1.rc")"
  if [ "$rc" != "$2" ]; then
    echo "FAIL: $1 expected rc=$2 got rc=$rc" >&2
    tail -n 20 "$logs/$1.stderr" >&2 || true
    exit 1
  fi
}

assert_preflight() {  # label expected-state expected-exit
  python3 - "$logs/$1.stdout" "$2" "$3" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1]))
assert payload["state"] == sys.argv[2], payload["state"]
assert payload["exit_code"] == int(sys.argv[3]), payload["exit_code"]
print(f"  preflight {sys.argv[2]} exit={payload['exit_code']} reason={payload['reason_code']}")
PY
}

capture_recorder() {  # database file
  psql_rows -d "$1" -c "SELECT app || '.' || name FROM django_migrations ORDER BY 1" > "$2"
}

upgrade_one() {  # key sha worktree database predecessor-state
  local key=$1 sha=$2 wt=$3 db=$4 pre_state=$5

  echo "-- $key: constructing predecessor $sha"
  git -C "$repo" worktree add --detach "$wt" "$sha" >/dev/null
  createdb_ "$db"

  run_manage predecessor "$wt" "$db" "$key-predecessor-migrate" migrate --noinput
  assert_rc "$key-predecessor-migrate" 0
  run_manage predecessor "$wt" "$db" "$key-inject" shell -c "exec(open('$helpers/inject_legacy.py').read())"
  assert_rc "$key-inject" 0

  run_manage candidate "$repo" "$db" "$key-preflight-before" migration_baseline_preflight --format=json
  assert_rc "$key-preflight-before" 0
  assert_preflight "$key-preflight-before" "$pre_state" 0

  run_manage candidate "$repo" "$db" "$key-upgrade" migrate --noinput
  assert_rc "$key-upgrade" 0
  run_manage candidate "$repo" "$db" "$key-preflight-after" migration_baseline_preflight --format=json
  assert_rc "$key-preflight-after" 0
  assert_preflight "$key-preflight-after" current-normalized-baseline 0

  run_manage candidate "$repo" "$db" "$key-second-migrate" migrate --noinput
  assert_rc "$key-second-migrate" 0
  run_manage candidate "$repo" "$db" "$key-verify" shell -c "exec(open('$helpers/verify_upgrade.py').read())"
  assert_rc "$key-verify" 0
  run_manage candidate "$repo" "$db" "$key-seed" seed_data --noinput
  assert_rc "$key-seed" 0

  run_manage candidate "$repo" "$db" "$key-schema" capture_schema_evidence
  assert_rc "$key-schema" 0
  python3 "$helpers/compare_schema_evidence.py" "$logs/fresh-schema.stdout" "$logs/$key-schema.stdout" "$key"

  capture_recorder "$db" "$evidence/$key-recorder.txt"
  if ! diff -u "$evidence/fresh-recorder.txt" "$evidence/$key-recorder.txt" > "$logs/$key-recorder.diff"; then
    echo "FAIL: $key migration recorder differs from the fresh install" >&2
    exit 1
  fi
  echo "  $key migration recorder identical to the fresh install"
}

echo "== prerequisites =="
for tool in git uv python3; do
  command -v "$tool" >/dev/null || { echo "$tool is required" >&2; exit 2; }
done
for sha in "$p1_sha" "$p2_sha"; do
  git -C "$repo" cat-file -e "$sha^{commit}" 2>/dev/null || {
    echo "predecessor revision $sha is not present in $repo; fetch the full history first" >&2
    exit 2
  }
done
git -C "$repo" rev-parse HEAD > "$evidence/candidate-revision.txt"
git -C "$repo" status --porcelain > "$evidence/candidate-status.txt"

echo "== fresh install reference =="
createdb_ "$fresh_db"
run_manage candidate "$repo" "$fresh_db" fresh-migrate migrate --noinput
assert_rc fresh-migrate 0
run_manage candidate "$repo" "$fresh_db" fresh-preflight migration_baseline_preflight --format=json
assert_rc fresh-preflight 0
assert_preflight fresh-preflight current-normalized-baseline 0
run_manage candidate "$repo" "$fresh_db" fresh-schema capture_schema_evidence
assert_rc fresh-schema 0
capture_recorder "$fresh_db" "$evidence/fresh-recorder.txt"

echo
echo "== P1: pre-squash predecessor =="
upgrade_one p1 "$p1_sha" "$p1_wt" "$p1_db" supported-predecessor-pre-squash

echo
echo "== P2: transition-release predecessor =="
upgrade_one p2 "$p2_sha" "$p2_wt" "$p2_db" supported-predecessor-transition-release

echo
for key in fresh p1 p2; do
  python3 - "$manifest" "$evidence/$key-recorder.txt" <<'PY'
import json
import sys

manifest = json.load(open(sys.argv[1]))
recorded = {line.strip() for line in open(sys.argv[2]) if line.strip()}
first_party_apps = set(manifest["first_party_apps"])
first_party = {row for row in recorded if row.split(".", 1)[0] in first_party_apps}
print(
    f"{sys.argv[2].rsplit('/', 1)[-1]}: first_party={len(first_party)} "
    f"shards={len(first_party & set(manifest['replacement_ids']))}"
)
PY
done

python3 - "$evidence/result.json" "$run_id" "$p1_sha" "$p2_sha" <<'PY'
import json
import sys

json.dump(
    {
        "run_id": sys.argv[2],
        "p1": sys.argv[3],
        "p2": sys.argv[4],
        "result": "SUPPORTED_UPGRADE_PASS",
    },
    open(sys.argv[1], "w"),
    indent=2,
    sort_keys=True,
)
PY

echo
echo "SUPPORTED_UPGRADE_PASS evidence=$evidence"
