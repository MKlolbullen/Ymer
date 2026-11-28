"""Request ids, JSON logs, worker heartbeats and the Prometheus /metrics endpoint."""

import json
import logging

import pytest

from tests.conftest import TEST_COLLAB_SECRET, TEST_SECRET_KEY
from ymer import create_app, observability
from ymer.api import health


@pytest.fixture
def healthy(monkeypatch):
    for name in health.CHECKS:
        monkeypatch.setitem(health.CHECKS, name, lambda: None)


def test_every_response_gets_a_request_id(client):
    resp = client.get("/api/health")
    generated = resp.headers["X-Request-ID"]
    assert len(generated) == 32
    resp = client.get("/api/health", headers={"X-Request-ID": "from-nginx-123"})
    assert resp.headers["X-Request-ID"] == "from-nginx-123"
    # Anything odd is replaced, so ids are safe to log.
    resp = client.get("/api/health", headers={"X-Request-ID": 'bad"id <x>'})
    assert resp.headers["X-Request-ID"] != 'bad"id <x>'


def test_metrics_counts_requests_by_route_and_status(client, healthy):
    client.get("/api/health")
    client.get("/api/nope")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/plain")
    text = resp.get_data(as_text=True)
    assert 'ymer_http_requests_total{endpoint="/api/health",method="GET",status="200"}' in text
    assert 'endpoint="unmatched",method="GET",status="404"' in text
    assert "ymer_http_request_duration_seconds_bucket" in text
    assert 'ymer_build_info{version="' in text
    assert 'ymer_dependency_up{dependency="redis"} 1.0' in text


def test_metrics_reports_a_failing_dependency(client, healthy, monkeypatch):
    def down():
        raise ConnectionError

    monkeypatch.setitem(health.CHECKS, "storage", down)
    text = client.get("/metrics").get_data(as_text=True)
    assert 'ymer_dependency_up{dependency="storage"} 0.0' in text


def test_metrics_token_is_enforced():
    app = create_app(
        {
            "env": "test",
            "secret_key": TEST_SECRET_KEY,
            "collab_secret": TEST_COLLAB_SECRET,
            "metrics_token": "scrape-me",
            "rate_limit_enabled": False,
        }
    )
    client = app.test_client()
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401
    ok = client.get("/metrics", headers={"Authorization": "Bearer scrape-me"})
    assert ok.status_code == 200
    app.extensions["sqlalchemy_engine"].dispose()


def test_metrics_can_be_turned_off():
    app = create_app(
        {
            "env": "test",
            "secret_key": TEST_SECRET_KEY,
            "collab_secret": TEST_COLLAB_SECRET,
            "metrics_enabled": False,
        }
    )
    assert app.test_client().get("/metrics").status_code == 404
    app.extensions["sqlalchemy_engine"].dispose()


def test_metrics_is_not_under_api(app):
    # The bundled nginx forwards /api/ only, so /metrics stays on the internal network.
    rules = {rule.rule for rule in app.url_map.iter_rules()}
    assert "/metrics" in rules
    assert "/api/metrics" not in rules


def test_json_formatter_includes_request_id_and_extras(app):
    record = logging.makeLogRecord(
        {"name": "ymer.test", "levelname": "INFO", "msg": "hello %s", "args": ("you",)}
    )
    record.status = 201
    with app.test_request_context("/"):
        from flask import g

        g.request_id = "abc"
        line = json.loads(observability.JsonFormatter().format(record))
    assert line["message"] == "hello you"
    assert line["level"] == "info"
    assert line["request_id"] == "abc"
    assert line["status"] == 201
    assert line["ts"].endswith("+00:00")


@pytest.mark.db
def test_metrics_reports_queues_and_heartbeats(app, client, healthy, db):
    with app.app_context():
        from ymer.extensions import get_redis

        get_redis().delete(observability.HEARTBEAT_KEY.format("ai-worker"))
        observability.heartbeat("factory-worker")
    text = client.get("/metrics").get_data(as_text=True)
    assert "ymer_ai_index_queue_depth " in text
    assert 'ymer_factory_runs{status="queued"}' in text
    assert 'ymer_accounts{kind="users"}' in text
    assert 'ymer_worker_heartbeat_timestamp_seconds{worker="ai-worker"} 0.0' in text
    line = next(
        row
        for row in text.splitlines()
        if row.startswith('ymer_worker_heartbeat_timestamp_seconds{worker="factory-worker"}')
    )
    assert float(line.split()[-1]) > 1_700_000_000
