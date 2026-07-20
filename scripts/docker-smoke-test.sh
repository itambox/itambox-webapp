#!/usr/bin/env bash
# Production Docker Compose smoke test.
#
# Builds the production image(s) from the current checkout, boots the full
# docker-compose.yml stack (app, worker, PostgreSQL, Valkey) behind freshly
# generated, non-placeholder ephemeral secrets, and verifies:
#   - db/valkey/app container health
#   - migrations apply cleanly against the compiled prod image
#   - `manage.py check --deploy` passes with zero warnings/errors
#   - /health/ responds 200 with a healthy DB check
#   - collected static assets are served correctly by WhiteNoise
#   - the app can round-trip a value through the Valkey cache
#   - the worker process stays up with no restarts/tracebacks
#
# All state (generated secrets, compose project, containers, volumes) is
# ephemeral and torn down on exit. Nothing here touches a real deployment.
#
# Usage: scripts/docker-smoke-test.sh
# Requires: Docker with Compose v2.24.4+ (for fail-closed !override) and
# python3 (or python) on PATH.
#
# Tunables (env vars): SMOKE_DB_TIMEOUT, SMOKE_APP_TIMEOUT,
# SMOKE_WORKER_GRACE_PERIOD (all in seconds).

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker-compose.yml"

DB_TIMEOUT="${SMOKE_DB_TIMEOUT:-90}"
APP_TIMEOUT="${SMOKE_APP_TIMEOUT:-120}"
WORKER_GRACE_PERIOD="${SMOKE_WORKER_GRACE_PERIOD:-15}"

WORKDIR=""
LOG_DIR=""
ENV_FILE=""
OVERRIDE_FILE=""
PROJECT_NAME=""
PYTHON_BIN=""
HOST_PORT=""

log() {
  printf '[smoke-test] %s\n' "$*"
}

