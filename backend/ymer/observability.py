"""Logs, request ids and Prometheus metrics for production.

- Every response carries an ``X-Request-ID`` (the incoming one when it looks sane, so the
  id nginx assigns follows the request through), and log lines written while handling a
  request include it.
- ``YMER_LOG_FORMAT=json`` writes one JSON object per line (for Loki, CloudWatch, journald
  and friends) and an access line per request from Flask, instead of gunicorn's.
- ``GET /metrics`` serves Prometheus metrics. It lives outside ``/api``, so the bundled
  nginx never forwards it: only services on the Docker network (Prometheus) reach it.
  Set ``YMER_METRICS_TOKEN`` to also require ``Authorization: Bearer <token>``.

Request counters and latencies are recorded per process. Under gunicorn with several
workers, set ``PROMETHEUS_MULTIPROC_DIR`` (the release image does) so every scrape adds
up all workers. Queue depths, dependency status and worker heartbeats are read fresh on
each scrape.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import UTC, datetime

from flask import Flask, Response, abort, current_app, g, has_request_context, request

HEARTBEAT_KEY = "ymer:heartbeat:{}"
WORKERS = ("ai-worker", "factory-worker")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

try:  # The metrics need prometheus-client; logs and request ids work without it.
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        REGISTRY,
        CollectorRegistry,
        Counter,
        Histogram,
        generate_latest,
    )
    from prometheus_client.core import GaugeMetricFamily

    REQUESTS = Counter(
        "ymer_http_requests_total",
        "HTTP requests handled by the API.",
        ["method", "endpoint", "status"],
    )
    LATENCY = Histogram(
        "ymer_http_request_duration_seconds",
        "Time spent handling API requests.",
        ["method", "endpoint"],
        buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300),
    )
except ImportError:  # pragma: no cover - the dependency is declared
    REQUESTS = LATENCY = None


# --- Logging ---------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message, request id, extras."""

    _skip = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None) or _current_request_id()
        if request_id:
            entry["request_id"] = request_id
        for key, value in vars(record).items():
            if key not in self._skip and key != "request_id" and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = _current_request_id() or "-"
        return True


def _current_request_id() -> str | None:
    return g.get("request_id") if has_request_context() else None


def configure_logging(log_format: str, level: str) -> None:
    """Send everything (Flask, gunicorn, libraries) to stderr in one format."""
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_RequestIdFilter())
    if log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] [%(request_id)s] %(message)s")
        )
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_ymer", False):
            root.removeHandler(existing)
    handler._ymer = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level.upper())
    # Flask's app logger and gunicorn's error log propagate to the root handler.
    for name in ("gunicorn.error",):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True


# --- Request hooks and /metrics --------------------------------------------------------


def init_observability(app: Flask, settings) -> None:
    if settings.env != "test":
        configure_logging(settings.log_format, settings.log_level)
    access_log = logging.getLogger("ymer.access")

    @app.before_request
    def _start() -> None:
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        g.request_started = time.perf_counter()

    @app.after_request
    def _finish(response: Response) -> Response:
        started = g.get("request_started")
        if started is None:
            return response
        elapsed = time.perf_counter() - started
        response.headers["X-Request-ID"] = g.request_id
        endpoint = request.url_rule.rule if request.url_rule else "unmatched"
        if REQUESTS is not None and endpoint != "/metrics":
            REQUESTS.labels(request.method, endpoint, str(response.status_code)).inc()
            LATENCY.labels(request.method, endpoint).observe(elapsed)
        if settings.log_format == "json" and not request.path.startswith("/api/health"):
            access_log.info(
                "%s %s %s",
                request.method,
                request.path,
                response.status_code,
                extra={
                    "method": request.method,
                    "path": request.path,
                    "endpoint": endpoint,
                    "status": response.status_code,
                    "duration_ms": round(elapsed * 1000, 1),
                    "bytes": response.calculate_content_length(),
                    "remote": request.remote_addr,
                    "user_agent": request.user_agent.string[:200],
                },
            )
        return response

    if settings.metrics_enabled and REQUESTS is not None:
        app.add_url_rule("/metrics", "metrics", _metrics_view)


