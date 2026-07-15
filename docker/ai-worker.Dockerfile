# One image, run as 3 separate Compose services (ai-worker-lpr, ai-worker-face,
# ai-worker-intrusion) differing only by the WORKER_MODULE env var (plan §6).
FROM --platform=linux/amd64 python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        ffmpeg \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY shared/ /app/shared/
RUN pip install --no-cache-dir -e /app/shared

# Each heavy ML dependency gets its own layer so a build interruption only
# costs that one package's download, not every package after it too —
# these are large (torch, paddlepaddle especially) and slow on a typical
# connection.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir ultralytics shapely
RUN pip install --no-cache-dir onnxruntime insightface
RUN pip install --no-cache-dir paddlepaddle paddleocr

COPY ai-worker/ /app/ai-worker/
# Pre-download YOLOv8s weights at build time so a fresh container never
# fetches from the internet mid-test (cold-start model download takes >15s
# and would cause section_intrusion to time out on a first-ever stack start).
RUN python -c "from ultralytics import YOLO; YOLO('yolov8s.pt')"
# Install lightweight non-ML deps that --no-deps below would skip.
RUN pip install --no-cache-dir redis "psycopg[binary]" "pydantic>=2.6,<3" prometheus-client opencv-python-headless
RUN pip install --no-cache-dir --no-deps -e /app/ai-worker

WORKDIR /app/ai-worker

EXPOSE 8001

# Non-root user — CIS Docker Benchmark 4.1 (Gap 30)
RUN useradd -m -u 1001 -s /bin/bash appuser
USER appuser

CMD ["python", "-m", "worker.main"]
