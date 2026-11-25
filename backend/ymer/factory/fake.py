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