def _metrics_view() -> Response:
    settings = current_app.extensions["ymer_settings"]
    if settings.metrics_token:
        given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(given.encode(), settings.metrics_token.encode()):
            abort(401)
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess

        base = CollectorRegistry()
        multiprocess.MultiProcessCollector(base)
    else:
        base = REGISTRY
    live = CollectorRegistry()
    live.register(_LiveCollector())
    body = generate_latest(base) + generate_latest(live)
    return Response(body, headers={"Content-Type": CONTENT_TYPE_LATEST})


class _LiveCollector:
    """State read on every scrape: version, dependencies, queues, workers, totals."""

    def collect(self):
        import ymer

        info = GaugeMetricFamily("ymer_build_info", "Ymer API build.", labels=["version"])
        info.add_metric([ymer.__version__], 1)
        yield info

        from ymer.api.health import CHECKS

        up = GaugeMetricFamily(
            "ymer_dependency_up", "1 when the API can reach the dependency.", labels=["dependency"]
        )
        for name, check in CHECKS.items():
            try:
                check()
                up.add_metric([name], 1)
            except Exception:  # noqa: BLE001 - a failed check is the measurement
                up.add_metric([name], 0)
        yield up

        yield from _queue_metrics()
        yield from _heartbeat_metrics()


def _queue_metrics():
    from sqlalchemy import func, select

    from ymer.db import db_session
    from ymer.models.ai import AiIndexJob
    from ymer.models.factory import FactoryCiRun, FactoryRun
    from ymer.models.user import User
    from ymer.models.workspace import Workspace

    try:
        depth, oldest = db_session.execute(
            select(func.count(), func.min(AiIndexJob.enqueued_at))
        ).one()
        runs = dict(
            db_session.execute(
                select(FactoryRun.status, func.count())
                .where(FactoryRun.status.in_(("queued", "running", "stopping")))
                .group_by(FactoryRun.status)
            ).all()
        )
        checks = dict(
            db_session.execute(
                select(FactoryCiRun.status, func.count())
                .where(FactoryCiRun.status.in_(("queued", "running")))
                .group_by(FactoryCiRun.status)
            ).all()
        )
        users = db_session.scalar(select(func.count()).select_from(User))
        workspaces = db_session.scalar(select(func.count()).select_from(Workspace))
    except Exception:  # noqa: BLE001 - the database being down shows in ymer_dependency_up
        db_session.rollback()
        return
    finally:
        db_session.remove()

    queue = GaugeMetricFamily("ymer_ai_index_queue_depth", "Pages waiting to be embedded.")
    queue.add_metric([], depth)
    yield queue
    age = GaugeMetricFamily(
        "ymer_ai_index_queue_oldest_seconds", "Age of the oldest page waiting to be embedded."
    )
    age.add_metric([], (datetime.now(UTC) - oldest).total_seconds() if oldest else 0)
    yield age
    builds = GaugeMetricFamily(
        "ymer_factory_runs", "Factory builds waiting or running.", labels=["status"]
    )
    for status in ("queued", "running", "stopping"):
        builds.add_metric([status], runs.get(status, 0))
    yield builds
    ci = GaugeMetricFamily(
        "ymer_factory_ci_runs", "Factory checks waiting or running.", labels=["status"]
    )
    for status in ("queued", "running"):
        ci.add_metric([status], checks.get(status, 0))
    yield ci
    totals = GaugeMetricFamily("ymer_accounts", "Accounts and workspaces.", labels=["kind"])
    totals.add_metric(["users"], users or 0)
    totals.add_metric(["workspaces"], workspaces or 0)
    yield totals


def _heartbeat_metrics():
    from ymer.extensions import get_redis

    family = GaugeMetricFamily(
        "ymer_worker_heartbeat_timestamp_seconds",
        "When each background worker last reported in (Unix time; 0 = never).",
        labels=["worker"],
    )
    try:
        values = get_redis().mget([HEARTBEAT_KEY.format(name) for name in WORKERS])
    except Exception:  # noqa: BLE001 - Redis being down shows in ymer_dependency_up
        return
    for name, value in zip(WORKERS, values, strict=True):
        family.add_metric([name], float(value or 0))
    yield family


def heartbeat(worker: str) -> None:
    """Record that a background worker is alive (called once per poll loop)."""
    from ymer.extensions import get_redis

    try:
        get_redis().set(HEARTBEAT_KEY.format(worker), f"{time.time():.0f}", ex=24 * 3600)
    except Exception:  # noqa: BLE001 - never stop a worker over monitoring
        logging.getLogger("ymer.worker").warning("heartbeat for %s failed", worker)
