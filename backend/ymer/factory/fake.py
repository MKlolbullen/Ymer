"""The offline demo model's brief: built from the outline of the sources by rules.

It finds features, users, constraints and exclusions under matching headings, and asks
the questions a builder would need answered when the notes (and the decisions taken so
far) never mention them. It is a stand-in for a real model, good enough to run the
whole stage in tests and demos without a key.
"""

import json
import re

from ymer.ai.fake import _outline_items, extract

SECTIONS = {
    "users": r"\b(users?|audience|who|personas?|customers?)\b",
    "constraints": (
        r"\b(constraints?|non-functional|limits?|tech|stack|platforms?|requirements? on)\b"
    ),
    "out_of_scope": r"\b(out of scope|non-goals?|not now|later|won't|will not)\b",
    "features": r"\b(features?|requirements?|must|should|scope|functions?|what it does|stories)\b",
}

# (question, why, words that show the notes already cover it)
GAPS = [
    (
        "Who will use it, and what do they need to get done?",
        "The notes do not say who the users are.",
        r"\b(users?|customers?|team|staff|admins?|people|members?|visitors?)\b",
    ),
    (
        "Where should it run: in the browser, on phones, on the desktop, "
        "or as a command-line tool?",
        "The notes do not say what kind of app this is.",
        r"\b(web|browser|website|mobile|phone|ios|android|desktop|cli|command[- ]line"
        r"|terminal|api|slack bot)\b",
    ),
    (
        "What does it need to remember between uses, if anything?",
        "The notes do not say what data it keeps.",
        r"\b(stores?|saves?|database|data|persist\w*|history|records?|files?)\b",
    ),
    (
        "Do people need to sign in, or is it open to anyone with the link?",
        "The notes do not mention accounts or access.",
        r"\b(log ?in|sign[- ]?in|accounts?|auth\w*|passwords?|sso|private|public|permissions?)\b",
    ),
]


def _section(heading: str) -> str | None:
    lowered = heading.lower()
    for name, pattern in SECTIONS.items():
        if re.search(pattern, lowered):
            return name
    return None


def fake_brief(payload: dict) -> str:
    sources = payload.get("sources") or []
    decisions = payload.get("decisions") or []
    closed = {q.casefold() for q in payload.get("dismissed") or []}
    closed |= {d["question"].casefold() for d in decisions}
    text = "\n\n".join(s["text"] for s in sources)
    covered = (text + "\n" + "\n".join(d["answer"] for d in decisions)).lower()

    found: dict[str, list[str]] = {name: [] for name in SECTIONS}
    loose: list[str] = []
    paragraphs: list[str] = []
    for source in sources:
        current = None
        for depth, kind, line, _checked in _outline_items(source["text"]):
            if kind == "heading":
                current = _section(line)
            elif kind == "item" and depth == 0:
                (found[current] if current else loose).append(line)
            elif kind == "text" and current is None and depth == 0:
                paragraphs.append(line)
    features = found["features"] or loose
    must = len(features) if found["features"] else min(3, len(features))
    first_title = sources[0]["title"] if sources else "the notes"
    summary = " ".join(extract("\n".join(paragraphs), 2)) if paragraphs else ""

    questions = [
        {"question": q, "why": why}
        for q, why, pattern in GAPS
        if q.casefold() not in closed and not re.search(pattern, covered)
    ]
    if not features and "what are the three things it must do on day one?" not in closed:
        questions.insert(
            0,
            {
                "question": "What are the three things it must do on day one?",
                "why": "The notes do not list any features yet.",
            },
        )
    return json.dumps(
        {
            "title": first_title,
            "summary": summary or f"An app described in {first_title}.",
            "problem": "",
            "users": found["users"][:10],
            "features": [
                {
                    "name": name.split(": ", 1)[0].strip()[:80],
                    "detail": name.split(": ", 1)[1].strip() if ": " in name else "",
                    "priority": "must" if i < must else "should",
                }
                for i, name in enumerate(features[:20])
            ],
            "constraints": found["constraints"][:10],
            "out_of_scope": found["out_of_scope"][:10],
            "questions": questions,
        }
    )


# --- architecture -----------------------------------------------------------------------

STACK_COMPONENTS = {
    "static-web": [
        ("Page", "ui", "The single page: forms, lists and results, in plain HTML and JavaScript."),
        ("Browser storage", "storage", "Keeps the data in the browser's local storage."),
    ],
    "python-cli": [
        ("Command line", "cli", "Parses commands and prints results."),
        ("Core logic", "library", "The rules of the app, free of input and output."),
        ("Storage", "storage", "A SQLite file in the user's data folder."),
    ],
    "flask-react": [
        ("Web UI", "ui", "React pages for every feature, talking to the API."),
        ("API", "api", "Flask JSON endpoints that check input and apply the rules."),
        ("Database", "database", "SQLite through SQLAlchemy, with a migration per change."),
    ],
}
UI_WORDS = r"\b(show|shows|display|list|view|see|page|screen|history|chart|dashboard)\b"


