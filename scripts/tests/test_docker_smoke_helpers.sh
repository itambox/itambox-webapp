#!/usr/bin/env bash
# Unit tests for the ephemeral-secret helper functions in
# scripts/docker-smoke-test.sh. Exercises pure bash/python logic only — no
# Docker required — so this stays runnable wherever Docker is not available.
#
# Usage: scripts/tests/test_docker_smoke_helpers.sh

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Sourcing (rather than executing) the smoke-test script loads its functions
# without running main() — see the BASH_SOURCE guard at the bottom of that file.
# shellcheck source=../docker-smoke-test.sh
source "$REPO_ROOT/scripts/docker-smoke-test.sh"

PYTHON_BIN="$(find_python)"

FAILURES=0

assert() {
  local description="$1" condition="$2"
  if [[ "$condition" == "0" ]]; then
    printf '  ok   - %s\n' "$description"
  else
    printf '  FAIL - %s\n' "$description"
    FAILURES=$((FAILURES + 1))
  fi
}

echo "== compose_supports_override =="
assert "minimum supported Compose version is accepted" "$(compose_supports_override '2.24.4'; echo $?)"
assert "newer Compose version is accepted" "$(compose_supports_override 'v2.40.1-desktop.1'; echo $?)"
if compose_supports_override '2.24.3'; then old_version_status=1; else old_version_status=0; fi
assert "older Compose version is rejected" "$old_version_status"
if compose_supports_override 'not-a-version'; then invalid_version_status=1; else invalid_version_status=0; fi
assert "unparseable Compose version is rejected" "$invalid_version_status"

echo "== gen_secret_key =="
key1="$(gen_secret_key)"
key2="$(gen_secret_key)"
assert "secret key is at least 50 characters" "$([[ ${#key1} -ge 50 ]]; echo $?)"
assert "secret key is not the insecure dev placeholder" "$([[ "$key1" != "django-insecure-dev-only-change-me-in-production" ]]; echo $?)"
assert "two generated secret keys differ" "$([[ "$key1" != "$key2" ]]; echo $?)"

echo "== gen_fernet_key =="
fkey="$(gen_fernet_key)"
assert "fernet key decodes to exactly 32 bytes" "$("$PYTHON_BIN" -c "
import base64, sys
raw = base64.urlsafe_b64decode(sys.argv[1])
sys.exit(0 if len(raw) == 32 else 1)
" "$fkey"; echo $?)"

echo "== gen_hex_secret =="
hex1="$(gen_hex_secret 32)"
hex2="$(gen_hex_secret 32)"
assert "hex secret has expected length (64 hex chars for 32 bytes)" "$([[ ${#hex1} -eq 64 ]]; echo $?)"
assert "hex secret is lowercase hexadecimal" "$([[ "$hex1" =~ ^[0-9a-f]{64}$ ]]; echo $?)"
assert "two generated hex secrets differ" "$([[ "$hex1" != "$hex2" ]]; echo $?)"

echo "== gen_api_token_peppers_json =="
peppers_json="$(gen_api_token_peppers_json "$hex1")"
assert "peppers value is valid JSON matching {\"1\": <secret>}" "$("$PYTHON_BIN" -c "
import json, sys
data = json.loads(sys.argv[1])
sys.exit(0 if data == {'1': sys.argv[2]} else 1)
" "$peppers_json" "$hex1"; echo $?)"

echo "== write_env_file =="
tmp_env="$(mktemp "${TMPDIR:-/tmp}/itambox-smoke-test.XXXXXX")"
write_env_file "$tmp_env"

