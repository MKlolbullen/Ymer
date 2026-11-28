"""Gunicorn settings for the release image (packaging/docker/api.Dockerfile).

Every value can still be overridden on the command line or with GUNICORN_CMD_ARGS.
"""

import os
import shutil

bind = "0.0.0.0:5000"
worker_class = "gthread"
workers = int(os.environ.get("YMER_WEB_WORKERS", "2"))
threads = int(os.environ.get("YMER_WEB_THREADS", "8"))
# AI requests and Factory actions can take minutes.
timeout = 300
graceful_timeout = 30
forwarded_allow_ips = "*"
# With JSON logs Flask writes the access lines itself (ymer.observability), with the
# request id and timings, so gunicorn's own access log would only duplicate them.
accesslog = None if os.environ.get("YMER_LOG_FORMAT") == "json" else "-"

_metrics_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")


def on_starting(server):
    # Counters from a previous run of the container must not be added to this one.
    if _metrics_dir:
        shutil.rmtree(_metrics_dir, ignore_errors=True)
        os.makedirs(_metrics_dir, exist_ok=True)


def child_exit(server, worker):
    if _metrics_dir:
        from prometheus_client import multiprocess

        multiprocess.mark_process_dead(worker.pid)
