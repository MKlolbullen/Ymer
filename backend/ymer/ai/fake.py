"""Offline stand-ins for a language model and an embedding model.

They make every AI feature usable in development, tests and demos without a key or a
network: the answers come from simple text heuristics (extractive summaries, outlines
turned into trees, keyword overlap), and are deterministic so tests can assert on them.
The app labels their output "demo" so nobody mistakes it for a real model.
"""

import hashlib
import json
import math
import re
from collections import Counter

from ymer.ai.providers import ChatRequest

STOPWORDS = frozenset(
    """a an and are as at be but by can do does for from has have how i if in into is it its
    me my of on or our so that the their them then there these they this to was we were what
    when where which who why will with you your about also all any just not no more most
    should would could than too very really much many""".split()  # noqa: SIM905
)
TYPOS = {
    "teh": "the",
    "recieve": "receive",
    "seperate": "separate",
    "definately": "definitely",
    "alot": "a lot",
    "dont": "don't",
    "cant": "can't",
    "wont": "won't",
    "doesnt": "doesn't",
    "hasnt": "hasn't",
    "wasnt": "wasn't",
    "arent": "aren't",
    "isnt": "isn't",
    "im": "I'm",
    "occured": "occurred",
    "untill": "until",
    "wich": "which",
}
FILLER = re.compile(r"\b(really|very|just|basically|actually|literally|quite|simply)\s+", re.I)


def words(text: str) -> list[str]:
    return re.findall(r"[a-zA-ZÀ-ÿ0-9']+", text.lower())


def stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def keywords(text: str) -> list[str]:
    return [stem(w) for w in words(text) if w not in STOPWORDS and len(w) > 2]


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip(" -*#>") for p in parts if len(p.strip(" -*#>")) > 1]


def _ranked(text: str) -> list[tuple[float, int, str]]:
    sents = sentences(text)
    freq = Counter(keywords(text))
    ranked = []
    for i, s in enumerate(sents):
        kws = keywords(s)
        score = sum(freq[k] for k in kws) / (len(kws) ** 0.5 if kws else 1)
        ranked.append((score, i, s))
    return ranked


def extract(text: str, count: int) -> list[str]:
    """The ``count`` most central sentences, in their original order."""
    best = sorted(_ranked(text), key=lambda r: (-r[0], r[1]))[:count]
    return [s for _, _, s in sorted(best, key=lambda r: r[1])]


def _end(sentence: str) -> str:
    return sentence if sentence[-1:] in ".!?:" else sentence + "."


def polish(text: str) -> str:
    """Fix spacing, capitals and a few common misspellings."""

    def fix_word(match: re.Match) -> str:
        word = match.group(0)
        fixed = TYPOS.get(word.lower())
        if fixed is None:
            return "I" if word == "i" else word
        return fixed[0].upper() + fixed[1:] if word[0].isupper() else fixed

    out = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        p = re.sub(r"[ \t]+", " ", paragraph.strip())
        p = re.sub(r"\s+([,.;:!?])", r"\1", p)
        p = re.sub(r"[a-zA-Z']+", fix_word, p)
        p = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), p)
        if p and not p.startswith(("-", "*", "#")):
            p = _end(p)
        out.append(p)
    return "\n\n".join(out)


