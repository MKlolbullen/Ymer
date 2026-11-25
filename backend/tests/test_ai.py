"""Phase 4: the AI layer (providers, indexing, semantic search, Q&A, assist, generation).

Everything runs against the offline fake providers; one test checks the request the
Claude provider would send, with the SDK client replaced.
"""

import json
from types import SimpleNamespace

import pycrdt
import pytest

from tests.conftest import TEST_COLLAB_SECRET
from tests.factories import grant, make_page, make_user, make_workspace
from ymer.ai import AiError, AiRefused, AiServices, get_ai
from ymer.ai.fake import FakeChat, HashEmbedder, database_from_outline, mindmap_from_outline
from ymer.ai.generate import mindmap_tree, tidy_layout
from ymer.ai.indexing import process_queue, split_text
from ymer.ai.providers import AnthropicProvider, ChatRequest
from ymer.content import outline
from ymer.db import db_session
from ymer.models import AiChunk, AiIndexJob, DatabaseRow, DocumentContent, Page, PageType, Role

SECRET = {"X-Ymer-Collab-Secret": TEST_COLLAB_SECRET}


# --- helpers ----------------------------------------------------------------------------


def el(tag: str, *children, **attrs) -> pycrdt.XmlElement:
    return pycrdt.XmlElement(
        tag,
        {k: str(v) for k, v in attrs.items()},
        [pycrdt.XmlText(c) if isinstance(c, str) else c for c in children],
    )


def yjs_blocks(*blocks: pycrdt.XmlElement) -> bytes:
    doc = pycrdt.Doc()
    fragment = doc.get("default", type=pycrdt.XmlFragment)
    for block in blocks:
        fragment.children.append(block)
    return doc.get_update()


def paragraphs(*texts: str) -> bytes:
    return yjs_blocks(*(el("paragraph", t) for t in texts))


def store(client, page, state: bytes):
    resp = client.put(f"/internal/collab/documents/{page.id}", data=state, headers=SECRET)
    assert resp.status_code == 204


def index_all(app):
    with app.app_context():
        process_queue(db_session, get_ai().embedder, limit=100)


@pytest.fixture
def team(app, db, client):
    owner = make_user(app, "owner")
    editor = make_user(app, "editor")
    viewer = make_user(app, "viewer")
    guest = make_user(app, "guest")
    outsider = make_user(app, "outsider")
    ws = make_workspace(owner, {editor: Role.EDITOR, viewer: Role.VIEWER})
    base = f"/api/workspaces/{ws.id}"
    return type("Team", (), dict(locals()))


def set_settings(app, **changes):
    app.extensions["ymer_settings"] = app.extensions["ymer_settings"].model_copy(update=changes)


# --- pure helpers (no database) ---------------------------------------------------------


def test_outline_keeps_headings_lists_and_tasks():
    state = yjs_blocks(
        el("heading", "Launch plan", level=1),
        el("paragraph", "We ship in ", el("pageMention", id="x", label="March")),
        el(
            "bulletList",
            el(
                "listItem",
                el("paragraph", "Website"),
                el("bulletList", el("listItem", el("paragraph", "Copy"))),
            ),
            el("listItem", el("paragraph", "Press")),
        ),
        el("taskList", el("taskItem", el("paragraph", "Book venue"), checked="true")),
        el("orderedList", el("listItem", el("paragraph", "First"))),
    )
    assert outline(state, PageType.DOCUMENT).splitlines() == [
        "# Launch plan",
        "We ship in March",
        "- Website",
        "  - Copy",
        "- Press",
        "- [x] Book venue",
        "1. First",
    ]
    assert outline(b"junk", PageType.DOCUMENT) == ""


def test_outline_of_a_mind_map_is_an_indented_tree():
    doc = pycrdt.Doc()
    nodes = doc.get("mindmap", type=pycrdt.Map)
    nodes["root"] = pycrdt.Map({"label": "Trip", "parent": None, "x": 0, "y": 0})
    nodes["a"] = pycrdt.Map({"label": "Food", "parent": "root", "x": 240, "y": 0})
    nodes["b"] = pycrdt.Map({"label": "Tacos", "parent": "a", "x": 480, "y": 0})
    assert outline(doc.get_update(), PageType.MINDMAP).splitlines() == [
        "# Trip",
        "- Food",
        "  - Tacos",
    ]