def _brief_sections(text: str) -> dict[str, list[tuple[str, str]]]:
    """Bullets per section of the brief, with the subsection (Must have, ...) they sit in."""
    sections: dict[str, list[tuple[str, str]]] = {}
    current, sub = "", ""
    for depth, kind, line, _checked in _outline_items(text):
        if kind == "heading" and depth <= 1:
            current, sub = line.casefold(), ""
        elif kind == "heading":
            sub = line.casefold()
        elif kind == "item":
            sections.setdefault(current, []).append((sub, line))
    return sections


def _pick_stack(text: str) -> tuple[str, str]:
    lowered = text.lower()
    if re.search(r"\b(cli|command[- ]line|terminal|script)\b", lowered):
        return "python-cli", "The brief describes a tool people run from a terminal."
    shared = r"\b(vote|votes|team|people|share|shared|history|accounts?|users|office)\b"
    if re.search(shared, lowered):
        return (
            "flask-react",
            "Several people use the same data, so it needs a server and a database.",
        )
    return "static-web", "One person uses it at a time and nothing has to be shared."


def _entity(name: str, detail: str) -> dict | None:
    """'Suggest a place' + 'name, cuisine and walking time' -> a Place with three fields."""
    fields = [f.strip() for f in re.split(r",|\band\b", detail) if f.strip()]
    if len(fields) < 2:
        return None
    noun = re.sub(r"^(an?|the)\s+", "", name.split()[-1] if name.split() else "").strip(".")
    if not noun:
        return None
    return {
        "entity": noun[:1].upper() + noun[1:],
        "fields": [{"name": "id", "type": "integer", "note": "primary key"}]
        + [{"name": f.replace(" ", "_").lower(), "type": "text", "note": ""} for f in fields],
    }


def fake_architecture(payload: dict) -> str:
    text = payload.get("brief") or ""
    stack = payload.get("stack")
    reason = "The team chose this stack."
    if not stack:
        stack, reason = _pick_stack(text)
    comps = STACK_COMPONENTS[stack]
    ui_name, main_name = comps[0][0], comps[1][0] if len(comps) > 1 else comps[0][0]

    sections = _brief_sections(text)
    features: list[tuple[str, str, str]] = []
    for key, items in sections.items():
        if "feature" in key:
            for sub, line in items:
                priority = next((p for p in ("must", "should", "could") if p in sub), "should")
                name, _, detail = line.partition(": ")
                features.append((name.strip(), detail.strip(), priority))

    setup = "Set up the project skeleton"
    tasks = [
        {
            "title": setup,
            "component": main_name,
            "priority": "must",
            "size": "S",
            "detail": f"Create the {STACKS_NAMES[stack]} layout, linting and an empty test run.",
            "depends_on": [],
        }
    ]
    entities = []
    for name, detail, priority in features:
        component = ui_name if re.search(UI_WORDS, name.lower()) else main_name
        tasks.append(
            {
                "title": name[:1].upper() + name[1:],
                "component": component,
                "priority": priority,
                "size": "M" if detail else "S",
                "detail": detail,
                "depends_on": [setup],
            }
        )
        if entity := _entity(name, detail):
            entities.append(entity)
    tasks.append(
        {
            "title": "End-to-end tests for the must-haves",
            "component": main_name,
            "priority": "must",
            "size": "M",
            "detail": "",
            "depends_on": [t["title"] for t in tasks[1:] if t["priority"] == "must"][:6],
        }
    )

    interfaces = []
    if stack == "flask-react":
        for e in entities:
            plural = e["entity"].lower() + "s"
            interfaces += [
                {"name": f"GET /api/{plural}", "purpose": f"List {plural}."},
                {"name": f"POST /api/{plural}", "purpose": f"Add a {e['entity'].lower()}."},
            ]
    elif stack == "python-cli":
        for name, _detail, _p in features[:8]:
            verb = name.split()[0].lower() if name.split() else "run"
            interfaces.append({"name": f"app {verb}", "purpose": name})

    constraints = [
        line for key, items in sections.items() if "constraint" in key for _s, line in items
    ]
    decisions = [line for key, items in sections.items() if "decision" in key for _s, line in items]
    summary = next((line for _d, kind, line, _c in _outline_items(text) if kind == "text"), "")
    return json.dumps(
        {
            "stack": stack,
            "stack_reason": reason,
            "overview": summary,
            "components": [
                {
                    "name": name,
                    "kind": kind,
                    "responsibility": what,
                    "depends_on": [comps[i + 1][0]] if i + 1 < len(comps) else [],
                }
                for i, (name, kind, what) in enumerate(comps)
            ],
            "data_model": entities,
            "interfaces": interfaces,
            "tasks": tasks,
            "risks": constraints + [f"Decided: {d}" for d in decisions],
        }
    )


STACKS_NAMES = {
    "static-web": "static web",
    "python-cli": "Python package",
    "flask-react": "Flask + React",
}
