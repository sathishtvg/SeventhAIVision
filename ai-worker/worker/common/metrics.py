"""Shared Prometheus metrics across all 3 AI worker modules (plan §10). Workers
aren't HTTP apps, so each runs start_metrics_server() once at startup — a
minimal threaded HTTP server prometheus_client spins up internally, no
FastAPI/Starlette dependency needed in the worker codebase."""

from prometheus_client import Counter, Gauge, Histogram, start_http_server

frames_consumed_total = Counter(
    "frames_consumed_total", "Frames read from the frame_jobs stream", ["module_type"]
)
frames_processed_total = Counter(
    "frames_processed_total", "Frames successfully processed", ["module_type"]
)
frames_failed_total = Counter(
    "frames_failed_total", "Frames that raised during processing", ["module_type"]
)
processing_latency_seconds = Histogram(
    "processing_latency_seconds", "Time to process one frame end-to-end", ["module_type"]
)
detections_written_total = Counter(
    "detections_written_total", "Rows written to the detections table", ["module_type", "tenant_id"]
)
alerts_created_total = Counter(
    "alerts_created_total", "Rows written to the alerts table", ["module_type", "tenant_id"]
)
consumer_group_lag = Gauge(
    "consumer_group_lag", "XINFO GROUPS lag for this worker's consumer group", ["module_type"]
)


def start_metrics_server(port: int = 8001) -> None:
    start_http_server(port)
