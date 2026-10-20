# The footage worker (FOOTAGE_REVIEW.md): analyses the videos uploaded to
# Footage Review one at a time, and turns a typed description into a vector for
# the API's search. ONNX Runtime and ffmpeg only - no PyTorch - so it starts on
# a small machine beside the live stack.
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
