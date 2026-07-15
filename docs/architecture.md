# Architecture Overview

Full design rationale lives in the implementation plan this codebase was built from:
`C:\Users\acer\.claude\plans\seventh-ai-vision-abstract-token.md`. This file is a short pointer, not a duplicate.

## Services

| Service | Process | DB driver | Purpose |
|---|---|---|---|
| `api` | FastAPI, async | `asyncpg` | REST API, JWT auth/RBAC, `/ws/live` realtime push |
| `ingestion` | async, standalone | `asyncpg` | RTSP/ONVIF camera capture, publishes `frame_jobs` to Redis Streams |
| `ai-worker-lpr` | sync consumer loop | `psycopg` | License plate detection (YOLOv8s) + OCR (PaddleOCR) |
| `ai-worker-face` | sync consumer loop | `psycopg` | Face detection/recognition (insightface `buffalo_l`) |
| `ai-worker-intrusion` | sync consumer loop | `psycopg` | Person detection (YOLOv8s) + zone breach (shapely) |
| `scheduler` | async, standalone | `asyncpg` | Daily partition maintenance + evidence/audit retention |
| `postgres` | — | — | Tenant-isolated via Row-Level Security; partitioned high-volume tables |
| `redis` | — | — | Streams (`frame_jobs`, per-module consumer groups) + Pub/Sub (`tenant_events:*`) + rate-limit/cache backend |

## Core invariants

- **Tenant isolation**: every tenant-scoped table has RLS enabled+forced; every DB session sets
  `app.current_tenant` before querying. No router/task queries a raw, unscoped session.
- **Generic detections**: `detections` is the parent for every AI module's output; `module_type` discriminates.
  Each module has a 1:1 child table (`lpr_events`, `face_events`, `intrusion_events`) for module-specific fields.
- **One device-resolution function**: `ai-worker/worker/device.py::resolve_device()` is the only place CUDA/CPU
  is decided. Every model load in every pipeline routes through it.
- **Crash-safe queues**: a Redis Streams message is only `XACK`'d after its DB transaction commits. Crashed
  workers leave messages in the consumer group's PEL, reclaimed via `XPENDING`/`XCLAIM` on the next loop pass.
