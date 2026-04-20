#!/usr/bin/env bash
set -euo pipefail

ROLE="${APP_ROLE:-${1:-server}}"
if [[ $# -gt 0 ]]; then
  shift || true
fi

case "${ROLE}" in
  server)
    exec python server.py "$@"
    ;;
  coordinator|worker)
    exec python coordinator.py "$@"
    ;;
  nightmare)
    exec python nightmare.py "$@"
    ;;
  fozzy)
    exec python fozzy.py "$@"
    ;;
  extractor)
    exec python extractor.py "$@"
    ;;
  auth0r)
    exec python auth0r.py "$@"
    ;;
  *)
    echo "Unknown APP_ROLE=${ROLE}. Valid: server|coordinator|worker|nightmare|fozzy|extractor|auth0r" >&2
    exit 2
    ;;
esac

