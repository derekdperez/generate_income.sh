#!/usr/bin/env bash
# One-command local / EC2 deploy for the full Nightmare v2 .NET stack (Docker Compose).
#
# Requires either:
#   - Docker Compose V2:  "docker compose version" works (install docker-compose-plugin), or
#   - Docker Compose V1:  standalone "docker-compose" on PATH.
#
# If you see: unknown shorthand flag: 'd' in -d
#   you ran "docker compose ..." without the Compose plugin — "compose" was ignored and
#   "up -d" was parsed as invalid global docker flags. Install the plugin or use docker-compose.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/deploy/docker-compose.yml"
cd "$ROOT"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required. Install Docker Engine, then re-run." >&2
  exit 1
fi

compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose -f "$COMPOSE_FILE" "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose -f "$COMPOSE_FILE" "$@"
  else
    echo "Docker Compose is not available." >&2
    echo "  Install V2 (recommended): https://docs.docker.com/compose/install/linux/" >&2
    echo "  e.g. Ubuntu: sudo apt-get install docker-compose-plugin" >&2
    echo "  Then verify: docker compose version" >&2
    exit 1
  fi
}

echo "Building images and starting stack from: $ROOT"
compose up -d --build

echo ""
echo "Nightmare v2 is running."
echo "  Command Center:  http://localhost:8080/  (use host public IP on EC2)"
echo "  RabbitMQ admin:  http://localhost:15672/  (user/pass: nightmare / nightmare)"
echo "  Postgres:        localhost:5432  db=nightmare_v2  user=nightmare"
echo ""
echo "Useful commands (from $ROOT):"
echo "  docker compose -f deploy/docker-compose.yml logs -f worker-spider"
echo "  docker compose -f deploy/docker-compose.yml down"
echo "(or docker-compose -f deploy/docker-compose.yml ... if you use V1)"
echo ""
