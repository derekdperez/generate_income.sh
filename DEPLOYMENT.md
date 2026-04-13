# Distributed Deployment (EC2 + Docker + Postgres)

## Overview
- One EC2 VM runs the **central coordinator server** (`server.py`) + Postgres.
- Any number of EC2 worker VMs run `coordinator.py`.
- Workers claim jobs from central APIs, heartbeat leases, upload artifacts/session checkpoints, and continue pipeline stages (`nightmare -> fozzy -> extractor`) without duplicate locks.

## 1) Build Image
From repo root:

```bash
docker build -t nightmare:latest .
```

## 2) Central VM
1. Copy repo to the central VM.
2. Copy `deploy/.env.example` to `deploy/.env` and set real values.
3. Ensure TLS cert/key files exist on host (for port 443).
4. Start:

```bash
cd deploy
docker compose -f docker-compose.central.yml --env-file .env up -d --build
```

## 3) Register Targets
Use coordinator API token:

```bash
python register_targets.py \
  --server-base-url https://<central-host> \
  --api-token <COORDINATOR_API_TOKEN> \
  --targets-file targets.txt
```

## 4) Worker VM(s)
1. Copy repo to each worker VM.
2. Set `config/coordinator.json` (or env vars) with:
   - `server_base_url`
   - `api_token`
3. Start worker container:

```bash
cd deploy
docker compose -f docker-compose.worker.yml --env-file .env up -d --build
```

## Security / Networking
- Open inbound ports:
  - `443/tcp` to worker VMs (and admins) for coordinator API.
  - `80/tcp` optional (HTTP endpoint).
- AWS security group baseline:
  - Allow `443` from worker subnet/security-group only.
  - Optional allow `80` from admin IPs or disable `http_port`.
  - Do not expose Postgres (`5432`) publicly; keep internal only.
- Restrict Postgres to internal container/network only on central VM.
- Use strong `COORDINATOR_API_TOKEN`.
- Do not hardcode API keys in images; pass via env or secret manager.

## Resume / Locking Model
- Target queue lock: `/api/coord/claim` + lease heartbeat + `/complete`.
- Stage queue lock: `/api/coord/stage/claim` + heartbeat + `/complete`.
- Session checkpoint: workers periodically POST `/api/coord/session` while Nightmare runs.
- Artifact replication: workers upload/download artifacts through `/api/coord/artifact` so other VMs can continue.
