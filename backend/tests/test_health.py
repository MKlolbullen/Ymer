import pytest

from ymer.api import health


def test_liveness(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["service"] == "ymer-api"


def test_readiness_all_ok(client, monkeypatch):
    for name in health.CHECKS:
        monkeypatch.setitem(health.CHECKS, name, lambda: None)
    resp = client.get("/api/health/ready")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert set(body["checks"]) == {"database", "redis", "storage"}


def test_readiness_reports_failure(client, monkeypatch):
    def boom():
        raise ConnectionError("down")

    for name in health.CHECKS:
        monkeypatch.setitem(health.CHECKS, name, lambda: None)
    monkeypatch.setitem(health.CHECKS, "redis", boom)

    resp = client.get("/api/health/ready")
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["status"] == "degraded"
    assert body["checks"]["redis"] == {
        "status": "error",
        "error": "ConnectionError",
        "latency_ms": body["checks"]["redis"]["latency_ms"],
    }
    assert body["checks"]["database"]["status"] == "ok"


def test_unknown_route_returns_json(client):
    resp = client.get("/api/nope")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "Not Found"


@pytest.mark.db
def test_database_check_against_postgres(app):
    with app.app_context():
        health._check_database()
