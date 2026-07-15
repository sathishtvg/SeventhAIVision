# GPU-accelerated AI worker image.
# Uses the official NVIDIA CUDA runtime base instead of plain python:3.11-slim.
#
# Build (from project root):
#   docker build -f docker/ai-worker-gpu.Dockerfile -t seventh-ai-vision/ai-worker-gpu:1.0.0 .
#
# Usage: replace the `image:` field (or build: context) for ai-worker-* services in
#   docker-compose.gpu.yml to use this image, or set COMPOSE_FILE appropriately.
#
# CUDA 12.1 + cuDNN 8 — compatible with PyTorch 2.x wheels from the CUDA index.
# Pin the base tag to keep builds reproducible across minor CUDA releases.

FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV DEVICE=cuda

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3.11 \
        python3.11-dev \
        python3-pip \
        libgl1 \
        libglib2.0-0 \
        ffmpeg \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3.11 /usr/bin/python3 \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && ln -sf /usr/bin/pip3 /usr/bin/pip

WORKDIR /app

COPY shared/ /app/shared/
RUN pip install --no-cache-dir -e /app/shared

# PyTorch with CUDA 12.1 support — replaces the CPU-only wheel from the base Dockerfile.
RUN pip install --no-cache-dir torch torchvision \
    --index-url https://download.pytorch.org/whl/cu121

RUN pip install --no-cache-dir ultralytics shapely
# onnxruntime-gpu uses CUDA via the ONNX Runtime GPU execution provider.
RUN pip install --no-cache-dir onnxruntime-gpu insightface
RUN pip install --no-cache-dir paddlepaddle-gpu paddleocr

COPY ai-worker/ /app/ai-worker/
# Pre-download YOLOv8s weights so containers start without an internet fetch.
RUN python -c "from ultralytics import YOLO; YOLO('yolov8s.pt')"
RUN pip install --no-cache-dir redis "psycopg[binary]" "pydantic>=2.6,<3" prometheus-client opencv-python-headless
RUN pip install --no-cache-dir --no-deps -e /app/ai-worker

WORKDIR /app/ai-worker

EXPOSE 8001

CMD ["python", "-m", "worker.main"]
