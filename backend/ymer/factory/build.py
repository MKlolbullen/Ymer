"""Build runs: the agent works through the Tasks database, one task at a time.

A run is queued by an editor (``queue_run``), which captures what the build works from
with that editor's access: the stack, the approved brief and architecture text, and the
tasks. A worker (``flask factory-worker``, or a thread with ``YMER_FACTORY_RUN_INLINE``)
then executes it (``execute_run``):

1. pick the next task that is not Done and whose dependencies are Done,
2. mark it In progress in the Tasks database and start a fresh agent conversation,
3. run the agent's tool calls (files are saved as they are written; commands run in the
   sandbox) until it calls ``finish_task``,
4. mark the task Done and carry its summary into the next task's notes.

The run stops at the first task that is blocked or out of steps, when a budget runs out,
or when someone presses Stop (checked between steps). Every step is committed and logged
as a ``FactoryRunEvent``, so the page can follow along while the run works.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ymer.ai.providers import AiError, ChatProvider
from ymer.config import Settings
from ymer.databases import properties_of
from ymer.factory.agent import AgentSession, ToolCall, ToolResult, start_session
from ymer.factory.sandbox import Sandbox, SandboxError, clip
from ymer.factory.sandbox.docker import safe_path
from ymer.factory.stacks import STACKS
from ymer.models import (
    DatabaseProperty,
    DatabaseRow,
    FactoryFile,
    FactoryProject,
    FactoryRun,
    FactoryRunEvent,
    FactoryStage,
    PropertyType,
    RunStatus,
)

log = logging.getLogger(__name__)

MAX_FILE_CHARS = 200_000
MAX_FILES = 300
MAX_CONTEXT_CHARS = 60_000
DONE, IN_PROGRESS, TO_DO = "Done", "In progress", "To do"


def now() -> datetime:
    return datetime.now(UTC)


# --- the Tasks database ----------------------------------------------------------------


def _props(session: Session, tasks_page_id: uuid.UUID) -> dict[str, DatabaseProperty]:
    return {p.name: p for p in properties_of(session, tasks_page_id)}


def _option_name(prop: DatabaseProperty | None, value: Any) -> str:
    if prop is None or value is None:
        return ""
    for option in prop.config.get("options", []):
        if option["id"] == value:
            return option["name"]
    return ""


def read_tasks(session: Session, tasks_page_id: uuid.UUID) -> list[dict]:
    """The rows of the Tasks database, in order, as plain dicts."""
    props = _props(session, tasks_page_id)
    title = next((p for p in props.values() if p.type is PropertyType.TITLE), None)
    rows = session.scalars(
        select(DatabaseRow)
        .where(DatabaseRow.database_id == tasks_page_id)
        .order_by(DatabaseRow.position)
    ).all()

    def text(row: DatabaseRow, name: str) -> str:
        prop = props.get(name)
        if prop is None:
            return ""
        value = row.values.get(str(prop.id))
        if prop.type is PropertyType.SELECT:
            return _option_name(prop, value)
        return str(value or "")

    tasks = []
    for row in rows:
        name = str(row.values.get(str(title.id)) or "").strip() if title else ""
        if not name:
            continue
        depends = text(row, "Depends on")
        tasks.append(
            {
                "row_id": str(row.id),
                "title": name,
                "status": text(row, "Status") or TO_DO,
                "component": text(row, "Component"),
                "priority": text(row, "Priority"),
                "size": text(row, "Size"),
                "detail": text(row, "Detail"),
                "depends_on": [d.strip() for d in depends.split(";") if d.strip()],
            }
        )
    return tasks


def set_task_status(
    session: Session, tasks_page_id: uuid.UUID | None, row_id: str, status: str
) -> None:
    """Move a task's Status select; quietly does nothing if the row or option is gone."""
    if tasks_page_id is None:
        return
    prop = _props(session, tasks_page_id).get("Status")
    row = session.get(DatabaseRow, uuid.UUID(row_id))
    if prop is None or row is None or row.database_id != tasks_page_id:
        return
    option = next((o for o in prop.config.get("options", []) if o["name"] == status), None)
    if option is None:
        return
    row.values = {**row.values, str(prop.id): option["id"]}


# --- queueing ---------------------------------------------------------------------------


def queue_run(
    session: Session,
    project: FactoryProject,
    *,
    title: str,
    brief_text: str,
    architecture_text: str,
    user_id: uuid.UUID,
) -> FactoryRun:
    tasks = read_tasks(session, project.tasks_page_id) if project.tasks_page_id else []
    run = FactoryRun(
        factory_id=project.page_id,
        status=RunStatus.QUEUED.value,
        started_by_id=user_id,
        context={
            "app": title,
            "stack": project.stack or "flask-react",
            "brief": brief_text[:MAX_CONTEXT_CHARS],
            "architecture": architecture_text[:MAX_CONTEXT_CHARS],
            "tasks": tasks,
        },
        tasks_total=len(tasks),
        tasks_done=sum(t["status"] == DONE for t in tasks),
    )
    session.add(run)
    session.flush()
    return run