def test_split_text_makes_bounded_chunks():
    text = "\n".join(f"Line {i} " + "word " * 30 for i in range(40))
    chunks = split_text(text, size=500)
    assert len(chunks) > 5
    assert all(len(c) <= 500 for c in chunks)
    assert "Line 0" in chunks[0] and "Line 39" in chunks[-1]


def test_hash_embedder_ranks_related_text_higher():
    e = HashEmbedder()
    q, related, other = e.embed(["vacation policy", "Our vacation policies", "Quarterly revenue"])

    def cos(a, b):
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cos(q, related) > cos(q, other)
    assert abs(cos(q, q) - 1) < 1e-9


def test_fake_generators_follow_the_outline():
    notes = "# Goals\n- Grow users\n- Hire\n# Risks\n- Budget: tight\n- [x] Legal review"
    mm = mindmap_from_outline("Plan", notes)
    assert [i["label"] for i in mm["ideas"]] == ["Goals", "Risks"]
    assert [c["label"] for c in mm["ideas"][0]["children"]] == ["Grow users", "Hire"]
    table = database_from_outline("Plan", notes)
    names = [p["name"] for p in table["properties"]]
    assert names[0] == "Name" and "Section" in names and "Done" in names and "Notes" in names
    budget = next(r for r in table["rows"] if r["values"][0]["value"] == "Budget")
    assert {"property": "Notes", "value": "tight"} in budget["values"]


def test_mindmap_layout_balances_branches():
    spec = {"title": "T", "ideas": [{"label": f"B{i}", "children": []} for i in range(6)]}
    nodes = mindmap_tree(spec)
    positions = tidy_layout(nodes)
    xs = [positions[n["id"]][0] for n in nodes if n["parent"] == "root"]
    assert any(x > 0 for x in xs) and any(x < 0 for x in xs)


def test_claude_provider_request_shape(monkeypatch):
    sent = {}

    class Stream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[
                    SimpleNamespace(type="thinking", thinking=""),
                    SimpleNamespace(type="text", text='{"ok": true}'),
                ],
            )

    provider = AnthropicProvider(
        model="claude-opus-5-5",
        effort="medium",
        api_key="test-key",
        base_url=None,
        timeout=10,
        fallbacks=True,
    )

    def stream(**kwargs):
        sent.update(kwargs)
        return Stream()

    monkeypatch.setattr(provider.client.beta.messages, "stream", stream)
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    out = provider.complete(
        ChatRequest(
            task="t", system="sys", messages=[{"role": "user", "content": "hi"}], schema=schema
        )
    )
    assert out == '{"ok": true}'
    assert sent["model"] == "claude-opus-5-5"
    assert sent["system"] == "sys"
    assert sent["output_config"] == {
        "effort": "medium",
        "format": {"type": "json_schema", "schema": schema},
    }
    assert sent["fallbacks"] == "default"
    assert sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert "thinking" not in sent and "temperature" not in sent

    monkeypatch.setattr(
        provider.client.beta.messages,
        "stream",
        lambda **kw: type(
            "S",
            (Stream,),
            {"get_final_message": lambda self: SimpleNamespace(stop_reason="refusal", content=[])},
        )(),
    )
    with pytest.raises(AiRefused):
        provider.complete(ChatRequest(task="t", system="s", messages=[]))


# --- status -----------------------------------------------------------------------------


@pytest.mark.db
def test_status_reports_the_demo_providers(client, team):
    data = client.get("/api/ai/status", headers=team.viewer.headers).get_json()
    assert data["enabled"] is True
    assert data["demo"] is True
    assert data["provider"] == "fake"
    assert data["semantic_search"] is True


