#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_DIR="${ROOT_DIR}/deploy"
TLS_DIR="${DEPLOY_DIR}/tls"
ENV_FILE="${DEPLOY_DIR}/.env"
WORKER_ENV_FILE="${DEPLOY_DIR}/worker.env.generated"

POSTGRES_DB_DEFAULT="nightmare"
POSTGRES_USER_DEFAULT="nightmare"

FORCE_REGEN=0
BASE_URL_OVERRIDE=""
CERT_DAYS=825

usage() {
  cat <<'USAGE'
Usage:
  ./deploy/bootstrap-central-auto.sh [--base-url https://host-or-ip] [--force]

What it does:
  - generates strong Postgres password + coordinator API token
  - auto-detects public host/IP (EC2 metadata first, external IP fallback)
  - generates self-signed TLS cert/key (unless already present)
  - writes deploy/.env
  - writes deploy/worker.env.generated (for worker VMs)
  - rebuilds and starts central docker compose stack
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url)
      BASE_URL_OVERRIDE="${2:-}"
      shift 2
      ;;
    --force)
      FORCE_REGEN=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

require_cmd() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    echo "Missing required command: $name" >&2
    exit 1
  fi
}

abs_path() {
  local p="$1"
  cd "$(dirname "$p")"
  echo "$(pwd -P)/$(basename "$p")"
}

gen_password() {
  openssl rand -hex 32
}

gen_token() {
  openssl rand -hex 48
}

metadata_get() {
  local path="$1"
  local token=""
  token="$(curl -fsS -m 2 -X PUT "http://169.254.169.254/latest/api/token" -H "X-aws-ec2-metadata-token-ttl-seconds: 60" || true)"
  if [[ -n "$token" ]]; then
    curl -fsS -m 2 -H "X-aws-ec2-metadata-token: ${token}" "http://169.254.169.254/latest/meta-data/${path}" || true
    return 0
  fi
  curl -fsS -m 2 "http://169.254.169.254/latest/meta-data/${path}" || true
}

detect_base_url() {
  if [[ -n "$BASE_URL_OVERRIDE" ]]; then
    echo "$BASE_URL_OVERRIDE"
    return 0
  fi

  local public_hostname public_ip
  public_hostname="$(metadata_get public-hostname | tr -d '\r\n')"
  public_ip="$(metadata_get public-ipv4 | tr -d '\r\n')"

  if [[ -n "$public_hostname" ]]; then
    echo "https://${public_hostname}"
    return 0
  fi
  if [[ -n "$public_ip" ]]; then
    echo "https://${public_ip}"
    return 0
  fi

  public_ip="$(curl -fsS -m 4 https://checkip.amazonaws.com 2>/dev/null | tr -d '\r\n' || true)"
  if [[ -n "$public_ip" ]]; then
    echo "https://${public_ip}"
    return 0
  fi

  local host_name
  host_name="$(hostname -f 2>/dev/null || hostname)"
  host_name="$(echo "$host_name" | tr -d '\r\n')"
  if [[ -n "$host_name" ]]; then
    echo "https://${host_name}"
    return 0
  fi

  echo "https://127.0.0.1"
}

build_san() {
  local host_no_scheme="$1"
  if [[ "$host_no_scheme" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "IP:${host_no_scheme},DNS:localhost,IP:127.0.0.1"
  else
    echo "DNS:${host_no_scheme},DNS:localhost,IP:127.0.0.1"
  fi
}

generate_cert_if_needed() {
  local cert_file="$1"
  local key_file="$2"
  local cn="$3"
  local san="$4"

  if [[ -f "$cert_file" && -f "$key_file" && "$FORCE_REGEN" -ne 1 ]]; then
    return 0
  fi

  mkdir -p "$TLS_DIR"
  openssl req -x509 -newkey rsa:4096 -sha256 -nodes \
    -keyout "$key_file" \
    -out "$cert_file" \
    -days "$CERT_DAYS" \
    -subj "/CN=${cn}" \
    -addext "subjectAltName=${san}" \
    >/dev/null 2>&1
  chmod 600 "$key_file"
}

require_cmd docker
require_cmd openssl
require_cmd curl

mkdir -p "$TLS_DIR"
POSTGRES_DB="${POSTGRES_DB_DEFAULT}"
POSTGRES_USER="${POSTGRES_USER_DEFAULT}"
POSTGRES_PASSWORD="$(gen_password)"
COORDINATOR_API_TOKEN="$(gen_token)"
COORDINATOR_BASE_URL="$(detect_base_url)"
BASE_HOST="${COORDINATOR_BASE_URL#https://}"
BASE_HOST="${BASE_HOST#http://}"
BASE_HOST="${BASE_HOST%%/*}"

CERT_FILE="${TLS_DIR}/server.crt"
KEY_FILE="${TLS_DIR}/server.key"
generate_cert_if_needed "$CERT_FILE" "$KEY_FILE" "$BASE_HOST" "$(build_san "$BASE_HOST")"

TLS_CERT_FILE="$(abs_path "$CERT_FILE")"
TLS_KEY_FILE="$(abs_path "$KEY_FILE")"

cat >"$ENV_FILE" <<EOF
POSTGRES_DB=${POSTGRES_DB}
POSTGRES_USER=${POSTGRES_USER}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
COORDINATOR_API_TOKEN=${COORDINATOR_API_TOKEN}
TLS_CERT_FILE=${TLS_CERT_FILE}
TLS_KEY_FILE=${TLS_KEY_FILE}
COORDINATOR_BASE_URL=${COORDINATOR_BASE_URL}
EOF
chmod 600 "$ENV_FILE"

cat >"$WORKER_ENV_FILE" <<EOF
COORDINATOR_BASE_URL=${COORDINATOR_BASE_URL}
COORDINATOR_API_TOKEN=${COORDINATOR_API_TOKEN}
EOF
chmod 600 "$WORKER_ENV_FILE"

cd "$DEPLOY_DIR"
docker compose -f docker-compose.central.yml --env-file .env up -d --build

echo "Central stack is running."
echo "Generated files:"
echo "  - ${ENV_FILE}"
echo "  - ${WORKER_ENV_FILE}"
echo "  - ${CERT_FILE}"
echo "  - ${KEY_FILE}"
echo
echo "Use ${WORKER_ENV_FILE} on each worker VM as deploy/.env."