def claim_next_run(session: Session) -> uuid.UUID | None:
    """Take the oldest queued run (safe with several workers)."""
    run = session.scalars(
        select(FactoryRun)
        .where(FactoryRun.status == RunStatus.QUEUED.value)
        .order_by(FactoryRun.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if run is None:
        session.rollback()
        return None
    run.status = RunStatus.RUNNING.value
    run.started_at = now()
    session.commit()
    return run.id


# --- running ----------------------------------------------------------------------------


class RunStopped(Exception):
    pass


class RunFailed(Exception):
    pass


@dataclass
class Budgets:
    max_steps: int
    task_steps: int
    max_tokens: int
    command_timeout: int


class Workbench:
    """The tools, bound to one run: files in the database, commands in the sandbox."""

    def __init__(self, session: Session, run: FactoryRun, sandbox: Sandbox, budgets: Budgets):
        self.session = session
        self.run = run
        self.sandbox = sandbox
        self.budgets = budgets
        self.stack = STACKS.get(run.context.get("stack"), STACKS["flask-react"])
        self.files: dict[str, str] = {
            f.path: f.content
            for f in session.scalars(
                select(FactoryFile).where(FactoryFile.factory_id == run.factory_id)
            )
        }
        self.tests_passed = False
        self.simulated = False

    def _event(self, kind: str, title: str, detail: str | None = None) -> None:
        log_event(self.session, self.run, kind, title, detail)

    def _save(self, path: str, content: str) -> None:
        row = self.session.get(FactoryFile, (self.run.factory_id, path))
        if row is None:
            self.session.add(
                FactoryFile(
                    factory_id=self.run.factory_id, path=path, content=content, run_id=self.run.id
                )
            )
        else:
            row.content, row.run_id = content, self.run.id
        self.files[path] = content

    def _exec(self, command: str) -> tuple[bool, str]:
        result = self.sandbox.run(
            dict(self.files),
            command,
            image=self.stack.image,
            timeout=self.budgets.command_timeout,
        )
        self.simulated = self.simulated or result.simulated
        status = "timed out" if result.timed_out else f"exit code {result.exit_code}"
        return result.ok, f"({status})\n{clip(result.output)}"

    def call(self, call: ToolCall) -> ToolResult:
        if call.error:
            return ToolResult(call.id, call.error, is_error=True)
        data = call.input
        try:
            match call.name:
                case "list_files":
                    listing = "\n".join(
                        f"{p} ({len(c)} chars)" for p, c in sorted(self.files.items())
                    )
                    return ToolResult(call.id, listing or "No files yet.")
                case "read_file":
                    path = str(safe_path(data["path"]))
                    if path not in self.files:
                        return ToolResult(call.id, f"No file {path}.", is_error=True)
                    return ToolResult(call.id, self.files[path])
                case "write_file":
                    path = str(safe_path(data["path"]))
                    if len(path) > 300:
                        return ToolResult(call.id, "That path is too long.", is_error=True)
                    if len(data["content"]) > MAX_FILE_CHARS:
                        return ToolResult(
                            call.id, f"Files can be at most {MAX_FILE_CHARS} characters.", True
                        )
                    if path not in self.files and len(self.files) >= MAX_FILES:
                        return ToolResult(call.id, f"The app can have {MAX_FILES} files.", True)
                    new = path not in self.files
                    self._save(path, data["content"])
                    self.tests_passed = False
                    self._event("file", f"{'Created' if new else 'Updated'} {path}")
                    return ToolResult(call.id, f"Saved {path}.")
                case "delete_file":
                    path = str(safe_path(data["path"]))
                    if path not in self.files:
                        return ToolResult(call.id, f"No file {path}.", is_error=True)
                    row = self.session.get(FactoryFile, (self.run.factory_id, path))
                    if row is not None:
                        self.session.delete(row)
                    del self.files[path]
                    self.tests_passed = False
                    self._event("file", f"Deleted {path}")
                    return ToolResult(call.id, f"Deleted {path}.")
                case "run_tests":
                    ok, output = self._exec(self.stack.sandbox_test)
                    self.tests_passed = ok
                    title = "Tests passed" if ok else "Tests failed"
                    self._event("tests", title, output)
                    return ToolResult(call.id, f"{title} {output}")
                case "run_command":
                    command = data["command"].strip()
                    if not command:
                        return ToolResult(call.id, "The command is empty.", is_error=True)
                    ok, output = self._exec(command)
                    self._event("command", f"$ {command[:200]}", output)
                    return ToolResult(call.id, output)
        except ValueError as exc:
            return ToolResult(call.id, str(exc), is_error=True)
        return ToolResult(call.id, f"Unknown tool {call.name}.", is_error=True)


def log_event(
    session: Session, run: FactoryRun, kind: str, title: str, detail: str | None = None
) -> None:
    seq = (
        session.scalar(
            select(func.max(FactoryRunEvent.seq)).where(FactoryRunEvent.run_id == run.id)
        )
        or 0
    )
    session.add(
        FactoryRunEvent(run_id=run.id, seq=seq + 1, kind=kind, title=title[:500], detail=detail)
    )
    session.commit()


def _stop_requested(session: Session, run: FactoryRun) -> bool:
    status = session.scalar(select(FactoryRun.status).where(FactoryRun.id == run.id))
    return status == RunStatus.STOPPING.value


def run_context(ctx: dict) -> str:
    stack = STACKS.get(ctx.get("stack"), STACKS["flask-react"])
    return (
        f"App: {ctx.get('app') or 'Untitled'}\n"
        f"Stack: {stack.name} ({stack.languages}).\n"
        f"Layout: {stack.layout}\n"
        f"Tests run with: {stack.sandbox_test}\n\n"
        f"<brief>\n{ctx.get('brief', '')}\n</brief>\n\n"
        f"<architecture>\n{ctx.get('architecture', '')}\n</architecture>"
    )


def task_message(tasks: list[dict], current: dict, notes: list[str], file_count: int) -> str:
    lines = []
    for t in tasks:
        mark = "✓" if t["status"] == DONE else ("→" if t is current else " ")
        lines.append(f"{mark} {t['title']}")
    facts = [f"{k}: {current[k]}" for k in ("component", "priority", "size") if current.get(k)]
    parts = [
        "Task list (✓ done, → current):\n" + "\n".join(lines),
        f"Current task: {current['title']}\n" + "\n".join(facts),
    ]
    if current.get("detail"):
        parts.append(f"Detail: {current['detail']}")
    if current.get("depends_on"):
        parts.append("Builds on: " + "; ".join(current["depends_on"]))
    if notes:
        parts.append("Notes from earlier tasks:\n" + "\n".join(f"- {n}" for n in notes[-20:]))
    parts.append(
        f"The app has {file_count} file(s) so far." if file_count else "There are no files yet."
    )
    parts.append("Do the current task.")
    return "\n\n".join(parts)


def _next_task(tasks: list[dict]) -> dict | None:
    done = {t["title"].casefold() for t in tasks if t["status"] == DONE}
    for t in tasks:
        if t["status"] != DONE and all(d.casefold() in done for d in t["depends_on"]):
            return t
    return None


def _is_setup(task: dict, index: int, file_count: int) -> bool:
    title = task["title"].casefold()
    return file_count == 0 or "skeleton" in title or "set up" in title or "setup" in title


def _work_on_task(
    session: Session,
    run: FactoryRun,
    bench: Workbench,
    agent: AgentSession,
    task: dict,
    budgets: Budgets,
) -> tuple[bool, str]:
    """Drive one conversation to finish_task. Returns (done, summary)."""
    nudged = False
    for _ in range(budgets.task_steps):
        if _stop_requested(session, run):
            raise RunStopped
        if run.steps >= budgets.max_steps:
            raise RunFailed(f"The build used its {budgets.max_steps} steps.")
        if run.tokens >= budgets.max_tokens:
            raise RunFailed(f"The build used its budget of {budgets.max_tokens:,} tokens.")
        turn = agent.step()
        run.steps += 1
        run.tokens += turn.tokens
        session.commit()
        if turn.text.strip():
            log_event(session, run, "note", clip(turn.text.strip(), 500))
        if turn.stop_reason == "pause_turn":
            agent.add_results([])
            continue
        if not turn.calls:
            if nudged:
                return False, "The agent stopped without finishing the task."
            nudged = True
            agent.nudge("Continue with the task, and call finish_task when the tests pass.")
            continue
        results, finished = [], None
        for call in turn.calls:
            if call.name == "finish_task" and not call.error:
                if call.input["outcome"] == "done" and not bench.tests_passed:
                    results.append(
                        ToolResult(
                            call.id,
                            "The tests have not passed since your last change. Run run_tests "
                            "and fix what fails first.",
                            is_error=True,
                        )
                    )
                    continue
                finished = call.input
                results.append(ToolResult(call.id, "Noted."))
                continue
            results.append(bench.call(call))
        session.commit()
        if finished:
            return finished["outcome"] == "done", finished["summary"].strip()[:1000]
        agent.add_results(results)
    return False, f"The task was not finished within {budgets.task_steps} steps."


def execute_run(
    session: Session,
    run_id: uuid.UUID,
    *,
    chat: ChatProvider | None,
    sandbox: Sandbox | None,
    settings: Settings,
) -> None:
    run = session.get(FactoryRun, run_id)
    if run is None:
        return
    if run.status == RunStatus.QUEUED.value:
        run.status, run.started_at = RunStatus.RUNNING.value, now()
        session.commit()
    budgets = Budgets(
        max_steps=settings.factory_max_steps,
        task_steps=settings.factory_task_steps,
        max_tokens=settings.factory_max_tokens,
        command_timeout=settings.factory_command_timeout,
    )
    project = session.get(FactoryProject, run.factory_id)
    tasks: list[dict] = [dict(t) for t in run.context.get("tasks", [])]
    notes: list[str] = []
    current: dict | None = None
    final, summary, error = RunStatus.FAILED, None, None
    try:
        if chat is None:
            raise RunFailed("AI features are turned off on this server.")
        if provider := run.context.get("personal_provider"):
            from ymer.ai.personal import profile_chat
            from ymer.models.user import UserAiProfile

            profile = (
                session.get(UserAiProfile, (run.started_by_id, provider))
                if run.started_by_id
                else None
            )
            chat = profile_chat(settings, profile, run.context["model"])
        if sandbox is None:
            raise RunFailed("Builds are turned off on this server (YMER_FACTORY_SANDBOX=off).")
        if not tasks:
            raise RunFailed("There are no tasks to build. Design the architecture first.")
        bench = Workbench(session, run, sandbox, budgets)
        context = run_context(run.context)
        log_event(
            session,
            run,
            "start",
            f"Building {run.tasks_total - run.tasks_done} task(s) with {bench.stack.name}",
            f"Sandbox: {sandbox.name}. Model: {chat.model}.",
        )
        while True:
            task = _next_task(tasks)
            if task is None:
                if any(t["status"] != DONE for t in tasks):
                    raise RunFailed("The remaining tasks depend on tasks that are not done.")
                break
            index, current = tasks.index(task), task
            set_task_status(
                session, project.tasks_page_id if project else None, task["row_id"], IN_PROGRESS
            )
            log_event(session, run, "task", task["title"], task.get("detail") or None)
            agent = start_session(
                chat,
                context=context,
                first=task_message(tasks, task, notes, len(bench.files)),
                script={
                    "task": task,
                    "stack": bench.stack.key,
                    "app": run.context.get("app"),
                    "setup": _is_setup(task, index, len(bench.files)),
                },
            )
            bench.tests_passed = False
            done, task_summary = _work_on_task(session, run, bench, agent, task, budgets)
            if not done:
                set_task_status(
                    session, project.tasks_page_id if project else None, task["row_id"], TO_DO
                )
                log_event(session, run, "blocked", f"Blocked: {task['title']}", task_summary)
                raise RunFailed(f"Stopped at “{task['title']}”: {task_summary}")
            task["status"] = DONE
            run.tasks_done += 1
            set_task_status(
                session, project.tasks_page_id if project else None, task["row_id"], DONE
            )
            notes.append(f"{task['title']}: {task_summary}")
            log_event(session, run, "done", f"Done: {task['title']}", task_summary)
        final = RunStatus.PASSED
        summary = f"Built {run.tasks_done} task(s); the tests pass."
        if bench.simulated:
            summary += " (Demo sandbox: the tests were checked, not run.)"
    except RunStopped:
        final, summary = RunStatus.STOPPED, "Stopped by a person."
    except RunFailed as exc:
        error = str(exc)
    except (AiError, SandboxError) as exc:
        error = str(exc)
    except Exception:
        log.exception("Factory run %s crashed", run_id)
        error = "The build crashed. The server log has the details."
    session.rollback()
    run = session.get(FactoryRun, run_id)
    if current is not None and current["status"] != DONE and project is not None:
        # A task left In progress by a stop, crash or budget goes back to To do.
        set_task_status(session, project.tasks_page_id, current["row_id"], TO_DO)
    run.status = final.value
    run.summary, run.error = summary, error
    run.finished_at = now()
    if final is RunStatus.PASSED and project is not None and project.stage == FactoryStage.PLANNED:
        project.stage = FactoryStage.BUILT.value
    session.commit()
    log_event(
        session,
        run,
        "finish",
        {"passed": "Build passed", "stopped": "Build stopped"}.get(final.value, "Build failed"),
        error or summary,
    )