assert "env file contains ITAMBOX_SECRET_KEY" "$(grep -q '^ITAMBOX_SECRET_KEY=' "$tmp_env"; echo $?)"
assert "env file contains ITAMBOX_FIELD_ENCRYPTION_KEYS" "$(grep -q '^ITAMBOX_FIELD_ENCRYPTION_KEYS=' "$tmp_env"; echo $?)"
assert "env file contains ITAMBOX_API_TOKEN_PEPPERS" "$(grep -q '^ITAMBOX_API_TOKEN_PEPPERS=' "$tmp_env"; echo $?)"
assert "env file contains ITAMBOX_DB_PASSWORD" "$(grep -q '^ITAMBOX_DB_PASSWORD=' "$tmp_env"; echo $?)"
assert "env file does not contain the insecure dev SECRET_KEY placeholder" "$(! grep -q 'django-insecure-dev-only-change-me-in-production' "$tmp_env"; echo $?)"
assert "env file does not use the docker-compose.yml default DB password" "$(! grep -q '^ITAMBOX_DB_PASSWORD=itambox$' "$tmp_env"; echo $?)"

perm="$(stat -c '%a' "$tmp_env" 2>/dev/null || stat -f '%Lp' "$tmp_env" 2>/dev/null || echo unknown)"
assert "env file permissions are restricted to the owner (600), got '$perm'" "$([[ "$perm" == "600" || "$perm" == "unknown" ]]; echo $?)"

assert "every value in the env file parses under Django's own .env loader" "$("$PYTHON_BIN" -c "
import sys
parsed = {}
with open(sys.argv[1], encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        parsed[k.strip()] = v.strip()
required = {
    'ITAMBOX_SECRET_KEY', 'ITAMBOX_FIELD_ENCRYPTION_KEYS', 'ITAMBOX_API_TOKEN_PEPPERS',
    'ITAMBOX_ALLOWED_HOSTS', 'ITAMBOX_DB_USER', 'ITAMBOX_DB_NAME', 'ITAMBOX_DB_PASSWORD',
}
missing = required - parsed.keys()
sys.exit(0 if not missing else 1)
" "$(to_native_path "$tmp_env")"; echo $?)"

echo "== write_override_file =="
tmp_override="$(mktemp "${TMPDIR:-/tmp}/itambox-smoke-override.XXXXXX.yml")"
ENV_FILE="$tmp_env"
OVERRIDE_FILE="$tmp_override"
write_override_file
assert "app and worker env_file values replace rather than append to production .env" "$([[ $(grep -c 'env_file: !override' "$tmp_override") -eq 2 ]]; echo $?)"
assert "smoke port replaces rather than appends to public production binding" "$(grep -q 'ports: !override' "$tmp_override"; echo $?)"
assert "smoke port is loopback-only with an OS-assigned host port" "$(grep -q '127.0.0.1::8000' "$tmp_override"; echo $?)"
assert "override does not retain the public production 8000:8000 binding" "$(! grep -qE '^[[:space:]]*-[[:space:]]*[\"'\'']?8000:8000' "$tmp_override"; echo $?)"

echo "== static asset build paths =="
tmp_static_urls="$(mktemp "${TMPDIR:-/tmp}/itambox-smoke-static.XXXXXX")"
curl() {
  printf '%s\n' "${@: -1}" >> "$tmp_static_urls"
  printf '%s' 200
}
HOST_PORT=49152
verify_static_assets
unset -f curl
assert "JavaScript smoke check targets the built dist path" "$(grep -Fxq 'http://127.0.0.1:49152/static/dist/itambox.js' "$tmp_static_urls"; echo $?)"
assert "CSS smoke check targets the built dist path" "$(grep -Fxq 'http://127.0.0.1:49152/static/dist/itambox.css' "$tmp_static_urls"; echo $?)"

echo "== deployment check warning policy =="
if grep -Fq 'python manage.py check --deploy --fail-level WARNING' "$REPO_ROOT/scripts/docker-smoke-test.sh"; then
  deploy_all_warning_status=1
else
  deploy_all_warning_status=0
fi
if grep -Fq 'python manage.py check --deploy --tag security --fail-level WARNING' "$REPO_ROOT/scripts/docker-smoke-test.sh"; then
  deploy_security_status=0
else
  deploy_security_status=1
fi
assert "non-security warning debt does not make the Docker smoke gate permanently red" "$deploy_all_warning_status"
assert "deployment security warnings remain blocking" "$deploy_security_status"

echo "== worker traceback detection consumes large logs =="
tmp_worker="$(mktemp -d "${TMPDIR:-/tmp}/itambox-smoke-worker-test.XXXXXX")"
set +e
(
  trap - EXIT
  LOG_DIR="$tmp_worker"
  compose() {
    if [[ "$1" == "ps" ]]; then
      printf '%s\n' worker-container-id
      return 0
    fi
    if [[ "$1" == "logs" ]]; then
      printf '%s\n' 'Traceback (most recent call last):'
      # Keep writing far beyond a typical pipe buffer. The old `grep -q`
      # pipeline closed early and made this producer fail with SIGPIPE.
      for ((i = 0; i < 20000; i++)); do
        printf 'worker log padding line %05d abcdefghijklmnopqrstuvwxyz\n' "$i"
      done
      return 0
    fi
    return 1
  }
  docker() {
    case "$3" in
      *State.Status*) printf '%s\n' running ;;
      *RestartCount*) printf '%s\n' 0 ;;
      *) return 1 ;;
    esac
  }
  verify_worker_stable 0
)
worker_traceback_status=$?
set -e
assert "large worker logs containing a traceback fail the smoke check" "$([[ $worker_traceback_status -ne 0 ]]; echo $?)"
assert "complete worker logs are retained as diagnostics" "$(grep -Fq 'worker log padding line 19999' "$tmp_worker/worker-stability.log"; echo $?)"