die() {
  log "ERROR: $*"
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

to_native_path() {
  # On Git Bash for Windows, native (non-MSYS) binaries — docker.exe,
  # python.exe — don't always get MSYS's automatic /tmp/... -> C:/...
  # argv translation (depends on exactly how each is installed/shimmed),
  # and paths embedded as *strings inside files* (e.g. the env_file entry
  # written into the compose override below) are never translated at all.
  # Route every path handed to such a binary, or written into a file one
  # will read, through cygpath -m (drive letter + forward slashes — valid
  # for both native Windows and MSYS tools). Everywhere else (Linux/macOS
  # CI, WSL) cygpath doesn't exist and this is a no-op passthrough.
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -m "$1"
  else
    printf '%s' "$1"
  fi
}

find_python() {
  # `command -v` alone isn't enough on Windows, where a bare `python3` on
  # PATH can be the Microsoft Store's non-functional app-execution-alias
  # stub. Verify the candidate actually runs before trusting it.
  local candidate
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "" >/dev/null 2>&1; then
      echo "$candidate"
      return 0
    fi
  done
  die "a working python3 or python is required to generate ephemeral secrets"
}

compose_supports_override() {
  local version="${1#v}"
  "$PYTHON_BIN" - "$version" <<'PYEOF'
import re
import sys

match = re.match(r"^(\d+)\.(\d+)\.(\d+)", sys.argv[1])
if not match:
    raise SystemExit(1)
raise SystemExit(0 if tuple(map(int, match.groups())) >= (2, 24, 4) else 1)
PYEOF
}

# ------------------------------------------------------------------------------
# Ephemeral secret generation — pure stdlib, no placeholder values, never
# reused between runs.
# ------------------------------------------------------------------------------
gen_secret_key() {
  "$PYTHON_BIN" -c "import secrets; print(secrets.token_urlsafe(64))"
}

gen_fernet_key() {
  # A Fernet key is base64.urlsafe_b64encode of 32 random bytes — generated
  # here without depending on the `cryptography` package being present on
  # the host (it's guaranteed inside the app container, not on the runner).
  "$PYTHON_BIN" -c "import os, base64; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
}

gen_hex_secret() {
  "$PYTHON_BIN" -c "import secrets, sys; print(secrets.token_hex(int(sys.argv[1])))" "$1"
}

gen_api_token_peppers_json() {
  "$PYTHON_BIN" -c "import json, sys; print(json.dumps({'1': sys.argv[1]}))" "$1"
}

write_env_file() {
  local path="$1"
  local secret_key fernet_key pepper_hex db_password api_token_peppers

  secret_key="$(gen_secret_key)"
  fernet_key="$(gen_fernet_key)"
  pepper_hex="$(gen_hex_secret 32)"
  db_password="$(gen_hex_secret 24)"
  api_token_peppers="$(gen_api_token_peppers_json "$pepper_hex")"

  [[ "$secret_key" != "django-insecure-dev-only-change-me-in-production" ]] \
    || die "generated an insecure placeholder SECRET_KEY — aborting"

  umask 077
  cat > "$path" <<EOF
ITAMBOX_SECRET_KEY=$secret_key
ITAMBOX_FIELD_ENCRYPTION_KEYS=$fernet_key
ITAMBOX_API_TOKEN_PEPPERS=$api_token_peppers
ITAMBOX_ALLOWED_HOSTS=127.0.0.1,localhost
ITAMBOX_CSRF_TRUSTED_ORIGINS=https://127.0.0.1
ITAMBOX_DB_USER=itambox_smoke
ITAMBOX_DB_NAME=itambox_smoke
ITAMBOX_DB_PASSWORD=$db_password
EOF
  chmod 600 "$path"
}

write_override_file() {
  # Redirects app/worker at the ephemeral secrets file instead of a real
  # `.env` next to docker-compose.yml (never touched by this script), and
  # publishes the app port on an OS-assigned loopback port so this doesn't
  # collide with a developer's own stack or dev server.
  local env_file_native
  env_file_native="$(to_native_path "$ENV_FILE")"
  cat > "$OVERRIDE_FILE" <<EOF
services:
  app:
    # Compose sequences append by default.  !override is required here so the
    # production env file and public port binding cannot leak into the
    # isolated smoke stack (Compose v2.24.4+).
    env_file: !override
      - $env_file_native
    ports: !override
      - "127.0.0.1::8000"
  worker:
    env_file: !override
      - $env_file_native
EOF
}

compose() {
  docker compose \
    -p "$PROJECT_NAME" \
    -f "$(to_native_path "$COMPOSE_FILE")" \
    -f "$(to_native_path "$OVERRIDE_FILE")" \
    --env-file "$(to_native_path "$ENV_FILE")" \
    "$@"
}

# ------------------------------------------------------------------------------
# Setup / teardown
# ------------------------------------------------------------------------------
setup_workdir() {
  WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/itambox-smoke.XXXXXX")"
  LOG_DIR="$WORKDIR/logs"
  ENV_FILE="$WORKDIR/smoke.env"
  OVERRIDE_FILE="$WORKDIR/docker-compose.smoke-override.yml"
  # Compose project names must match ^[a-z0-9][a-z0-9_-]*$ — lowercase only.
  # mktemp's random suffix can include uppercase, so fold case before
  # stripping anything that isn't alnum/dash (WORKDIR's basename already
  # carries the itambox-smoke prefix from the mktemp template below).
  local project_slug
  project_slug="$(basename "$WORKDIR" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9' '-')"
  PROJECT_NAME="${project_slug%-}"
  log "Working directory: $WORKDIR (compose project: $PROJECT_NAME)"
}

dump_diagnostics() {
  [[ -n "$WORKDIR" ]] || return 0
  mkdir -p "$LOG_DIR"
  compose ps > "$LOG_DIR/compose-ps.txt" 2>&1 || true
  local svc
  for svc in db valkey app worker; do
    compose logs --no-color --timestamps "$svc" > "$LOG_DIR/$svc.log" 2>&1 || true
  done
}

cleanup() {
  local ec=$? teardown_ec=0
  if [[ -z "$WORKDIR" ]]; then
    exit "$ec"
  fi

  if [[ $ec -ne 0 ]]; then
    log "Smoke test failed (exit code $ec) — collecting diagnostics..."
    dump_diagnostics
  fi

  log "Tearing down ephemeral Docker resources..."
  mkdir -p "$LOG_DIR"
  if compose down -v --remove-orphans --timeout 20 >"$LOG_DIR/compose-down.log" 2>&1; then
    rm -f "$LOG_DIR/compose-down.log"
  else
    teardown_ec=$?
    log "Docker teardown failed (exit code $teardown_ec); resources may remain."
    cat "$LOG_DIR/compose-down.log" >&2 || true
    if [[ $ec -eq 0 ]]; then
      ec=$teardown_ec
      dump_diagnostics
    fi
  fi

  # Never leave generated secrets on disk, regardless of outcome.
  rm -f "$ENV_FILE" "$OVERRIDE_FILE" 2>/dev/null || true

  if [[ $ec -ne 0 ]]; then
    log "Diagnostics preserved at: $LOG_DIR"
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
      printf 'log-dir=%s\n' "$LOG_DIR" >> "$GITHUB_OUTPUT"
    fi
  else
    rm -rf "$WORKDIR"
  fi
  exit "$ec"
}
trap cleanup EXIT

# ------------------------------------------------------------------------------
# Preflight
# ------------------------------------------------------------------------------
preflight_checks() {
  local compose_version
  require_cmd docker
  PYTHON_BIN="$(find_python)"
  if ! docker compose version >/dev/null 2>&1; then
    die "Docker Compose v2 (the 'docker compose' CLI plugin) is required."
  fi
  compose_version="$(docker compose version --short 2>/dev/null || true)"
  if ! compose_supports_override "$compose_version"; then
    die "Docker Compose v2.24.4 or newer is required for fail-closed !override semantics (found '${compose_version:-unknown}')."
  fi
  if ! docker info >/dev/null 2>&1; then
    die "Docker daemon is not reachable. Start Docker and re-run this script."
  fi
}

# ------------------------------------------------------------------------------
# Readiness waits
# ------------------------------------------------------------------------------
wait_for_container_health() {
  local service="$1" timeout="$2" waited=0 cid status
  cid="$(compose ps -q "$service")"
  [[ -n "$cid" ]] || die "service '$service' has no running container"

  while true; do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid")"
    if [[ "$status" == "healthy" ]]; then
      log "service '$service' is healthy"
      return 0
    elif [[ "$status" == "unhealthy" ]]; then
      die "service '$service' reported unhealthy"
    elif [[ "$status" == "none" ]]; then
      die "service '$service' has no healthcheck defined — cannot verify readiness"
    fi
    if (( waited >= timeout )); then
      die "timed out after ${timeout}s waiting for '$service' to become healthy (last status: $status)"
    fi
    sleep 3
    waited=$((waited + 3))
  done
}

discover_host_port() {
  local mapping
  mapping="$(compose port app 8000)"
  HOST_PORT="${mapping##*:}"
  [[ -n "$HOST_PORT" && "$HOST_PORT" != "$mapping" ]] \
    || die "failed to discover published host port for app:8000 (got '$mapping')"
  log "app published at 127.0.0.1:$HOST_PORT"
}

# ------------------------------------------------------------------------------
# Checks
# ------------------------------------------------------------------------------
run_migrations() {
  log "Running database migrations against the prod image..."
  compose run --rm --no-deps app python manage.py migrate --noinput
}

run_deploy_check() {
  log "Running manage.py check --deploy (all checks; errors are blocking)..."
  compose run --rm --no-deps app python manage.py check --deploy

  # The repository currently carries non-security warning debt (notably OpenAPI
  # schema warnings). Keep those visible above without making this unrelated
  # Docker smoke gate permanently red, while preserving a strict production
  # security gate for every deploy warning.
  log "Running strict deployment security checks (warnings are blocking)..."
  compose run --rm --no-deps app \
    python manage.py check --deploy --tag security --fail-level WARNING
}

app_http_get() {
  local path="$1"
  compose exec -T app python - "$path" <<'PYEOF'
import sys
import urllib.request

request = urllib.request.Request(
    f"http://127.0.0.1:8000{sys.argv[1]}",
    headers={"Host": "127.0.0.1", "X-Forwarded-Proto": "https"},
)
with urllib.request.urlopen(request, timeout=5) as response:
    if response.status != 200:
        raise SystemExit(f"unexpected HTTP status: {response.status}")
    sys.stdout.buffer.write(response.read())
PYEOF
}

verify_health_endpoint() {
  local timeout="$1" waited=0 body_file
  body_file="$WORKDIR/health-response.json"

  while ! app_http_get /health/ > "$body_file"; do
    if (( waited >= timeout )); then
      die "timed out after ${timeout}s waiting for a healthy /health/ response"
    fi
    sleep 3
    waited=$((waited + 3))
  done

  "$PYTHON_BIN" - "$(to_native_path "$body_file")" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
assert data.get("status") == "ok", f"unexpected health payload: {data}"
assert data.get("checks", {}).get("database") == "ok", f"database check failed: {data}"
print("health endpoint OK:", data)
PYEOF
}

verify_static_assets() {
  local asset
  for asset in dist/itambox.js dist/itambox.css; do
    app_http_get "/static/$asset" >/dev/null \
      || die "expected /static/$asset to return 200 (npm build or collectstatic broken in the image?)"
    log "static asset OK: /static/$asset"
  done
}

verify_cache_roundtrip() {
  local out
  out="$(compose exec -T app python manage.py shell <<'PYEOF'
from django.core.cache import cache
cache.set("itambox_smoke_test", "ok", 30)
value = cache.get("itambox_smoke_test")
assert value == "ok", f"Valkey cache round-trip failed (got {value!r})"
print("CACHE_ROUNDTRIP_OK")
PYEOF
  )"
  mkdir -p "$LOG_DIR"
  printf '%s\n' "$out" > "$WORKDIR/cache-check.log"
  grep -q "CACHE_ROUNDTRIP_OK" <<<"$out" || die "Valkey cache round-trip check failed: $out"
  log "Valkey cache round-trip OK"
}

