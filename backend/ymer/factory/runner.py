"""Starting build runs: in the worker process, or in a thread for development."""

import logging
import threading
import uuid

from flask import Flask

from ymer.app import settings_for
from ymer.factory.build import claim_next_run, execute_run
from ymer.factory.sandbox import Sandbox, build_sandbox

log = logging.getLogger(__name__)


def sandbox_for(app: Flask) -> Sandbox | None:
    if "ymer_factory_sandbox" not in app.extensions:
        app.extensions["ymer_factory_sandbox"] = build_sandbox(settings_for(app))
    return app.extensions["ymer_factory_sandbox"]


def run_now(app: Flask, run_id: uuid.UUID) -> None:
    """Execute one run in this thread (the worker, an inline thread, or a test)."""
    from ymer.ai import get_ai
    from ymer.db import db_session

    with app.app_context():
        try:
            execute_run(
                db_session,
                run_id,
                chat=get_ai().chat,
                sandbox=sandbox_for(app),
                settings=settings_for(app),
            )
        finally:
            db_session.remove()


def start_inline(app: Flask, run_id: uuid.UUID) -> None:
    """Development mode: run the build in a background thread of the web process."""
    thread = threading.Thread(
        target=run_now, args=(app, run_id), name=f"factory-run-{run_id}", daemon=True
    )
    thread.start()


def work_once(app: Flask) -> bool:
    """Claim and execute the next queued run. False when the queue was empty."""
    from ymer.db import db_session

    with app.app_context():
        try:
            run_id = claim_next_run(db_session)
        finally:
            db_session.remove()
    if run_id is None:
        return False
    log.info("Factory run %s started", run_id)
    run_now(app, run_id)
    return True
