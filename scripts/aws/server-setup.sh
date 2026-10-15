#!/usr/bin/env bash
# Runs ON the AWS demo server (Ubuntu 22.04), started by `scripts/aws/aws-demo.sh setup`. Safe to run again:
# it updates the code, rebuilds and restarts, and makes the secrets and the demo data only the first time.
#
#   SEVENTH_MODE=free  (default) no GPU: the application without the AI analytics
#   SEVENTH_MODE=gpu   the AWS Deep Learning image on a GPU machine: the AI workers too
#
#   /opt/seventh/app                the code, cloned from GitHub (the repository is public)
#   /opt/seventh/docker.env         the application's secrets, made here once and never sent anywhere
#   /opt/seventh/demo-accounts.txt  the demo accounts and their password (aws-demo.sh copies it to the laptop)
#
# The demo organisation is made by backend/scripts/seed_demo.py, which drops and remakes the tenant `demo`:
# on this server there is nothing else in it. It is given a password made here, not the one written in the
# public repository, and this server's database address.
set -euo pipefail

MODE="${SEVENTH_MODE:-free}"
HOME_DIR=/opt/seventh
APP=$HOME_DIR/app
REPO=https://github.com/sathishtvg/SeventhAIVision.git
BRANCH="${SEVENTH_BRANCH:-main}"
CORE="postgres redis frontend-certs api ingestion scheduler drone-runner intelligence-runner frontend mediamtx"
if [ "$MODE" = gpu ]; then
  WORKERS="${SEVENTH_AI_WORKERS:-}"
  [ -n "$WORKERS" ] || WORKERS="ai-worker-intrusion ai-worker-face"
  COMPOSE="-f docker/docker-compose.yml -f docker/docker-compose.gpu.yml"
else
  WORKERS=""
  COMPOSE="-f docker/docker-compose.yml"
fi
D="sudo docker"
step() { printf '\n== %s\n' "$*"; }

# demo_showcase.py reads the demo password out of seed_demo.py (the public one); here it is given this server's.
showcase() {  # showcase <password> [step]
  local script
  script="$(sed 's|^    seed = Path("/app/backend/scripts/seed_demo.py").read_text(encoding="utf-8")$|    return os.environ["DEMO_PASSWORD"]|' "$APP/scripts/ops/demo_showcase.py")"
  grep -q 'return os.environ\["DEMO_PASSWORD"\]' <<<"$script" || { echo "demo_showcase.py changed; not run"; return 1; }
  printf '%s\n' "$script" | $D exec -i -e DEMO_PASSWORD="$1" -e DEMO_STEP="${2:-}" docker-api-1 python -
}

# Only one step of the demo story (today | live), on a server that is set up already.
if [ -n "${SEVENTH_DEMO_STEP:-}" ]; then
  pw="$(sed -n 's/^Password (every account below): //p' "$HOME_DIR/demo-accounts.txt")"
  [ -n "$pw" ] || { echo "No demo accounts yet: run setup first."; exit 1; }
  showcase "$pw" "$SEVENTH_DEMO_STEP"
  exit 0
fi

step "Folders ($MODE)"
sudo mkdir -p "$HOME_DIR"
sudo chown "$(id -un)":"$(id -gn)" "$HOME_DIR"
chmod 700 "$HOME_DIR"

if [ "$MODE" = free ] && [ "$(swapon --show | wc -l)" = 0 ]; then
  step "Swap: 4 GB, so building on an 8 GB machine does not run out of memory"
  sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile >/dev/null && sudo swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

step "Docker and Compose"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
if ! $D compose version >/dev/null 2>&1; then
  sudo apt-get update -y && (sudo apt-get install -y docker-compose-plugin || sudo apt-get install -y docker-compose-v2)
fi
$D compose version

if [ "$MODE" = gpu ]; then
  step "GPU"
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
  if ! $D run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi -L; then
    echo "Docker cannot reach the GPU yet; scripts/gpu-setup.sh runs once the code is here."
    NEED_GPU_SETUP=1
  fi
fi

step "Code ($BRANCH)"
if [ -d "$APP/.git" ]; then
  git -C "$APP" fetch --quiet origin
  git -C "$APP" checkout --quiet "$BRANCH"
  git -C "$APP" pull --quiet --ff-only origin "$BRANCH"
else
  git clone --quiet --branch "$BRANCH" "$REPO" "$APP"
fi
git -C "$APP" log --oneline -1
if [ "${NEED_GPU_SETUP:-0}" = 1 ]; then
  sudo bash "$APP/scripts/gpu-setup.sh"
  $D run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi -L
fi

step "Secrets (made once, kept in $HOME_DIR/docker.env)"
if [ ! -f "$HOME_DIR/docker.env" ]; then
  hex() { openssl rand -hex "$1"; }
  fernet="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
  umask 077
  cat > "$HOME_DIR/docker.env" <<EOF
