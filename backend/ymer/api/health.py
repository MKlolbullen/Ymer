"""Health endpoints.

- ``GET /api/health``: liveness. Always 200 while the process is up; touches nothing.
- ``GET /api/health/ready``: readiness. Checks Postgres, Redis and object storage
  and returns 503 if any of them is unreachable.
"""

import time
from collections.abc import Callable

from flask import Blueprint, current_app, jsonify
from sqlalchemy import text

import ymer
from ymer.app import settings_for
from ymer.db import get_engine
from ymer.extensions import get_redis, get_s3

health_bp = Blueprint("health", __name__, url_prefix="/health")


@health_bp.get("")
def liveness():
    return jsonify(status="ok", service="ymer-api", version=ymer.__version__)


def _check_database() -> None:
    with get_engine(current_app).connect() as conn:
        conn.execute(text("SELECT 1"))


def _check_redis() -> None:
    get_redis().ping()


def _check_storage() -> None:
    get_s3().head_bucket(Bucket=settings_for(current_app).s3_bucket)


CHECKS: dict[str, Callable[[], None]] = {
    "database": _check_database,
    "redis": _check_redis,
    "storage": _check_storage,
}


@health_bp.get("/ready")
def readiness():
    results: dict[str, dict] = {}
    for name, check in CHECKS.items():
        started = time.perf_counter()
        try:
            check()
            results[name] = {"status": "ok"}
        except Exception as exc:  # noqa: BLE001 - report any failure, never raise
            current_app.logger.warning("health check %s failed: %s", name, exc)
            results[name] = {"status": "error", "error": type(exc).__name__}
        results[name]["latency_ms"] = round((time.perf_counter() - started) * 1000, 1)

    healthy = all(r["status"] == "ok" for r in results.values())
    body = {"status": "ok" if healthy else "degraded", "checks": results}
    return jsonify(body), 200 if healthy else 503