@pytest.mark.db
def test_ai_off_answers_503(app, client, team):
    app.extensions["ymer_ai"] = AiServices(chat=None, embedder=None)
    page = make_page(team.ws, "Doc")
    resp = client.post(
        f"{team.base}/pages/{page.id}/ai/assist",
        json={"action": "improve", "text": "hi"},
        headers=team.editor.headers,
    )
    assert resp.status_code == 503
    assert client.get("/api/ai/status", headers=team.viewer.headers).get_json()["enabled"] is False


# --- indexing ---------------------------------------------------------------------------


@pytest.mark.db
def test_saving_a_page_queues_it_and_the_worker_embeds_changed_chunks(app, client, team):
    page = make_page(team.ws, "Handbook")
    store(client, page, paragraphs("Vacation: everyone gets 25 days.", "Expenses go to Finance."))
    assert db_session.get(AiIndexJob, page.id) is not None
    index_all(app)
    assert db_session.get(AiIndexJob, page.id) is None
    chunks = db_session.query(AiChunk).filter_by(page_id=page.id).all()
    assert len(chunks) == 1 and "25 days" in chunks[0].content
    assert chunks[0].content.startswith("Handbook (document)")

    # Saving the same text again re-embeds nothing.
    store(client, page, paragraphs("Vacation: everyone gets 25 days.", "Expenses go to Finance."))
    with app.app_context():
        from ymer.ai.indexing import index_page

        assert index_page(db_session, db_session.get(Page, page.id), get_ai().embedder) == 0


@pytest.mark.db
def test_database_rows_are_indexed_under_their_database(app, client, team):
    db_page = client.post(
        f"{team.base}/pages",
        json={"type": "database", "title": "Vendors"},
        headers=team.editor.headers,
    ).get_json()["page"]
    props = client.get(
        f"{team.base}/databases/{db_page['id']}", headers=team.editor.headers
    ).get_json()
    title_id = props["properties"][0]["id"]
    client.post(
        f"{team.base}/databases/{db_page['id']}/rows",
        json={"values": {title_id: "Acme catering"}},
        headers=team.editor.headers,
    )
    index_all(app)
    rows = db_session.query(AiChunk).filter(AiChunk.row_id.isnot(None)).all()
    assert len(rows) == 1
    assert rows[0].content == "Row in Vendors\nName: Acme catering"


@pytest.mark.db
def test_inline_indexing_mode(app, client, team):
    set_settings(app, ai_index_inline=True)
    page = make_page(team.ws, "Inline")
    store(client, page, paragraphs("Indexed right away."))
    assert db_session.get(AiIndexJob, page.id) is None
    assert db_session.query(AiChunk).filter_by(page_id=page.id).count() == 1


@pytest.mark.db
def test_indexing_errors_leave_the_page_queued(app, client, team):
    class Broken(HashEmbedder):
        def embed(self, texts):
            raise AiError("down")

    page = make_page(team.ws, "Doc")
    store(client, page, paragraphs("text"))
    with app.app_context():
        process_queue(db_session, Broken())
    job = db_session.get(AiIndexJob, page.id)
    assert job.attempts == 1 and job.last_error == "down"


# --- semantic search and Q&A respect page permissions -------------------------------------


@pytest.fixture
def library(app, client, team):
    public = make_page(team.ws, "Travel policy")
    store(client, public, paragraphs("Book flights through the travel desk. Economy class only."))
    secret = make_page(team.ws, "Salaries")
    store(client, secret, paragraphs("Salary bands: engineers earn the travel desk budget."))
    grant(public, team.guest, Role.VIEWER)  # the guest sees only the travel policy
    index_all(app)
    return SimpleNamespace(public=public, secret=secret)


