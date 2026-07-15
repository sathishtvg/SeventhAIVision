#!/usr/bin/env bash
# Seventh AI Vision — GPU Host Setup
# Installs NVIDIA drivers + Container Toolkit on Ubuntu 22.04 / 20.04 (amd64).
#
# Usage (as root or with sudo):
#   chmod +x scripts/gpu-setup.sh
#   sudo bash scripts/gpu-setup.sh
#
# After running, verify with:
#   nvidia-smi
#   docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi

set -euo pipefail

DRIVER_VERSION="${NVIDIA_DRIVER_VERSION:-535}"
CUDA_VERSION="${CUDA_VERSION:-12.1}"

echo "=== Seventh AI Vision — GPU Host Setup ==="
echo "Target: Ubuntu 22.04/20.04 amd64"
echo "Driver: ${DRIVER_VERSION}, CUDA: ${CUDA_VERSION}"
echo ""

# ── 1. System update ──────────────────────────────────────────────────────────
echo "[1/5] Updating package lists..."
apt-get update -qq

# ── 2. NVIDIA driver ──────────────────────────────────────────────────────────
echo "[2/5] Installing NVIDIA driver ${DRIVER_VERSION}..."
apt-get install -y --no-install-recommends \
    linux-headers-$(uname -r) \
    "nvidia-driver-${DRIVER_VERSION}" \
    "nvidia-utils-${DRIVER_VERSION}"

# ── 3. NVIDIA Container Toolkit repository ───────────────────────────────────
echo "[3/5] Adding NVIDIA Container Toolkit repository..."
apt-get install -y --no-install-recommends curl gnupg

curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    | tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

# ── 4. Install nvidia-container-toolkit ──────────────────────────────────────
echo "[4/5] Installing nvidia-container-toolkit..."
apt-get update -qq
apt-get install -y --no-install-recommends nvidia-container-toolkit

# Configure Docker daemon to use the NVIDIA runtime as default for GPU containers.
# This does NOT set it as the global default runtime — only containers with
# --gpus or deploy.resources.reservations.devices will use it.
nvidia-ctk runtime configure --runtime=docker

# Restart Docker to pick up the new runtime configuration.
echo ""
echo "[5/5] Restarting Docker daemon..."
systemctl restart docker

# ── Verify ────────────────────────────────────────────────────────────────────
echo ""
echo "=== Verification ==="
echo "nvidia-smi output:"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null \
    || echo "  nvidia-smi not yet available — reboot may be required if driver was freshly installed."

echo ""
echo "Docker nvidia runtime config:"
docker info 2>/dev/null | grep -i nvidia || echo "  Restart Docker if nvidia runtime not listed."

echo ""
echo "=== Done ==="
echo "Start with GPU acceleration:"
echo "  docker compose -f docker/docker-compose.yml -f docker/docker-compose.gpu.yml up -d"
