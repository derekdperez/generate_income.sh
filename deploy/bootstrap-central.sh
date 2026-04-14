#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_DIR="${ROOT_DIR}/deploy"
SETUP_PG="${DEPLOY_DIR}/setup-postgres.sh"

if [[ ! -f "${DEPLOY_DIR}/.env" ]]; then
  echo "Missing ${DEPLOY_DIR}/.env (copy from .env.example and fill secrets)." >&2
  echo "Or run: ${SETUP_PG} --init-env" >&2
  exit 1
fi

cd "${DEPLOY_DIR}"
docker compose -f docker-compose.central.yml --env-file .env up -d --build
echo "Central coordinator stack started."

if [[ -f "$SETUP_PG" ]]; then
  bash "$SETUP_PG" --no-up || true
fi