POSTGRES_DB=seventh_ai_vision
POSTGRES_USER=postgres
POSTGRES_PASSWORD=$(hex 24)
POSTGRES_APP_USER=svc_app
POSTGRES_APP_PASSWORD=$(hex 24)
REDIS_URL=redis://redis:6379/0
JWT_SECRET_KEY_CURRENT=$(hex 32)
JWT_SECRET_KEY_PREVIOUS=$(hex 32)
JWT_ACTIVE_KID=aws-$(date +%Y-%m)
JWT_ALGORITHM=HS256
ACCESS_TOKEN_TTL_MIN=15
REFRESH_TOKEN_TTL_DAYS=7
EVIDENCE_ROOT=/data/evidence
LPR_CONFIDENCE_THRESHOLD=0.55
FACE_MATCH_THRESHOLD=0.6
INTRUSION_BREACH_COOLDOWN_SECONDS=60
EVIDENCE_RETENTION_DAYS=90
AUDIT_RETENTION_YEARS=7
DEVICE=
GRAFANA_ADMIN_PASSWORD=$(hex 16)
APP_VERSION=0.1.0
GIT_SHA=$(git -C "$APP" rev-parse --short HEAD)
BUILD_DATE=$(date -u +%Y-%m-%d)
AUDIT_HMAC_KEY=$(hex 32)
CREDENTIALS_ENCRYPTION_KEY=$fernet
DRONE_EDGE_KEY=
DRONE_EDGE_CENTRAL_URL=http://api:8000
EOF
  umask 022
  echo "Made."
else
  echo "Kept."
fi
install -m 600 "$HOME_DIR/docker.env" "$APP/docker/.env"

step "Build (the first time takes a while)"
cd "$APP"
$D compose -f docker/docker-compose.yml build api ingestion scheduler drone-runner intelligence-runner frontend postgres
if [ -n "$WORKERS" ]; then
  # shellcheck disable=SC2086
  $D compose $COMPOSE build $WORKERS
fi

step "Start: $CORE $WORKERS"
# shellcheck disable=SC2086
$D compose $COMPOSE up -d --no-deps $CORE $WORKERS
for i in $(seq 1 120); do
  if curl -fsS -o /dev/null http://localhost:8000/health; then break; fi
  sleep 5
done
curl -fsS -o /dev/null http://localhost:8000/health && echo "API healthy."
$D exec docker-api-1 sh -c 'cd /app/backend && alembic current 2>/dev/null | tail -1'

step "Demo organisation (made once)"
if [ ! -f "$HOME_DIR/demo-accounts.txt" ]; then
  pw="$(openssl rand -base64 18 | tr -d '/+=' | cut -c1-16)Aa1"
  $D exec -i -e DEMO_PASSWORD="$pw" -w /app/backend docker-api-1 python - <<'PY' | grep -v -i "password"
import asyncio, os
import scripts.seed_demo as seed
seed.ADMIN_URL = os.environ["ALEMBIC_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql+asyncpg://")
seed.DEMO_PASSWORD = os.environ["DEMO_PASSWORD"]
asyncio.run(seed.seed())
PY
  showcase "$pw"
  if [ "$MODE" = free ]; then
    # Continuous recording of three cameras is what overloaded an 8 GB laptop on 8 October 2026; a 2-processor
    # machine without a GPU has less to spare. Live view and alerts still work without it.
    $D exec docker-postgres-1 psql -U postgres -d seventh_ai_vision -qAt -c \
      "update cameras set continuous_recording = false where tenant_id = (select id from tenants where slug = 'demo')"
    echo "Continuous recording switched off on the demo cameras (switch it on per camera if wanted)."
  fi
  emails="$($D exec docker-postgres-1 psql -U postgres -d seventh_ai_vision -At -c \
    "select u.email || '  (' || r.name || ')' from users u join roles r on r.id = u.role_id join tenants t on t.id = u.tenant_id where t.slug = 'demo' order by u.role_id, u.email")"
  umask 077
  {
    echo "Seventh AI Vision demo server - accounts of the organisation 'demo'"
    echo "Organisation: demo"
    echo "Password (every account below): $pw"
    echo
    echo "$emails"
  } > "$HOME_DIR/demo-accounts.txt"
  umask 022
  echo "Made. Accounts: $(echo "$emails" | wc -l)"
else
  echo "Kept. To refresh the story: scripts/aws/aws-demo.sh today (or live) on the laptop."
fi

if [ -n "$WORKERS" ]; then
  step "Is the AI on the GPU?"
  for w in $WORKERS; do
    printf '%s: ' "$w"
    $D exec "docker-$w-1" python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')" 2>&1 | tail -1
  done
fi
echo
echo "Done ($MODE)."
