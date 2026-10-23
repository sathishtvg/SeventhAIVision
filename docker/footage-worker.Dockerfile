# The footage worker (FOOTAGE_REVIEW.md): analyses the videos uploaded to
# Footage Review one at a time, and turns a typed description into a vector for
# the API's search. ONNX Runtime and ffmpeg only - no PyTorch - so it starts on
# a small machine beside the live stack.

# ── The person and vehicle detector, made ONNX once, here ─────────────────────
# The same weights the live AI workers run (ai-worker/yolov8s.pt, Ultralytics
# YOLOv8s, AGPL-3.0 - see FOOTAGE_REVIEW.md), converted with no network needed.
# PyTorch is in this stage only. A machine that already has the AI workers'
# image can build from it and download nothing:
#   --build-arg DETECTOR_EXPORT_BASE=docker-ai-worker-behavior:latest
ARG DETECTOR_EXPORT_BASE=python:3.11-slim
FROM --platform=linux/amd64 ${DETECTOR_EXPORT_BASE} AS detector
USER root
RUN pip install --no-cache-dir "torch==2.12.1" --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir "ultralytics==8.4.75" "onnx==1.22.0"
COPY ai-worker/yolov8s.pt /export/yolov8s.pt
WORKDIR /export
RUN python -c "from ultralytics import YOLO; YOLO('yolov8s.pt').export(format='onnx', imgsz=640, simplify=False, dynamic=False, opset=17)"
# The pose model for the second look at a possible altercation: Ultralytics' own release, checked.
RUN python -c "import hashlib, urllib.request as u; d = u.urlopen('https://github.com/ultralytics/assets/releases/download/v8.4.0/yolov8s-pose.pt', timeout=300).read(); h = hashlib.sha256(d).hexdigest(); assert h == '234314cd8baf62616791aceb9ea6ad5c19f26cf6c0d8f3a1bfce1e23b186cfb3', h; open('yolov8s-pose.pt', 'wb').write(d)" \
    && python -c "from ultralytics import YOLO; YOLO('yolov8s-pose.pt').export(format='onnx', imgsz=640, simplify=False, dynamic=False, opset=17)"

# ── The worker ────────────────────────────────────────────────────────────────
FROM --platform=linux/amd64 python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/ai-worker

COPY ai-worker/requirements-footage.txt /app/ai-worker/requirements-footage.txt
RUN pip install --no-cache-dir -r /app/ai-worker/requirements-footage.txt

# The model is part of the image, pinned to one revision and checked against its
# published checksums: a changed file stops the build, and nothing is fetched
# when the container runs (a GCC network gives a container no internet).
COPY ai-worker/worker/__init__.py /app/ai-worker/worker/__init__.py
COPY ai-worker/worker/footage/__init__.py ai-worker/worker/footage/fetch_model.py /app/ai-worker/worker/footage/
RUN python -m worker.footage.fetch_model /opt/models/siglip2

COPY --from=detector /export/yolov8s.onnx /opt/models/yolov8s/yolov8s.onnx
COPY --from=detector /export/yolov8s-pose.onnx /opt/models/yolov8s-pose/yolov8s-pose.onnx

COPY ai-worker/worker/footage/ /app/ai-worker/worker/footage/
COPY ai-worker/worker/footage_main.py /app/ai-worker/worker/footage_main.py

ENV FOOTAGE_MODEL_DIR=/opt/models/siglip2 \
    PYTHONUNBUFFERED=1

EXPOSE 8090

# Non-root user — CIS Docker Benchmark 4.1
RUN useradd -m -u 1001 -s /bin/bash appuser
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=3)"

CMD ["python", "-m", "worker.footage_main"]
