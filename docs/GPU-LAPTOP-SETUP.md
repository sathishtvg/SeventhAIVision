# Moving the stack to a GPU machine

Written 2026-09-05, from the state of the project on that date: branch
`Development`, migration head `0086`, database ~225 MB.

The point of the move is that inference on the old laptop was not slow, it was
unusable — face detection measured **38.8 s** on a 480 px image with the CPU
otherwise idle, and **over nine minutes** while mediamtx was transcoding the
demo loops. Nothing about the code changes on a GPU host; `resolve_device()`
picks CUDA when torch reports it and falls back to CPU when it does not.

---

## 1. What travels, and what does not

| | |
|---|---|
| From `git clone` | All source, **and the model weights** — `yolov8s-oiv7.pt` and `liveness_minifasnet.onnx` are tracked, so they come with the repo |
| **Carried by hand** | `docker/.env` (gitignored), the **database**, and optionally the Claude memory + transcripts |
| Rebuilt on arrival | Every Docker image. Do not try to copy `docker_data.vhdx` — see §6 |

```bash
git clone https://github.com/sathishtvg/SeventhAIVision.git
cd SeventhAIVision
git checkout Development
```

## 2. Prerequisites on a **Windows** GPU laptop

`scripts/gpu-setup.sh` in this repo is for **Ubuntu hosts only** — do not run it
on Windows. On Windows the path is shorter than it looks:

1. **NVIDIA Windows driver ≥ 525** (newer is fine). This alone provides CUDA
   inside WSL2. There is no separate driver to install into WSL.
2. **Docker Desktop** with the WSL2 backend enabled.
3. **Do not** install `nvidia-container-toolkit` inside WSL. It is required for
   native Linux hosts and actively gets in the way on Docker Desktop, which
   wires GPU access through WSL2 itself.

Verify before going further — if this does not print your card, nothing below
will work:

```bash
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

On an **Ubuntu** host instead, run `sudo bash scripts/gpu-setup.sh`, which
installs the driver and the Container Toolkit and configures the Docker runtime.

## 3. Environment file

`docker/.env` is gitignored and will not be in the clone. Build it from the
template:

```bash
cp docker/.env.example docker/.env
```

Then fill in every `CHANGE_ME`. Generate each one separately — never reuse a
value across two keys.

**`CREDENTIALS_ENCRYPTION_KEY` is the one to think about.** It encrypts stored
device and gateway credentials at rest. If you are restoring the database from
the old machine (§4), this key **must be the value from the old `docker/.env`**,
or every stored credential becomes unreadable. Only generate a fresh one if you
are starting with an empty database.

## 4. Moving the database

The volumes do not travel; a dump does. On the **old** machine:

```bash
docker exec docker-postgres-1 pg_dump -U postgres -Fc seventh_ai_vision > seventh_ai_vision.dump
```

Copy that file across, bring up Postgres alone on the new machine, then:

```bash
docker compose -f docker/docker-compose.yml up -d postgres
docker cp seventh_ai_vision.dump docker-postgres-1:/tmp/db.dump
docker exec docker-postgres-1 pg_restore -U postgres -d seventh_ai_vision --clean --if-exists /tmp/db.dump
```

Then create and migrate the **test** database as well — see §7, this is the
mistake that has cost two full test runs already.

## 5. Bringing it up with GPU

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.gpu.yml up -d
```

The overlay sets `DEVICE: cuda`, reserves one GPU per worker and raises
`shm_size` to 2 GB for CUDA tensor operations. It covers all twelve workers.

**Watch memory.** One GPU with `count: 1` per worker means twelve workers each
claiming a device; on a single-GPU laptop change `count` to `all` so they share
it, or start only the workers you are testing:

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.gpu.yml \
  up -d postgres redis api ingestion scheduler frontend minio minio-init mediamtx ai-worker-face
```

### Confirm it is actually on the GPU

Do not assume — `resolve_device()` falls back to CPU silently, which is exactly
how a "GPU run" ends up reporting CPU timings:

```bash
docker exec docker-ai-worker-face-1 python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
docker compose logs ai-worker-face | grep -i "device\|cuda"
```

Then re-measure face detection against the 38.8 s CPU baseline. That number is
the whole reason for the move, so it is the number to check first.

## 6. Traps that cost days on the old machine

**Do not copy `docker_data.vhdx` to the new machine.** Docker's WSL data disk
carries the owning account's SID. Moving it to a machine with a different
account produces `Wsl/Service/AttachDisk/MountDisk/HCS/E_ACCESSDENIED`, which
surfaces only as "Docker Desktop is unable to start" and took a full session to
diagnose. Rebuild the images instead — it is faster than the recovery.

**Orphaned Unix sockets survive a crash.** If Docker Desktop starts crashing at
launch with "The file cannot be accessed by the system", the culprit is stale
`.sock` files in `%LOCALAPPDATA%\Docker\run` and
`%LOCALAPPDATA%\docker-secrets-engine`. Windows cannot delete or rename those
files at all — but the **parent folder can be renamed**, and Docker recreates
it. Clear both in one pass, or it crashes on whichever you missed.

**Git Bash rewrites container paths.** `docker exec ... /app/backend/tests`
becomes `C:/Program Files/Git/app/backend/tests` and fails while still exiting
0. Prefix with `MSYS_NO_PATHCONV=1`.

## 7. Migrations, and the test database

Any session that adds an Alembic migration must apply it **twice** — once to
the dev database and once to the test one — before trusting a single pytest
result:

```bash
MSYS_NO_PATHCONV=1 docker exec docker-api-1 python -m alembic upgrade head

MSYS_NO_PATHCONV=1 docker exec -e ALEMBIC_DATABASE_URL=postgresql+psycopg://postgres:<password>@postgres:5432/seventh_ai_vision_test \
  docker-api-1 python -m alembic upgrade head
```

Skipping the second produced 22 failures that looked exactly like real
regressions, on a run lasting 1 h 53 m. Confirm both report the same head:

```bash
docker exec docker-postgres-1 psql -U postgres -d seventh_ai_vision      -tc "select version_num from alembic_version;"
docker exec docker-postgres-1 psql -U postgres -d seventh_ai_vision_test -tc "select version_num from alembic_version;"
```

## 8. Claude session history (optional)

Memory and transcripts are keyed by folder path and live outside the repo. To
carry them, copy the old machine's

```
~/.claude/projects/D--Claude-Project-Virtual-Patrolling/
```

to the folder name the new machine's path mangles to. **The transcripts contain
at least three live credentials typed during past sessions — keep them out of
the repository.**

## 9. First things worth testing on the GPU

These were blocked purely by hardware and are now unblocked:

- **Face detection latency.** Baseline to beat: 38.8 s idle, 9 min under load.
- **Liveness spoof rejection.** A real photo scores 0.9102 against a 0.70
  threshold, but rejection of a printed photo or a phone screen has never been
  tested — and that is the half that actually protects anything. Needs a
  printout and a second phone.
- **The mobile camera flow end to end.** Never run; the old laptop could not
  start an Android emulator with 516 MB free.
- **Server-side downscaling before detection.** Treat as required rather than
  optional; the 480 px measurement above is what full-resolution frames would
  be multiplied against.
