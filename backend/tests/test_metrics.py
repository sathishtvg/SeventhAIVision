"""Confirms /metrics actually serves real Prometheus text format and that the
hand-rolled PrometheusMiddleware (app/core/metrics.py) records a request —
this is the regression test for the prometheus-fastapi-instrumentator
incompatibility discovered while building this (it crashed every request)."""


async def test_metrics_endpoint_serves_prometheus_format(app_client):
    await app_client.get("/health")  # generate at least one recorded request

    resp = await app_client.get("/metrics", follow_redirects=True)
    assert resp.status_code == 200
    body = resp.text
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body
    assert "ws_connections_active" in body