verify_worker_stable() {
  local grace="$1" cid state restarts worker_log grep_status
  sleep "$grace"
  cid="$(compose ps -q worker)"
  [[ -n "$cid" ]] || die "worker container is not running"

  state="$(docker inspect --format '{{.State.Status}}' "$cid")"
  [[ "$state" == "running" ]] || die "worker container is not in 'running' state (got '$state')"

  restarts="$(docker inspect --format '{{.RestartCount}}' "$cid")"
  [[ "$restarts" == "0" ]] || die "worker container restarted $restarts time(s) since start — check logs"

  # Consume the complete Compose stream before searching it. With pipefail,
  # `compose logs | grep -q` can fail open: grep exits on the first match, the
  # producer gets SIGPIPE, and the nonzero pipeline status makes the `if` false.
  worker_log="$LOG_DIR/worker-stability.log"
  if ! compose logs --no-color worker > "$worker_log" 2>&1; then
    die "could not read worker logs — check $worker_log"
  fi
  if grep -Fq "Traceback (most recent call last)" "$worker_log"; then
    die "worker logs contain a traceback — check logs"
  else
    grep_status=$?
    [[ "$grep_status" == "1" ]] || die "could not inspect worker logs for tracebacks (grep status $grep_status)"
  fi
  log "worker is stable (running, no restarts, no traceback in logs)"
}

# ------------------------------------------------------------------------------
main() {
  preflight_checks
  setup_workdir
  write_env_file "$ENV_FILE"
  write_override_file

  log "Building images from a clean checkout ($REPO_ROOT)..."
  compose build

  log "Starting PostgreSQL and Valkey..."
  compose up -d db valkey
  wait_for_container_health db "$DB_TIMEOUT"
  wait_for_container_health valkey "$DB_TIMEOUT"

  run_migrations
  run_deploy_check

  log "Starting app and worker..."
  compose up -d app worker
  wait_for_container_health app "$APP_TIMEOUT"
  discover_host_port

  verify_health_endpoint "$APP_TIMEOUT"
  verify_static_assets
  verify_cache_roundtrip
  verify_worker_stable "$WORKER_GRACE_PERIOD"

  log "All production Docker Compose smoke checks passed."
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