echo "== worker traceback scan errors fail closed =="
tmp_worker_grep="$(mktemp -d "${TMPDIR:-/tmp}/itambox-smoke-worker-grep-test.XXXXXX")"
set +e
(
  trap - EXIT
  LOG_DIR="$tmp_worker_grep"
  compose() {
    [[ "$1" == "ps" ]] && printf '%s\n' worker-container-id && return 0
    [[ "$1" == "logs" ]] && printf '%s\n' 'worker started normally' && return 0
    return 1
  }
  docker() {
    case "$3" in
      *State.Status*) printf '%s\n' running ;;
      *RestartCount*) printf '%s\n' 0 ;;
      *) return 1 ;;
    esac
  }
  grep() { return 2; }
  verify_worker_stable 0
)
worker_grep_status=$?
set -e
assert "a traceback scan read error fails the smoke check" "$([[ $worker_grep_status -ne 0 ]]; echo $?)"

echo "== cleanup fail-closed behavior =="
tmp_cleanup="$(mktemp -d "${TMPDIR:-/tmp}/itambox-smoke-cleanup-test.XXXXXX")"
set +e
(
  trap - EXIT
  WORKDIR="$tmp_cleanup"
  LOG_DIR="$tmp_cleanup/logs"
  ENV_FILE="$tmp_cleanup/smoke.env"
  OVERRIDE_FILE="$tmp_cleanup/override.yml"
  mkdir -p "$LOG_DIR"
  : > "$ENV_FILE"
  : > "$OVERRIDE_FILE"
  compose() { return 23; }
  true
  cleanup
)
cleanup_status=$?
set -e
assert "successful checks become nonzero when Docker teardown fails" "$([[ $cleanup_status -eq 23 ]]; echo $?)"
assert "generated env is removed even when teardown fails" "$([[ ! -e "$tmp_cleanup/smoke.env" ]]; echo $?)"
assert "generated override is removed even when teardown fails" "$([[ ! -e "$tmp_cleanup/override.yml" ]]; echo $?)"
assert "teardown diagnostics are preserved on failure" "$([[ -f "$tmp_cleanup/logs/compose-down.log" ]]; echo $?)"

rm -rf "$tmp_cleanup"
rm -rf "$tmp_worker"
rm -rf "$tmp_worker_grep"
rm -f "$tmp_env" "$tmp_override" "$tmp_static_urls"

echo
if [[ "$FAILURES" -eq 0 ]]; then
  echo "All docker-smoke-test.sh helper checks passed."
  exit 0
else
  echo "$FAILURES helper check(s) failed."
  exit 1
fi
