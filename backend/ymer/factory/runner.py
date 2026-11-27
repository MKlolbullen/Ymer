"""Starting build runs and checks: in the worker process, or in a thread for development.

A build that passes is followed by its checks in the same thread, and checks that fail
with auto-fix on are followed by the fix build, and so on, until something passes, the
auto-fix limit is reached, or a run is stopped.
"""

import logging
import threading
import uuid

from flask import Flask

from ymer.app import settings_for
from ymer.factory.build import claim_next_run, execute_run
from ymer.factory.ci import after_build, claim_ci, claim_next_ci, execute_ci
from ymer.factory.sandbox import Sandbox, build_sandbox

log = logging.getLogger(__name__)


def sandbox_for(app: Flask) -> Sandbox | None:
    if "ymer_factory_sandbox" not in app.extensions:
        app.extensions["ymer_factory_sandbox"] = build_sandbox(settings_for(app))
    return app.extensions["ymer_factory_sandbox"]


def _claim_run(session, run_id: uuid.UUID) -> bool:
    from sqlalchemy import select

    from ymer.factory.build import now
    from ymer.models import FactoryRun, RunStatus

    run = session.scalars(
        select(FactoryRun)
        .where(FactoryRun.id == run_id, FactoryRun.status == RunStatus.QUEUED.value)
        .with_for_update(skip_locked=True)
    ).first()
    if run is None:
        session.rollback()
        return False
    run.status, run.started_at = RunStatus.RUNNING.value, now()
    session.commit()
    return True


def run_now(app: Flask, run_id: uuid.UUID) -> None:
    """Execute one build in this thread (the worker, an inline thread, or a test), then
    its checks, then any fix build the checks started."""
    from ymer.ai import get_ai
    from ymer.db import db_session

    with app.app_context():
        try:
            next_run: uuid.UUID | None = run_id
            while next_run is not None:
                execute_run(
                    db_session,
                    next_run,
                    chat=get_ai().chat,
                    sandbox=sandbox_for(app),
                    settings=settings_for(app),
                )
                ci_id = after_build(db_session, next_run)
                next_run = None
                if ci_id is not None and claim_ci(db_session, ci_id):
                    fix = execute_ci(
                        db_session, ci_id, sandbox=sandbox_for(app), settings=settings_for(app)
                    )
                    if fix is not None and _claim_run(db_session, fix):
                        log.info("Factory checks failed; fix build %s started", fix)
                        next_run = fix
        finally:
            db_session.remove()


def check_now(app: Flask, ci_id: uuid.UUID) -> None:
    """Run one queued check run in this thread, and the fix build it may start."""
    from ymer.db import db_session

    with app.app_context():
        try:
            if not claim_ci(db_session, ci_id):
                return
            fix = execute_ci(
                db_session, ci_id, sandbox=sandbox_for(app), settings=settings_for(app)
            )
            claimed = fix is not None and _claim_run(db_session, fix)
        finally:
            db_session.remove()
    if claimed:
        run_now(app, fix)


def start_checks_inline(app: Flask, ci_id: uuid.UUID) -> None:
    thread = threading.Thread(
        target=check_now, args=(app, ci_id), name=f"factory-checks-{ci_id}", daemon=True
    )
    thread.start()


def start_inline(app: Flask, run_id: uuid.UUID) -> None:
    """Development mode: run the build in a background thread of the web process."""
    thread = threading.Thread(
        target=run_now, args=(app, run_id), name=f"factory-run-{run_id}", daemon=True
    )
    thread.start()


def work_once(app: Flask) -> bool:
    """Claim and execute the next queued build, else the next queued checks. False when
    both queues were empty."""
    from ymer.db import db_session

    with app.app_context():
        try:
            run_id = claim_next_run(db_session)
            ci_id = None if run_id is not None else claim_next_ci(db_session)
        finally:
            db_session.remove()
    if run_id is not None:
        log.info("Factory run %s started", run_id)
        run_now(app, run_id)
        return True
    if ci_id is not None:
        log.info("Factory checks %s started", ci_id)
        _run_claimed_checks(app, ci_id)
        return True
    return False


def _run_claimed_checks(app: Flask, ci_id: uuid.UUID) -> None:
    from ymer.db import db_session

    with app.app_context():
        try:
            fix = execute_ci(
                db_session, ci_id, sandbox=sandbox_for(app), settings=settings_for(app)
            )
            claimed = fix is not None and _claim_run(db_session, fix)
        finally:
            db_session.remove()
    if claimed:
        run_now(app, fix)