@pytest.mark.db
def test_semantic_search_only_returns_visible_pages(client, team, library):
    q = {"q": "travel desk"}
    member = client.get(f"{team.base}/ai/search", query_string=q, headers=team.viewer.headers)
    assert {r["page_id"] for r in member.get_json()["results"]} == {
        str(library.public.id),
        str(library.secret.id),
    }
    guest = client.get(f"{team.base}/ai/search", query_string=q, headers=team.guest.headers)
    assert [r["page_id"] for r in guest.get_json()["results"]] == [str(library.public.id)]
    outsider = client.get(f"{team.base}/ai/search", query_string=q, headers=team.outsider.headers)
    assert outsider.status_code == 404


@pytest.mark.db
def test_ask_answers_from_visible_pages_with_sources(client, team, library):
    resp = client.post(
        f"{team.base}/ai/ask",
        json={"question": "How do I book flights?"},
        headers=team.guest.headers,
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "travel desk" in data["answer"]["content"]
    assert "[1]" in data["answer"]["content"]
    assert [s["page_id"] for s in data["answer"]["sources"]] == [str(library.public.id)]
    assert all("text" not in s for s in data["answer"]["sources"])
    assert data["demo"] is True

    convo = data["conversation"]["id"]
    follow = client.post(
        f"{team.base}/ai/ask",
        json={"question": "Which class?", "conversation_id": convo},
        headers=team.guest.headers,
    )
    assert follow.status_code == 200
    thread = client.get(f"{team.base}/ai/conversations/{convo}", headers=team.guest.headers)
    assert [m["role"] for m in thread.get_json()["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    listed = client.get(f"{team.base}/ai/conversations", headers=team.guest.headers).get_json()
    assert [c["id"] for c in listed["conversations"]] == [convo]

    # Conversations are private to their author.
    other = client.get(f"{team.base}/ai/conversations/{convo}", headers=team.owner.headers)
    assert other.status_code == 404
    assert (
        client.get(f"{team.base}/ai/conversations", headers=team.owner.headers).get_json()[
            "conversations"
        ]
        == []
    )


@pytest.mark.db
def test_sources_disappear_when_access_is_lost(client, team, library):
    grant(make_page(team.ws, "Lobby"), team.guest, Role.VIEWER)  # keeps the guest in
    data = client.post(
        f"{team.base}/ai/ask", json={"question": "travel desk"}, headers=team.guest.headers
    ).get_json()
    convo = data["conversation"]["id"]
    assert [s["page_id"] for s in data["answer"]["sources"]] == [str(library.public.id)]
    client.delete(
        f"{team.base}/pages/{library.public.id}/permissions/{team.guest.id}",
        headers=team.owner.headers,
    )
    thread = client.get(f"{team.base}/ai/conversations/{convo}", headers=team.guest.headers)
    assert thread.status_code == 200
    assert thread.get_json()["messages"][1]["sources"] == []


@pytest.mark.db
def test_ask_never_sends_hidden_pages_to_the_model(app, client, team, library):
    seen = []

    class Spy(FakeChat):
        def complete(self, request):
            seen.append(json.dumps(request.messages) + json.dumps(request.payload))
            return super().complete(request)

    app.extensions["ymer_ai"] = AiServices(chat=Spy(), embedder=get_ai_embedder(app))
    client.post(
        f"{team.base}/ai/ask", json={"question": "salary bands"}, headers=team.guest.headers
    )
    assert seen and all("Salary" not in s for s in seen)


def get_ai_embedder(app):
    return app.extensions["ymer_ai"].embedder


# --- writing assist ---------------------------------------------------------------------


@pytest.mark.db
def test_assist_roles(client, team):
    page = make_page(team.ws, "Doc")
    url = f"{team.base}/pages/{page.id}/ai/assist"
    text = {"text": "i dont think teh plan works.  it needs  work"}
    resp = client.post(url, json={"action": "fix", **text}, headers=team.editor.headers)
    assert resp.status_code == 200
    assert resp.get_json()["text"] == "I don't think the plan works. It needs work."
    assert (
        client.post(url, json={"action": "fix", **text}, headers=team.viewer.headers).status_code
        == 403
    )
    assert (
        client.post(
            url, json={"action": "summarize", **text}, headers=team.viewer.headers
        ).status_code
        == 200
    )
    assert (
        client.post(url, json={"action": "fix", **text}, headers=team.outsider.headers).status_code
        == 404
    )
    custom = client.post(url, json={"action": "custom", **text}, headers=team.editor.headers)
    assert custom.status_code == 422
    bad = client.post(url, json={"action": "nope", **text}, headers=team.editor.headers)
    assert bad.status_code == 422


@pytest.mark.db
def test_assist_without_text_uses_the_whole_page(client, team):
    page = make_page(team.ws, "Doc")
    store(client, page, paragraphs("Short page."))
    resp = client.post(
        f"{team.base}/pages/{page.id}/ai/assist",
        json={"action": "summarize"},
        headers=team.viewer.headers,
    )
    assert resp.get_json()["text"] == "Short page."
    empty = make_page(team.ws, "Empty")
    resp = client.post(
        f"{team.base}/pages/{empty.id}/ai/assist",
        json={"action": "summarize"},
        headers=team.viewer.headers,
    )
    assert resp.status_code == 422


@pytest.mark.db
def test_provider_errors_and_refusals_become_json_errors(app, client, team):
    class Failing(FakeChat):
        def __init__(self, exc):
            self.exc = exc

        def complete(self, request):
            raise self.exc

    page = make_page(team.ws, "Doc")
    url = f"{team.base}/pages/{page.id}/ai/assist"
    for exc, code in ((AiError("Could not reach the AI provider."), 502), (AiRefused("no"), 422)):
        app.extensions["ymer_ai"] = AiServices(chat=Failing(exc), embedder=None)
        resp = client.post(url, json={"action": "fix", "text": "x"}, headers=team.editor.headers)
        assert resp.status_code == code
        assert resp.get_json()["message"] == str(exc)


@pytest.mark.db
def test_rate_limit(app, client, team, monkeypatch):
    counts: dict[str, int] = {}

    class FakeRedis:
        def incr(self, key):
            counts[key] = counts.get(key, 0) + 1
            return counts[key]

        def expire(self, key, seconds):
            pass

    monkeypatch.setitem(app.extensions, "redis", FakeRedis())
    set_settings(app, ai_rate_limit=2, rate_limit_enabled=True)
    page = make_page(team.ws, "Doc")
    url = f"{team.base}/pages/{page.id}/ai/assist"
    codes = [
        client.post(
            url, json={"action": "fix", "text": "x"}, headers=team.editor.headers
        ).status_code
        for _ in range(3)
    ]
    assert codes == [200, 200, 429]


# --- generation -------------------------------------------------------------------------


NOTES = yjs_blocks(
    el("heading", "Goals", level=2),
    el(
        "bulletList",
        el("listItem", el("paragraph", "Grow to 10k users")),
        el("listItem", el("paragraph", "Hire two engineers")),
    ),
    el("heading", "Risks", level=2),
    el("taskList", el("taskItem", el("paragraph", "Budget: still tight"), checked="false")),
)


@pytest.mark.db
def test_generate_a_mind_map_from_a_page(app, client, team):
    source = make_page(team.ws, "Strategy")
    store(client, source, NOTES)
    resp = client.post(
        f"{team.base}/ai/generate",
        json={"kind": "mindmap", "source_page_id": str(source.id), "parent_id": str(source.id)},
        headers=team.editor.headers,
    )
    assert resp.status_code == 201, resp.get_json()
    page = resp.get_json()["page"]
    assert page["type"] == "mindmap" and page["parent_id"] == str(source.id)
    assert page["title"] == "Strategy (mind map)"
    state = db_session.get(DocumentContent, page["id"]).state
    assert outline(state, PageType.MINDMAP).splitlines() == [
        "# Strategy",
        "- Goals",
        "  - Grow to 10k users",
        "  - Hire two engineers",
        "- Risks",
        "  - Budget: still tight",
    ]
    assert "Hire two engineers" in db_session.get(Page, page["id"]).content_text
    assert db_session.get(AiIndexJob, page["id"]) is not None


@pytest.mark.db
def test_generate_a_database_from_pasted_text(client, team):
    text = "# Q1\n- Alpha: first launch\n- [x] Beta - done early\n# Q2\n- Gamma"
    resp = client.post(
        f"{team.base}/ai/generate",
        json={"kind": "database", "text": text, "title": "Launches"},
        headers=team.editor.headers,
    )
    assert resp.status_code == 201, resp.get_json()
    data = resp.get_json()
    assert data["summary"] == {"properties": 4, "rows": 3}
    db_id = data["page"]["id"]
    database = client.get(f"{team.base}/databases/{db_id}", headers=team.editor.headers).get_json()
    props = {p["name"]: p for p in database["properties"]}
    assert props["Name"]["type"] == "title"
    assert [o["name"] for o in props["Section"]["config"]["options"]] == ["Q1", "Q2"]
    assert [v["type"] for v in database["views"]] == ["table", "board"]
    rows = (
        db_session.query(DatabaseRow)
        .filter_by(database_id=db_id)
        .order_by(DatabaseRow.position)
        .all()
    )
    beta = rows[1].values
    assert beta[props["Name"]["id"]] == "Beta"
    assert beta[props["Done"]["id"]] is True
    assert rows[0].values[props["Notes"]["id"]] == "first launch"


@pytest.mark.db
def test_generate_checks_both_pages(client, team):
    source = make_page(team.ws, "Notes")
    store(client, source, NOTES)
    body = {"kind": "mindmap", "source_page_id": str(source.id)}
    # A viewer can read the notes but cannot create pages.
    assert (
        client.post(f"{team.base}/ai/generate", json=body, headers=team.viewer.headers).status_code
        == 403
    )
    # A guest editor of another page cannot read these notes.
    other = make_page(team.ws, "Theirs")
    grant(other, team.guest, Role.EDITOR)
    resp = client.post(
        f"{team.base}/ai/generate",
        json={**body, "parent_id": str(other.id)},
        headers=team.guest.headers,
    )
    assert resp.status_code == 404
    empty = make_page(team.ws, "Empty")
    resp = client.post(
        f"{team.base}/ai/generate",
        json={"kind": "mindmap", "source_page_id": str(empty.id)},
        headers=team.editor.headers,
    )
    assert resp.status_code == 422


@pytest.mark.db
def test_generate_rejects_malformed_model_output(app, client, team):
    class Garbage(FakeChat):
        def complete(self, request):
            return "not json"

    app.extensions["ymer_ai"] = AiServices(chat=Garbage(), embedder=None)
    resp = client.post(
        f"{team.base}/ai/generate",
        json={"kind": "database", "text": "- a"},
        headers=team.editor.headers,
    )
    assert resp.status_code == 502
    assert db_session.query(Page).filter_by(workspace_id=team.ws.id).count() == 0


def test_openai_compatible_provider_against_a_stub_server():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from ymer.ai.providers import OpenAICompatibleEmbedder, OpenAICompatibleProvider

    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, self.headers.get("Authorization"), body))
            if self.path.endswith("/chat/completions"):
                out = {"choices": [{"message": {"content": "hello"}}]}
            else:
                out = {
                    "data": [{"index": 1, "embedding": [0, 1]}, {"index": 0, "embedding": [1, 0]}]
                }
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        chat = OpenAICompatibleProvider(model="llama", base_url=base, api_key="k", timeout=5)
        schema = {"type": "object"}
        assert (
            chat.complete(ChatRequest(task="t", system="sys", messages=[], schema=schema))
            == "hello"
        )
        path, auth, body = seen[0]
        assert path == "/v1/chat/completions" and auth == "Bearer k"
        assert body["messages"][0] == {"role": "system", "content": "sys"}
        assert body["response_format"]["json_schema"]["schema"] == schema
        embedder = OpenAICompatibleEmbedder(model="nomic", base_url=base, api_key=None, timeout=5)
        assert embedder.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
        assert seen[1][0] == "/v1/embeddings" and seen[1][1] is None
    finally:
        server.shutdown()
