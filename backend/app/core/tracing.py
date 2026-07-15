"""Gap 14 — OpenTelemetry distributed tracing setup for Seventh AI Vision.

Call setup_tracing(app) once from the FastAPI lifespan function (main.py).
When OTEL_ENABLED=false or when the opentelemetry packages are absent, every
function is a safe no-op — no import errors, no runtime failures.

Usage in main.py:
    from app.core.tracing import setup_tracing
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_tracing(app)
        yield

Environment variables:
    OTEL_ENABLED                  Enable tracing (default: true)
    OTEL_EXPORTER_OTLP_ENDPOINT   Collector gRPC endpoint (default: http://otel-collector:4317)
    OTEL_SERVICE_NAME             Service name in trace UI (default: seventh-ai-vision-api)
    OTEL_SERVICE_VERSION          Release version tag (default: 1.0.0)
    OTEL_TRACES_SAMPLER_ARG       Head-sampling ratio 0.0-1.0 (default: 1.0 = 100%)
    DEPLOYMENT_ENV                Resource attribute (default: development)
"""

from __future__ import annotations

import os

# Conditional import: tracing.py can be imported by test_opentelemetry.py
# even when opentelemetry packages are not installed in the API container.
# (Same pattern as locustfile.py — Gap 13.)
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.propagators.b3 import B3MultiFormat
    from opentelemetry.propagators.composite import CompositePropagator
    from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    _OTEL_AVAILABLE = True
except ImportError:
    _OTEL_AVAILABLE = False
    trace = None  # type: ignore[assignment]

# ── Configuration ─────────────────────────────────────────────────────────────
OTEL_ENABLED: bool = os.getenv("OTEL_ENABLED", "true").lower() not in ("false", "0", "no")
OTEL_ENDPOINT: str = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
OTEL_SERVICE_NAME: str = os.getenv("OTEL_SERVICE_NAME", "seventh-ai-vision-api")
OTEL_SERVICE_VERSION: str = os.getenv("OTEL_SERVICE_VERSION", "1.0.0")
OTEL_SAMPLE_RATIO: float = float(os.getenv("OTEL_TRACES_SAMPLER_ARG", "1.0"))
DEPLOYMENT_ENV: str = os.getenv("DEPLOYMENT_ENV", "development")


def setup_tracing(app=None):
    """Initialize OpenTelemetry tracing.

    Returns the TracerProvider on success, or None when tracing is disabled
    or the opentelemetry packages are not installed.
    """
    if not _OTEL_AVAILABLE or not OTEL_ENABLED:
        return None

    resource = Resource(attributes={
        SERVICE_NAME: OTEL_SERVICE_NAME,
        SERVICE_VERSION: OTEL_SERVICE_VERSION,
        "deployment.environment": DEPLOYMENT_ENV,
    })

    sampler = ParentBased(root=TraceIdRatioBased(OTEL_SAMPLE_RATIO))
    provider = TracerProvider(resource=resource, sampler=sampler)

    otlp_exporter = OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))

    trace.set_tracer_provider(provider)

    # W3C TraceContext (primary) + B3 multi-format (Zipkin/Jaeger interop)
    set_global_textmap(CompositePropagator([
        TraceContextTextMapPropagator(),
        B3MultiFormat(),
    ]))

    if app is not None:
        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls="health,metrics",
            http_capture_headers_server_request=["X-Tenant-ID", "X-Request-ID"],
        )
        SQLAlchemyInstrumentor().instrument(enable_commenter=True)
        RedisInstrumentor().instrument()

    return provider


def get_tracer(name: str = __name__) -> "trace.Tracer":  # type: ignore[name-defined]
    """Return a named tracer for manual span creation in application code."""
    if not _OTEL_AVAILABLE or trace is None:
        return None  # type: ignore[return-value]
    return trace.get_tracer(name)
