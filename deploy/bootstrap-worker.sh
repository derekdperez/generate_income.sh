#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_DIR="${ROOT_DIR}/deploy"

if [[ ! -f "${DEPLOY_DIR}/.env" ]]; then
  echo "Missing ${DEPLOY_DIR}/.env (copy from .env.example and fill COORDINATOR_BASE_URL/COORDINATOR_API_TOKEN)." >&2
  exit 1
fi

cd "${DEPLOY_DIR}"
docker compose -f docker-compose.worker.yml --env-file .env up -d --build
echo "Worker started."

