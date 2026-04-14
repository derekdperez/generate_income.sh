#!/usr/bin/env bash
# Create/configure Postgres for the Nightmare coordinator (Docker Compose stack).
#
# Schema (coordinator_*) is created automatically the first time server.py connects
# (CoordinatorStore._ensure_schema in server.py).
#
# Usage:
#   ./deploy/setup-postgres.sh                 # start postgres service, wait, print connection summary
#   ./deploy/setup-postgres.sh --init-env      # create deploy/.env from .env.example with generated secrets
#   ./deploy/setup-postgres.sh --write-config  # merge DATABASE_URL into ../config/server.json (host runs server outside Docker)
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_DIR="${ROOT_DIR}/deploy"
ENV_FILE="${DEPLOY_DIR}/.env"
EXAMPLE_ENV="${DEPLOY_DIR}/.env.example"
COMPOSE_FILE="${DEPLOY_DIR}/docker-compose.central.yml"

INIT_ENV=0
INIT_ENV_ONLY=0
WRITE_CONFIG=0
DO_UP=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --init-env) INIT_ENV=1; shift ;;
    --init-env-only) INIT_ENV=1; INIT_ENV_ONLY=1; shift ;;
    --write-config) WRITE_CONFIG=1; shift ;;
    --no-up) DO_UP=0; shift ;;
    --help|-h)
      grep '^#' "$0" | head -n 20
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

resolve_compose_cmd() {
  if docker compose version >/dev/null 2>&1; then
    echo "docker compose"
    return 0
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    echo "docker-compose"
    return 0
  fi
  echo ""
  return 1
}

gen_hex() {
  openssl rand -hex "${1:-32}"
}

init_env_file() {
  if [[ -f "$ENV_FILE" ]]; then
    echo "Already exists: $ENV_FILE (skip --init-env)"
    return 0
  fi
  if [[ ! -f "$EXAMPLE_ENV" ]]; then
    echo "Missing $EXAMPLE_ENV" >&2
    exit 1
  fi
  require_cmd openssl
  local db pass token
  db="${POSTGRES_DB:-nightmare}"
  user="${POSTGRES_USER:-nightmare}"
  pass="$(gen_hex 24)"
  token="$(gen_hex 48)"
  local base_url="${COORDINATOR_BASE_URL:-https://127.0.0.1}"
  cat >"$ENV_FILE" <<EOF
POSTGRES_DB=${db}
POSTGRES_USER=${user}
POSTGRES_PASSWORD=${pass}
POSTGRES_HOST_PORT=${POSTGRES_HOST_PORT:-5432}
COORDINATOR_API_TOKEN=${token}
TLS_CERT_FILE=${TLS_CERT_FILE:-/tmp/nightmare-tls-placeholder.crt}
TLS_KEY_FILE=${TLS_KEY_FILE:-/tmp/nightmare-tls-placeholder.key}
COORDINATOR_BASE_URL=${base_url}
EOF
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE (set TLS_CERT_FILE/TLS_KEY_FILE and COORDINATOR_BASE_URL for production)."
}

load_env_exports() {
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
}

compose_up_postgres() {
  local cc
  cc="$(resolve_compose_cmd)" || {
    echo "docker compose or docker-compose is required." >&2
    exit 1
  }
  require_cmd docker
  cd "$DEPLOY_DIR"
  $cc -f docker-compose.central.yml --env-file .env up -d postgres
}

wait_postgres_healthy() {
  local name tries
  require_cmd docker
  name="${POSTGRES_CONTAINER_NAME:-nightmare-postgres}"
  tries=45
  echo "Waiting for Postgres (${name})..."
  while [[ $tries -gt 0 ]]; do
    if docker exec "$name" pg_isready -U "${POSTGRES_USER:-nightmare}" -d "${POSTGRES_DB:-nightmare}" >/dev/null 2>&1; then
      echo "Postgres is ready."
      return 0
    fi
    sleep 2
    tries=$((tries - 1))
  done
  echo "Timeout waiting for Postgres." >&2
  return 1
}

print_summary() {
  load_env_exports
  local host_port="${POSTGRES_HOST_PORT:-5432}"
  local enc_pass
  enc_pass="$(python -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "${POSTGRES_PASSWORD:-}")"
  local url="postgresql://${POSTGRES_USER}:${enc_pass}@127.0.0.1:${host_port}/${POSTGRES_DB}"
  echo ""
  echo "=== Postgres (coordinator) ==="
  echo "Host port: ${host_port} -> container 5432"
  echo "DATABASE_URL (from host):"
  echo "  ${url}"
  echo ""
  echo "psql example:"
  echo "  psql \"${url}\""
  echo ""
  echo "Coordinator API token (Authorization: Bearer ...):"
  echo "  ${COORDINATOR_API_TOKEN:-}"
  echo ""
  echo "Schema is applied when server.py starts with this DATABASE_URL (CoordinatorStore)."
}

write_server_json() {
  load_env_exports
  export POSTGRES_HOST_PORT="${POSTGRES_HOST_PORT:-5432}"
  export ROOT_DIR_FOR_SETUP="$ROOT_DIR"
  python - <<'PY'
import json
import os
import urllib.parse
from pathlib import Path

root = Path(os.environ["ROOT_DIR_FOR_SETUP"])
cfg_path = root / "config" / "server.json"
password = os.environ.get("POSTGRES_PASSWORD", "")
user = os.environ.get("POSTGRES_USER", "nightmare")
db = os.environ.get("POSTGRES_DB", "nightmare")
port = int(os.environ.get("POSTGRES_HOST_PORT", "5432"))
token = os.environ.get("COORDINATOR_API_TOKEN", "").strip()
enc = urllib.parse.quote(password, safe="")
url = f"postgresql://{user}:{enc}@127.0.0.1:{port}/{db}"

cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
cfg["database_url"] = url
if token:
    cfg["coordinator_api_token"] = token
cfg_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Updated {cfg_path} with database_url" + (" and coordinator_api_token." if token else "."))
PY
}

# ---- main ----

if [[ "$INIT_ENV" -eq 1 ]]; then
  init_env_file
fi

if [[ "$INIT_ENV_ONLY" -eq 1 ]]; then
  echo "Exiting after --init-env-only."
  exit 0
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Run: $0 --init-env  or copy from .env.example" >&2
  exit 1
fi

load_env_exports

if [[ "$DO_UP" -eq 1 ]]; then
  compose_up_postgres
  wait_postgres_healthy || true
fi

print_summary

if [[ "$WRITE_CONFIG" -eq 1 ]]; then
  write_server_json
fi

exit 0
