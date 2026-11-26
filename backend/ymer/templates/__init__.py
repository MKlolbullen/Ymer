"""Built-in templates: ready-made pages, and starter content for new workspaces.

Each template is a folder under ``files/`` written in the same formats an import
accepts (Markdown documents, CSV databases with an optional ``.schema.json``, and
``.mindmap.json`` mind maps), and is applied by the import pipeline. So adding a template
is adding files, and links between a template's pages work like links in an import.

``{{date}}`` in a file (or its front matter title) becomes today's date.

Workspace templates may also include page templates (``includes``); their files are
added next to the workspace template's own, so its pages can link to them by file name.
"""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ymer.importing.bundle import Plan, SourceFile, build_plan

FILES = Path(__file__).parent / "files"


@dataclass(frozen=True)
class Template:
    id: str
    name: str
    description: str
    icon: str
    kind: str  # "page" or "workspace"
    includes: tuple[str, ...] = ()
    first: tuple[str, ...] = field(default=())  # root page titles to put first, in order

    def json(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "kind": self.kind,
        }


PAGE_TEMPLATES = [
    Template(
        "meeting-notes", "Meeting notes", "Agenda, notes, decisions and action items.", "📝", "page"
    ),
    Template(
        "project-brief",
        "Project brief",
        "Problem, goals, scope, milestones and risks.",
        "🎯",
        "page",
    ),
    Template("weekly-plan", "Weekly plan", "Priorities for the week, day by day.", "🗓️", "page"),
    Template(
        "decision-record",
        "Decision record",
        "Context, options, the decision and its consequences.",
        "⚖️",
        "page",
    ),
    Template(
        "retro", "Retrospective", "What went well, what did not, what to try next.", "🔁", "page"
    ),
    Template(
        "task-tracker",
        "Task tracker",
        "A database of tasks with status, priority and due date.",
        "✅",
        "page",
    ),
    Template(
        "reading-list",
        "Reading list",
        "Books and articles, with status, rating and tags.",
        "📚",
        "page",
    ),
    Template("bug-tracker", "Bug tracker", "Bugs by severity, status and area.", "🐞", "page"),
    Template(
        "brainstorm",
        "Brainstorm",
        "A mind map to collect goals, ideas and questions.",
        "🧠",
        "page",
    ),
]

WORKSPACE_TEMPLATES = [
    Template(
        "getting-started",
        "Getting started",
        "A short tour, a task list and a mind map to try things out.",
        "👋",
        "workspace",
        includes=("task-tracker", "brainstorm"),
        first=("Welcome to Ymer",),
    ),
    Template(
        "team-wiki",
        "Team wiki",
        "A home page, onboarding, meeting notes, projects and a decision log.",
        "🏠",
        "workspace",
        first=("Team home",),
    ),
    Template(
        "personal",
        "Personal notes",
        "A home page, a journal, a weekly plan, a reading list and ideas.",
        "🌱",
        "workspace",
        includes=("weekly-plan", "reading-list", "brainstorm"),
        first=("Home",),
    ),
    Template(
        "product-team",
        "Product team",
        "A roadmap, specs, a bug tracker and retrospectives.",
        "🚀",
        "workspace",
        includes=("bug-tracker", "retro"),
        first=("Product home",),
    ),
]

BY_ID = {t.id: t for t in PAGE_TEMPLATES + WORKSPACE_TEMPLATES}
DEFAULT_WORKSPACE_TEMPLATE = "getting-started"


def _files(template_id: str) -> list[SourceFile]:
    root = FILES / template_id
    return [
        SourceFile(path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def plan_for(template: Template, today: date | None = None) -> Plan:
    files = _files(template.id)
    for included in template.includes:
        files.extend(_files(included))
    plan = build_plan(files, {"date": (today or date.today()).isoformat()})
    order = {title: i for i, title in enumerate(template.first)}
    plan.roots.sort(key=lambda n: order.get(n.title, len(order)))
    return plan
