"""Checks (CI) for apps the Factory built: run, record, gate and repair.

After every passed build, and whenever an editor asks, the app's current files are
checked in the build sandbox: lint, build, tests and a smoke run, each one shell script
from ``ymer.factory.ci_scripts`` run in a fresh sandbox (``execute_ci``). Every step
runs even when an earlier one fails, so a failure report is complete.

What happens with the result:

- **History.** Each run is a row in a "Checks" database under the factory page (status,
  trigger, date, each step, seconds), with a chart view of passes and failures per day.
  It is an ordinary Ymer database, so people can filter it, chart it and link to it.
- **Gate.** With ``YMER_FACTORY_CI_GATE`` on (the default), a release needs a passed
  check run on exactly the files it would ship (``release_blocker``).
- **Repair.** A failure opens (or refreshes) a change request of origin ``ci`` and adds a
  fix task, with the failing steps' output, to the Tasks database. The next build works
  on it like any other planned change. With auto-fix on for the factory, that build is
  started straight away, up to ``YMER_FACTORY_CI_MAX_AUTO_FIXES`` times in a row; a pass
  resets the count, so a person steps in when repairs keep failing.
"""

import hashlib
import logging
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ymer.ai.generate import add_rows, create_database
from ymer.charts import ChartConfig
from ymer.config import Settings
from ymer.databases import properties_of
from ymer.factory.budget import remaining
from ymer.factory.build import DONE, log_event, read_tasks
from ymer.factory.changes import append_tasks, next_number
from ymer.factory.ci_scripts import STEPS, ci_files, step_command
from ymer.factory.releases import current_files
from ymer.factory.sandbox import Sandbox, SandboxError, clip
from ymer.factory.stacks import STACKS
from ymer.models import (
    ACTIVE_RUN_STATUSES,
    ChangeOrigin,
    ChangeStatus,
    CiStatus,
    CiTrigger,
    DatabaseRow,
    DatabaseView,
    FactoryChange,
    FactoryCiRun,
    FactoryProject,
    FactoryRun,
    FactoryStage,
    Page,
    PageType,
    PropertyType,
    RunStatus,
    User,
    ViewType,
    Workspace,
)

log = logging.getLogger(__name__)

STEP_OUTPUT = 8_000  # characters of each step's output kept
FIX_OUTPUT = 2_500  # characters of each failing step's output put in the fix task
ACTIVE_CI = (CiStatus.QUEUED.value, CiStatus.RUNNING.value)
HISTORY_RUNS = 20
CHECKS_TITLE = "Checks"


def now() -> datetime:
    return datetime.now(UTC)


def files_hash(files: dict[str, str]) -> str:
    """Identifies a set of files: the same paths with the same contents hash the same."""
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(files[path].encode()).digest())
    return digest.hexdigest()


# --- queueing ---------------------------------------------------------------------------