class FakeChat:
    name = "fake"
    model = "ymer-demo"

    def complete(self, request: ChatRequest) -> str:
        task, data = request.task, request.payload
        if task.startswith("assist."):
            return self._assist(task.removeprefix("assist."), data)
        if task == "ask":
            return self._ask(data)
        if task == "generate.mindmap":
            return json.dumps(mindmap_from_outline(data.get("title", ""), data.get("text", "")))
        if task == "generate.database":
            return json.dumps(database_from_outline(data.get("title", ""), data.get("text", "")))
        if task == "factory.brief":
            from ymer.factory.fake import fake_brief  # the factory builds on this module

            return fake_brief(data)
        if task == "factory.architecture":
            from ymer.factory.fake import fake_architecture

            return fake_architecture(data)
        if task == "factory.spec_changes":
            from ymer.factory.fake import fake_spec_changes

            return fake_spec_changes(data)
        if task == "factory.change_plan":
            from ymer.factory.fake import fake_change_plan

            return fake_change_plan(data)
        return ""

    def _assist(self, action: str, data: dict) -> str:
        text = data.get("text", "")
        if action == "summarize":
            picked = extract(text, 3 if len(sentences(text)) > 4 else 1)
            return " ".join(_end(s) for s in picked)
        if action == "shorten":
            sents = sentences(FILLER.sub("", text))
            return " ".join(_end(s) for s in extract(" ".join(sents), max(1, len(sents) // 2)))
        if action == "expand":
            top = [w for w, _ in Counter(keywords(text)).most_common(3)]
            extra = (
                f"In practice this comes down to {', '.join(top[:-1])} and {top[-1]}, "
                "and each of them deserves a closer look."
                if len(top) > 1
                else "This deserves a closer look."
            )
            return polish(text) + "\n\n" + extra
        if action == "bullets":
            return "\n".join(f"- {_end(s)}" for s in sentences(text))
        if action == "custom" and "bullet" in data.get("instruction", "").lower():
            return "\n".join(f"- {_end(s)}" for s in sentences(text))
        return polish(text)  # improve, fix, custom

    def _ask(self, data: dict) -> str:
        question = set(keywords(data.get("question", "")))
        scored = []
        for source in data.get("sources", []):
            # Skip the chunk's header line (page title and type) and "Property:" labels.
            body = source.get("text", "").split("\n", 1)[-1]
            body = re.sub(r"^[^:\n]{1,40}: ", "", body, flags=re.M)
            body = re.sub(r"^\s*(?:[-*]|\d+\.)?\s*\[[ xX]\]\s*", "", body, flags=re.M)
            for sent in sentences(body):
                overlap = len(question & set(keywords(sent)))
                if overlap:
                    scored.append((overlap, source["n"], sent))
        if not scored:
            return (
                "I could not find anything about that in the pages you can see. "
                "Try other words, or check that the page is shared with you."
            )
        scored.sort(key=lambda s: -s[0])
        seen, picked = set(), []
        for _, n, sent in scored:
            if sent.casefold() not in seen:
                seen.add(sent.casefold())
                picked.append(f"{_end(sent)} [{n}]")
            if len(picked) == 3:
                break
        return "Here is what your workspace says:\n\n" + "\n".join(f"- {p}" for p in picked)


# --- outlines to structures (also used to check real models' output shape in tests) -----


def _outline_items(text: str) -> list[tuple[int, str, str, bool | None]]:
    """(depth, kind, text, checked) per line: kind is heading, item or text; checked is
    True/False for task items and None otherwise."""
    items = []
    for raw in text.splitlines():
        if not raw.strip():
            continue
        indent = (len(raw) - len(raw.lstrip(" "))) // 2
        line = raw.strip()
        if m := re.match(r"^(#{1,6})\s+(.*)", line):
            items.append((len(m.group(1)) - 1, "heading", m.group(2).strip(), None))
        elif m := re.match(r"^(?:[-*+]|\d+[.)])\s+(?:\[([ xX])\]\s+)?(.*)", line):
            checked = None if m.group(1) is None else m.group(1) in "xX"
            items.append((indent, "item", m.group(2).strip(), checked))
        else:
            items.append((indent, "text", line, None))
    return items


def _short(text: str, limit: int = 60) -> str:
    text = text.strip().rstrip(".")
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + "…"


MINDMAP_LABEL = 44  # wider labels overlap their parent in the tidy layout


def mindmap_from_outline(title: str, text: str) -> dict:
    """{title, ideas: [{label, children: [{label, children: [{label}]}]}]}."""
    items = _outline_items(text)
    ideas: list[dict] = []
    current: dict | None = None
    sub: dict | None = None
    headings = [i for i in items if i[1] == "heading"]
    top_depth = min((i[0] for i in headings), default=0)
    for depth, kind, line, _ in items:
        if kind == "heading" and depth == top_depth:
            current = {"label": _short(line, MINDMAP_LABEL), "children": []}
            ideas.append(current)
            sub = None
        elif kind == "heading" or (kind == "item" and depth == 0):
            node = {"label": _short(line, MINDMAP_LABEL), "children": []}
            if current is None:
                ideas.append(node)
                current, sub = node, None
            else:
                current["children"].append(node)
                sub = node
        elif kind == "item":
            target = sub if sub is not None else current
            if target is None:
                ideas.append({"label": _short(line, MINDMAP_LABEL), "children": []})
            elif target is current:
                current["children"].append({"label": _short(line, MINDMAP_LABEL), "children": []})
            else:
                target["children"].append({"label": _short(line, MINDMAP_LABEL)})
        else:  # plain paragraph: its key sentences become ideas
            if current is None and headings:
                continue  # an introduction above the first heading
            for sent in extract(line, 2):
                node = {"label": _short(sent, 30), "children": []}
                (current["children"] if current is not None else ideas).append(node)
    if not ideas:
        ideas = [{"label": w.title(), "children": []} for w in keywords(text)[:5]]
    for idea in ideas:
        idea["children"] = idea["children"][:8]
        for child in idea["children"]:
            child.setdefault("children", [])
            child["children"] = [{"label": c["label"]} for c in child["children"][:6]]
    return {"title": title or "Mind map", "ideas": ideas[:10]}


def database_from_outline(title: str, text: str) -> dict:
    """{title, properties: [{name, type, options}], rows: [{values: [{property, value}]}]}."""
    items = _outline_items(text)
    section = None
    rows: list[dict] = []
    sections: list[str] = []
    has_tasks = any(checked is not None for *_, checked in items)
    for depth, kind, line, checked in items:
        if kind == "heading":
            section = _short(line, 40)
            continue
        if kind != "item" or depth > 0:
            continue
        done = ("true" if checked else "false") if has_tasks else None
        name, notes = line, ""
        for sep in (": ", " — ", " - ", " – "):
            if sep in line:
                name, notes = line.split(sep, 1)
                break
        values = [{"property": "Name", "value": _short(name, 120)}]
        if notes:
            values.append({"property": "Notes", "value": notes.strip()})
        if section:
            values.append({"property": "Section", "value": section})
            if section not in sections:
                sections.append(section)
        if done is not None:
            values.append({"property": "Done", "value": done})
        rows.append({"values": values})
    if not rows:  # no list: one row per key sentence
        rows = [
            {"values": [{"property": "Name", "value": _short(s, 120)}]} for s in extract(text, 10)
        ]
    properties = [{"name": "Name", "type": "title", "options": []}]
    if any(len(r["values"]) > 1 and r["values"][1]["property"] == "Notes" for r in rows):
        properties.append({"name": "Notes", "type": "text", "options": []})
    if sections:
        properties.append({"name": "Section", "type": "select", "options": sections})
    if has_tasks:
        properties.append({"name": "Done", "type": "checkbox", "options": []})
    return {"title": title or "Database", "properties": properties, "rows": rows[:100]}


class HashEmbedder:
    """Bag of (stemmed) words and character trigrams, hashed into a fixed-size vector.

    Not semantic in the way a trained model is, but it ranks related text together well
    enough to exercise semantic search and Q&A end to end.
    """

    name = "fake"

    def __init__(self, model: str = "hash-256", dimensions: int = 256) -> None:
        self.model = model
        self.dimensions = dimensions

    def _bucket(self, feature: str) -> tuple[int, float]:
        digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        return value % self.dimensions, 1.0 if (value >> 63) & 1 else -1.0

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dimensions
            for word in keywords(text):
                i, sign = self._bucket("w:" + word)
                vec[i] += sign
                padded = f"#{word}#"
                for j in range(len(padded) - 2):
                    i, sign = self._bucket("c:" + padded[j : j + 3])
                    vec[i] += 0.3 * sign
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out