def queue_ci(
    session: Session,
    project: FactoryProject,
    *,
    trigger: CiTrigger,
    run_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> FactoryCiRun:
    last = session.scalar(
        select(func.max(FactoryCiRun.number)).where(FactoryCiRun.factory_id == project.page_id)
    )
    ci = FactoryCiRun(
        factory_id=project.page_id,
        number=(last or 0) + 1,
        status=CiStatus.QUEUED.value,
        trigger=trigger.value,
        run_id=run_id,
        stack=project.stack if project.stack in STACKS else "flask-react",
        steps=[],
        started_by_id=user_id,
    )
    session.add(ci)
    session.flush()
    return ci


def claim_ci(session: Session, ci_id: uuid.UUID) -> bool:
    """Move one queued check run to running. False when someone else already took it."""
    ci = session.scalars(
        select(FactoryCiRun)
        .where(FactoryCiRun.id == ci_id, FactoryCiRun.status == CiStatus.QUEUED.value)
        .with_for_update(skip_locked=True)
    ).first()
    if ci is None:
        session.rollback()
        return False
    ci.status, ci.started_at = CiStatus.RUNNING.value, now()
    session.commit()
    return True


def claim_next_ci(session: Session) -> uuid.UUID | None:
    ci = session.scalars(
        select(FactoryCiRun)
        .where(FactoryCiRun.status == CiStatus.QUEUED.value)
        .order_by(FactoryCiRun.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if ci is None:
        session.rollback()
        return None
    ci.status, ci.started_at = CiStatus.RUNNING.value, now()
    session.commit()
    return ci.id


def after_build(session: Session, run_id: uuid.UUID) -> uuid.UUID | None:
    """Queue the checks for a build that passed. Returns the check run's id."""
    run = session.get(FactoryRun, run_id)
    if run is None or run.status != RunStatus.PASSED.value:
        return None
    project = session.get(FactoryProject, run.factory_id)
    if project is None:
        return None
    ci = queue_ci(
        session, project, trigger=CiTrigger.BUILD, run_id=run.id, user_id=run.started_by_id
    )
    session.commit()
    return ci.id


# --- running ----------------------------------------------------------------------------


def execute_ci(
    session: Session, ci_id: uuid.UUID, *, sandbox: Sandbox | None, settings: Settings
) -> uuid.UUID | None:
    """Run every step, record the result, and open a fix on failure.

    Returns the id of a fix build it queued (auto-fix), or None."""
    ci = session.get(FactoryCiRun, ci_id)
    if ci is None or ci.status not in ACTIVE_CI:
        return None
    if ci.status == CiStatus.QUEUED.value:
        ci.status, ci.started_at = CiStatus.RUNNING.value, now()
        session.commit()
    project = session.get(FactoryProject, ci.factory_id)
    files = current_files(session, ci.factory_id)
    ci.files_hash, ci.file_count = files_hash(files), len(files)
    stack = STACKS.get(ci.stack, STACKS["flask-react"])
    status, summary = CiStatus.PASSED, None
    try:
        if sandbox is None:
            raise SandboxError("Checks are turned off on this server (YMER_FACTORY_SANDBOX=off).")
        if not files:
            raise SandboxError("There are no files to check yet. Build the app first.")
        ci.sandbox = sandbox.name
        snapshot = {**files, **ci_files(stack.key)}
        steps: list[dict] = []
        for key, name in STEPS:
            command = step_command(key)
            started = time.monotonic()
            result = sandbox.run(
                snapshot, command, image=stack.image, timeout=settings.factory_command_timeout
            )
            ci.simulated = ci.simulated or result.simulated
            steps.append(
                {
                    "key": key,
                    "name": name,
                    "command": command,
                    "status": "passed" if result.ok else "failed",
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                    "seconds": round(time.monotonic() - started, 1),
                    "output": clip(result.output, STEP_OUTPUT),
                }
            )
            ci.steps = list(steps)  # a new list, so the JSON column is saved
            session.commit()  # the page follows along step by step
        failed = [s["name"] for s in steps if s["status"] != "passed"]
        if failed:
            status = CiStatus.FAILED
            summary = f"Failed: {', '.join(failed)}."
        else:
            summary = f"All {len(steps)} checks passed."
        if ci.simulated:
            summary += " (Demo sandbox: the files were checked, nothing ran.)"
    except SandboxError as exc:
        status, summary = CiStatus.ERROR, str(exc)
    except Exception:
        log.exception("Factory checks %s crashed", ci_id)
        status, summary = CiStatus.ERROR, "The checks crashed. The server log has the details."
        session.rollback()
        ci = session.get(FactoryCiRun, ci_id)
        project = session.get(FactoryProject, ci.factory_id)
    ci.status, ci.summary, ci.finished_at = status.value, summary, now()
    session.commit()

    fix_run_id = None
    if project is not None:
        try:
            with session.begin_nested():
                record_history(session, project, ci)
        except Exception:
            log.exception("Could not add checks %s to the history database", ci_id)
        if status is CiStatus.PASSED:
            project.ci_fix_streak = 0
        elif status is CiStatus.FAILED:
            open_fix(session, project, ci)
            fix_run_id = auto_fix(session, project, ci, settings)
        session.commit()
    if ci.run_id is not None:
        build = session.get(FactoryRun, ci.run_id)
        if build is not None:
            title = {
                CiStatus.PASSED: f"Checks #{ci.number} passed",
                CiStatus.FAILED: f"Checks #{ci.number} failed",
            }.get(status, f"Checks #{ci.number} could not run")
            log_event(session, build, "checks", title, summary)
    return fix_run_id


# --- repair -------------------------------------------------------------------------------


def fix_detail(ci: FactoryCiRun) -> str:
    parts = [
        f"Checks #{ci.number} failed. Make every check pass again without weakening it. "
        "Run a check yourself with run_command, e.g. `sh .ymer-ci/lint.sh`."
    ]
    for step in ci.steps:
        if step["status"] == "passed":
            continue
        output = step.get("output") or ""
        if len(output) > FIX_OUTPUT:
            output = "[... earlier output cut ...]\n" + output[-FIX_OUTPUT:]
        parts.append(f"{step['name']} failed (`{step['command']}`):\n{output.strip()}")
    return "\n\n".join(parts)[:8000]


def _set_task_detail(session: Session, tasks_page_id: uuid.UUID, row_id: str, text: str) -> None:
    prop = next((p for p in properties_of(session, tasks_page_id) if p.name == "Detail"), None)
    row = session.get(DatabaseRow, uuid.UUID(row_id))
    if prop is not None and row is not None:
        row.values = {**row.values, str(prop.id): text[:10_000]}


def open_fix(session: Session, project: FactoryProject, ci: FactoryCiRun) -> FactoryChange:
    """Open (or refresh) the change request for failing checks, and its fix task."""
    failed = [s["name"] for s in ci.steps if s["status"] != "passed"]
    title = f"Fix failing checks: {', '.join(failed)}"[:200]
    detail = fix_detail(ci)
    change = session.scalars(
        select(FactoryChange)
        .where(
            FactoryChange.factory_id == project.page_id,
            FactoryChange.origin == ChangeOrigin.CI.value,
            FactoryChange.status.in_((ChangeStatus.OPEN.value, ChangeStatus.PLANNED.value)),
        )
        .order_by(FactoryChange.number.desc())
        .limit(1)
    ).first()
    if change is None:
        change = FactoryChange(
            factory_id=project.page_id,
            number=next_number(session, project.page_id),
            title=title,
            detail=detail,
            origin=ChangeOrigin.CI.value,
            status=ChangeStatus.OPEN.value,
            tasks=[],
            created_by_id=ci.started_by_id,
        )
        session.add(change)
        session.flush()
    else:
        change.title, change.detail = title, detail
    ci.change_id = change.id

    tasks_page = session.get(Page, project.tasks_page_id) if project.tasks_page_id else None
    if tasks_page is None:
        return change  # stays open; planning it needs a Tasks database
    tasks = read_tasks(session, tasks_page.id)
    pending = [t for t in tasks if t["title"] in change.tasks and t["status"] != DONE]
    if pending:
        # The fix task from an earlier failure is not built yet: give it the new log.
        _set_task_detail(
            session, tasks_page.id, pending[0]["row_id"], f"Change #{change.number}. {detail}"
        )
    else:
        task_title = f"Fix failing checks (checks #{ci.number})"
        append_tasks(
            session,
            tasks_page,
            [
                {
                    "title": task_title,
                    "component": "",
                    "priority": "must",
                    "size": "S",
                    "detail": detail,
                    "depends_on": [],
                    "change": change.number,
                }
            ],
            user_id=ci.started_by_id or tasks_page.created_by_id,
        )
        change.tasks = [*change.tasks, task_title]
    change.status, change.planned_at = ChangeStatus.PLANNED.value, now()
    if project.stage in (FactoryStage.BUILT.value, FactoryStage.RELEASED.value):
        project.stage = FactoryStage.PLANNED.value
    session.flush()
    return change


def auto_fix(
    session: Session, project: FactoryProject, ci: FactoryCiRun, settings: Settings
) -> uuid.UUID | None:
    """Queue a build of the fix task, when the factory allows it. Returns the build's id."""
    if not project.ci_auto_fix or project.tasks_page_id is None:
        return None

    def note(text: str) -> None:
        ci.summary = f"{ci.summary or ''} {text}".strip()

    if project.ci_fix_streak >= settings.factory_ci_max_auto_fixes:
        note(
            f"Auto-fix paused after {project.ci_fix_streak} tries in a row; "
            "build the fix task yourself."
        )
        return None
    if session.scalar(
        select(func.count()).where(
            FactoryRun.factory_id == project.page_id, FactoryRun.status.in_(ACTIVE_RUN_STATUSES)
        )
    ):
        return None
    template = session.get(FactoryRun, ci.run_id) if ci.run_id else None
    if template is None:
        template = session.scalars(
            select(FactoryRun)
            .where(FactoryRun.factory_id == project.page_id)
            .order_by(FactoryRun.created_at.desc())
            .limit(1)
        ).first()
    if template is None:
        return None
    page = session.get(Page, project.page_id)
    workspace = session.get(Workspace, page.workspace_id) if page else None
    left = remaining(session, workspace, settings) if workspace else None
    if left is not None and left <= 0:
        note("Auto-fix skipped: the workspace's monthly Factory budget is used up.")
        return None
    tasks = read_tasks(session, project.tasks_page_id)
    keep = ("app", "stack", "brief", "architecture", "sandbox", "model", "personal_provider")
    context = {k: template.context[k] for k in keep if k in template.context}
    context |= {"stack": project.stack or context.get("stack"), "tasks": tasks}
    context["auto_fix"] = ci.number
    if left is not None:
        context["max_tokens"] = left
    run = FactoryRun(
        factory_id=project.page_id,
        status=RunStatus.QUEUED.value,
        started_by_id=template.started_by_id,
        context=context,
        tasks_total=len(tasks),
        tasks_done=sum(t["status"] == DONE for t in tasks),
    )
    session.add(run)
    session.flush()
    project.ci_fix_streak += 1
    ci.fix_run_id = run.id
    note(f"Auto-fix started a build (try {project.ci_fix_streak}).")
    return run.id


# --- history database ------------------------------------------------------------------

STEP_OPTIONS = ["Passed", "Failed", "Not run"]
COLORS = {"Passed": "green", "Failed": "red", "Error": "orange", "Not run": "gray"}
TRIGGERS = {CiTrigger.BUILD.value: "After build", CiTrigger.MANUAL.value: "Manual"}


def history_spec() -> dict:
    return {
        "properties": [
            {"name": "Run", "type": "title"},
            {"name": "Status", "type": "select", "options": ["Passed", "Failed", "Error"]},
            {"name": "Trigger", "type": "select", "options": list(TRIGGERS.values())},
            {"name": "Date", "type": "date"},
            *({"name": name, "type": "select", "options": STEP_OPTIONS} for _k, name in STEPS),
            {"name": "Seconds", "type": "number"},
            {"name": "Files", "type": "number"},
            {"name": "Summary", "type": "text"},
        ],
        "rows": [],
    }


def _history_page(session: Session, project: FactoryProject, user_id: uuid.UUID | None) -> Page:
    page = session.get(Page, project.checks_page_id) if project.checks_page_id else None
    if page is not None:
        return page
    factory = session.get(Page, project.page_id)
    page = Page(
        workspace_id=factory.workspace_id,
        parent_id=factory.id,
        type=PageType.DATABASE,
        title=CHECKS_TITLE,
        position=4,
        created_by_id=user_id or factory.created_by_id,
    )
    session.add(page)
    session.flush()
    create_database(session, page, history_spec(), user_id=user_id or factory.created_by_id)
    props = {p.name: p for p in properties_of(session, page.id)}
    for prop in props.values():
        if prop.type is PropertyType.SELECT:
            prop.config = {
                **prop.config,
                "options": [
                    {**o, "color": COLORS.get(o["name"], o.get("color"))}
                    for o in prop.config.get("options", [])
                ],
            }
    chart = ChartConfig(
        kind="bar", x=props["Date"].id, date_bucket="day", split_by=props["Status"].id
    )
    session.add(
        DatabaseView(
            workspace_id=page.workspace_id,
            database_id=page.id,
            name="Passes per day",
            type=ViewType.CHART,
            config={"chart": chart.model_dump(mode="json")},
            position=2,
        )
    )
    project.checks_page_id = page.id
    session.flush()
    return page


def record_history(session: Session, project: FactoryProject, ci: FactoryCiRun) -> None:
    page = _history_page(session, project, ci.started_by_id)
    by_key = {s["key"]: s for s in ci.steps}
    seconds = round(sum(s.get("seconds") or 0 for s in ci.steps), 1)
    values = [
        {"property": "Run", "value": f"Checks #{ci.number}"},
        {"property": "Status", "value": ci.status.capitalize()},
        {"property": "Trigger", "value": TRIGGERS.get(ci.trigger, ci.trigger)},
        {"property": "Date", "value": (ci.finished_at or now()).isoformat()},
        {"property": "Seconds", "value": str(seconds)},
        {"property": "Files", "value": str(ci.file_count)},
        {"property": "Summary", "value": ci.summary or ""},
    ]
    for key, name in STEPS:
        step = by_key.get(key)
        values.append(
            {"property": name, "value": step["status"].capitalize() if step else "Not run"}
        )
    first = session.scalar(
        select(func.min(DatabaseRow.position)).where(DatabaseRow.database_id == page.id)
    )
    # Newest first: each run goes above the ones before it.
    add_rows(
        session,
        page,
        list(properties_of(session, page.id)),
        [{"values": values}],
        user_id=ci.started_by_id or page.created_by_id,
        start=(first if first is not None else 1) - 1,
    )
    from ymer.ai.indexing import mark_dirty

    mark_dirty(session, [page.id])


# --- reading ------------------------------------------------------------------------------


def latest_ci(session: Session, factory_id: uuid.UUID) -> FactoryCiRun | None:
    return session.scalars(
        select(FactoryCiRun)
        .where(FactoryCiRun.factory_id == factory_id)
        .order_by(FactoryCiRun.number.desc())
        .limit(1)
    ).first()


def release_blocker(session: Session, project: FactoryProject, files: dict[str, str]) -> str | None:
    """Why ``files`` cannot be released yet, or None when the checks passed on them."""
    latest = latest_ci(session, project.page_id)
    if latest is not None and latest.status in ACTIVE_CI:
        return "The checks are still running. Release when they have finished."
    wanted = files_hash(files)
    ci = session.scalars(
        select(FactoryCiRun)
        .where(FactoryCiRun.factory_id == project.page_id, FactoryCiRun.files_hash == wanted)
        .order_by(FactoryCiRun.number.desc())
        .limit(1)
    ).first()
    if ci is None:
        return "Run the checks on this build before releasing it."
    if ci.status == CiStatus.FAILED.value:
        failed = ", ".join(s["name"] for s in ci.steps if s["status"] != "passed")
        return (
            f"The checks failed on this build (checks #{ci.number}: {failed}). "
            "Build the fix task, then release."
        )
    if ci.status != CiStatus.PASSED.value:
        return f"The checks could not run on this build (checks #{ci.number}). Run them again."
    return None


def _person(session: Session, user_id: uuid.UUID | None) -> dict | None:
    user = session.get(User, user_id) if user_id else None
    return {"id": str(user.id), "display_name": user.display_name} if user else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def ci_json(session: Session, ci: FactoryCiRun, *, output: bool = False) -> dict:
    change = session.get(FactoryChange, ci.change_id) if ci.change_id else None
    keys = ("key", "name", "command", "status", "exit_code", "timed_out", "seconds")
    return {
        "id": str(ci.id),
        "number": ci.number,
        "status": ci.status,
        "trigger": ci.trigger,
        "run_id": str(ci.run_id) if ci.run_id else None,
        "stack": ci.stack,
        "sandbox": ci.sandbox,
        "simulated": ci.simulated,
        "summary": ci.summary,
        "file_count": ci.file_count,
        "steps": [
            {k: s.get(k) for k in keys} | ({"output": s.get("output")} if output else {})
            for s in ci.steps
        ],
        "change": {"number": change.number, "title": change.title, "status": change.status}
        if change
        else None,
        "fix_run_id": str(ci.fix_run_id) if ci.fix_run_id else None,
        "started_by": _person(session, ci.started_by_id),
        "created_at": _iso(ci.created_at),
        "started_at": _iso(ci.started_at),
        "finished_at": _iso(ci.finished_at),
    }


def checks_json(session: Session, project: FactoryProject, settings: Settings) -> dict:
    recent = session.scalars(
        select(FactoryCiRun)
        .where(FactoryCiRun.factory_id == project.page_id)
        .order_by(FactoryCiRun.number.desc())
        .limit(HISTORY_RUNS)
    ).all()
    finished = [c for c in recent if c.status in (CiStatus.PASSED.value, CiStatus.FAILED.value)]
    passed = sum(c.status == CiStatus.PASSED.value for c in finished)
    files = current_files(session, project.page_id)
    return {
        "latest": ci_json(session, recent[0], output=True) if recent else None,
        "recent": [
            {"number": c.number, "status": c.status, "created_at": _iso(c.created_at)}
            for c in recent
        ],
        "pass_rate": round(passed / len(finished), 3) if finished else None,
        "page_id": str(project.checks_page_id) if project.checks_page_id else None,
        "auto_fix": project.ci_auto_fix,
        "fix_streak": project.ci_fix_streak,
        "max_auto_fixes": settings.factory_ci_max_auto_fixes,
        "gate": settings.factory_ci_gate,
        "release_blocker": release_blocker(session, project, files) if files else None,
    }
