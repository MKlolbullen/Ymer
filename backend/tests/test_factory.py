"""Factory stage F1: sources, the brief, open questions and approval.

Runs against the offline demo model, which builds the brief from the sources' outline.
"""

import json

import pytest

from tests.conftest import TEST_COLLAB_SECRET
from tests.factories import make_page, make_user, make_workspace
from tests.test_ai import el, yjs_blocks
from ymer.content import outline
from ymer.db import db_session
from ymer.factory.brief import clean_brief, fit_sources
from ymer.factory.document import bullets, document_state, heading, paragraph, tasks
from ymer.factory.fake import fake_brief
from ymer.models import DocumentContent, DocumentVersion, Page, PageLink, PageType, Role

SECRET = {"X-Ymer-Collab-Secret": TEST_COLLAB_SECRET}

SPEC = yjs_blocks(
    el("heading", "Expense tracker", level=1),
    el("paragraph", "A small web app to log expenses and see totals per month."),
    el("heading", "Features", level=2),
    el(
        "bulletList",
        el("listItem", el("paragraph", "Add an expense: amount, date and category")),
        el("listItem", el("paragraph", "Monthly totals per category")),
        el("listItem", el("paragraph", "Export to CSV")),
    ),
    el("heading", "Out of scope", level=2),
    el("bulletList", el("listItem", el("paragraph", "Receipt scanning"))),
)


# --- pure helpers -----------------------------------------------------------------------


def test_document_state_is_what_the_editor_reads():
    import uuid

    target = uuid.uuid4()
    state = document_state(
        [
            paragraph("Summary here."),
            heading("Features"),
            heading("Must have", 3),
            bullets(["One", "Two"]),
            tasks([("Done thing", True), ("Open thing", False)]),
            heading("Sources"),
            bullets([[("mention", target, "Spec")]]),
        ]
    )
    assert outline(state, PageType.DOCUMENT).splitlines() == [
        "Summary here.",
        "## Features",
        "### Must have",
        "- One",
        "- Two",
        "- [x] Done thing",
        "- [ ] Open thing",
        "## Sources",
        "- Spec",
    ]


def test_fake_brief_finds_features_and_asks_about_gaps():
    text = (
        "# Expense tracker\nA place to log expenses.\n## Features\n"
        "- Add an expense: amount and date\n- Monthly totals at 17:00\n## Out of scope\n- Receipts"
    )
    brief = json.loads(fake_brief({"sources": [{"title": "Spec", "text": text}]}))
    assert [f["name"] for f in brief["features"]] == ["Add an expense", "Monthly totals at 17:00"]
    assert brief["features"][0]["detail"] == "amount and date"
    assert {f["priority"] for f in brief["features"]} == {"must"}
    assert brief["out_of_scope"] == ["Receipts"]
    asked = [q["question"] for q in brief["questions"]]
    # The notes never say who uses it, where it runs, what it keeps or who may sign in.
    assert any("Who will use it" in q for q in asked)
    assert any("Where should it run" in q for q in asked)
    assert any("remember" in q for q in asked)
    assert any("sign in" in q for q in asked)

    # Answered and dismissed questions are not asked again, and answers cover gaps.
    again = json.loads(
        fake_brief(
            {
                "sources": [{"title": "Spec", "text": text}],
                "decisions": [
                    {"question": asked[0], "answer": "Our finance team, in the browser."}
                ],
                "dismissed": [q for q in asked if "sign in" in q or "remember" in q],
            }
        )
    )
    assert again["questions"] == []


def test_clean_brief_caps_and_defaults_untrusted_output():
    brief = clean_brief(
        {
            "title": "  T  ",
            "features": [
                {"name": "A", "detail": "x", "priority": "MUST"},
                {"name": "B", "priority": "urgent"},
                {"name": ""},
                "junk",
            ],
            "users": ["", "Admins", 3],
            "questions": [{"question": f"Q{i}?", "why": ""} for i in range(20)],
        }
    )
    assert brief["title"] == "T"
    assert [(f["name"], f["priority"]) for f in brief["features"]] == [
        ("A", "must"),
        ("B", "should"),
    ]
    assert brief["users"] == ["Admins", "3"]
    assert len(brief["questions"]) == 8
    assert clean_brief("nonsense")["features"] == []


def test_fit_sources_shares_the_budget():
    fitted = fit_sources([("a", "short"), ("b", "x\n" * 200)], limit=100)
    assert fitted[0] == ("a", "short")
    assert len(fitted[1][1]) <= 100


# --- API --------------------------------------------------------------------------------


@pytest.fixture
def team(app, db, client):
    owner = make_user(app, "owner")
    editor = make_user(app, "editor")
    viewer = make_user(app, "viewer")
    ws = make_workspace(owner, {editor: Role.EDITOR, viewer: Role.VIEWER})
    spec = make_page(ws, "Expense tracker spec")
    resp = client.put(f"/internal/collab/documents/{spec.id}", data=SPEC, headers=SECRET)
    assert resp.status_code == 204
    base = f"/api/workspaces/{ws.id}"
    factory = client.post(
        f"{base}/pages", json={"type": "factory", "title": "Expense app"}, headers=editor.headers
    ).get_json()["page"]
    url = f"{base}/factories/{factory['id']}"
    return type("Team", (), dict(locals()))


def test_a_new_factory_starts_without_sources(client, team):
    data = client.get(team.url, headers=team.viewer.headers).get_json()["factory"]
    assert data["stage"] == "sources"
    assert data["sources"] == [] and data["questions"] == [] and data["brief"] is None
    assert data["can_edit"] is False
    assert client.get(team.url, headers=team.editor.headers).get_json()["factory"]["can_edit"]


def test_only_factory_pages_have_a_factory(client, team):
    resp = client.get(f"{team.base}/factories/{team.spec.id}", headers=team.editor.headers)
    assert resp.status_code == 404


def test_sources_need_edit_access_and_visible_pages(client, team):
    body = {"page_ids": [str(team.spec.id)]}
    resp = client.put(f"{team.url}/sources", json=body, headers=team.viewer.headers)
    assert resp.status_code == 403

    other = make_workspace(make_user(client.application, "other"))
    elsewhere = make_page(other, "Not here")
    resp = client.put(
        f"{team.url}/sources", json={"page_ids": [str(elsewhere.id)]}, headers=team.editor.headers
    )
    assert resp.status_code == 404

    resp = client.put(
        f"{team.url}/sources", json={"page_ids": [team.factory["id"]]}, headers=team.editor.headers
    )
    assert resp.status_code == 400

    data = client.put(f"{team.url}/sources", json=body, headers=team.editor.headers).get_json()
    assert [s["title"] for s in data["factory"]["sources"]] == ["Expense tracker spec"]

    data = client.put(
        f"{team.url}/sources", json={"page_ids": []}, headers=team.editor.headers
    ).get_json()
    assert data["factory"]["sources"] == []


def test_a_brief_needs_notes(client, team):
    resp = client.post(f"{team.url}/brief", headers=team.editor.headers)
    assert resp.status_code == 422


def test_brief_questions_and_approval(app, client, team):
    h = team.editor.headers
    client.put(f"{team.url}/sources", json={"page_ids": [str(team.spec.id)]}, headers=h)

    resp = client.post(f"{team.url}/brief", headers=h)
    assert resp.status_code == 201
    first = resp.get_json()["factory"]
    assert first["stage"] == "brief"
    assert first["brief"]["parent_id"] == team.factory["id"]
    assert first["brief"]["title"] == "Brief"
    with app.app_context():
        content = db_session.get(DocumentContent, first["brief"]["id"])
        text = outline(content.state, PageType.DOCUMENT)
        assert "## Features" in text and "- Add an expense: amount, date and category" in text
        assert "## Out of scope" in text and "- Receipt scanning" in text
        # The source is linked, so it lists the brief among its backlinks.
        assert db_session.get(PageLink, (first["brief"]["id"], team.spec.id)) is not None

    questions = first["questions"]
    asked = {q["question"]: q for q in questions}
    assert questions and all(q["status"] == "open" for q in questions)
    assert not any("Where should it run" in q for q in asked)  # "web app" covers it

    resp = client.post(f"{team.url}/approve", headers=h)
    assert resp.status_code == 409 and "still open" in resp.get_json()["message"]

    who = next(q for q in questions if "Who will use it" in q["question"])
    data = client.patch(
        f"{team.url}/questions/{who['id']}",
        json={"answer": "The four people in finance."},
        headers=h,
    ).get_json()["factory"]
    answered = next(q for q in data["questions"] if q["id"] == who["id"])
    assert answered["status"] == "answered"
    assert answered["answered_by"]["display_name"]
    for q in data["questions"]:
        if q["status"] == "open":
            client.patch(f"{team.url}/questions/{q['id']}", json={"status": "dismissed"}, headers=h)

    second = client.post(f"{team.url}/brief", headers=h).get_json()["factory"]
    assert second["brief"]["id"] != first["brief"]["id"]
    assert all(q["status"] != "open" for q in second["questions"])
    with app.app_context():
        old = db_session.get(Page, first["brief"]["id"])
        assert old.title == "Brief (earlier draft)"
        content = db_session.get(DocumentContent, second["brief"]["id"])
        text = outline(content.state, PageType.DOCUMENT)
        assert "## Decisions" in text and "The four people in finance." in text

    resp = client.post(f"{team.url}/approve", headers=team.viewer.headers)
    assert resp.status_code == 403
    approved = client.post(f"{team.url}/approve", headers=h).get_json()["factory"]
    assert approved["stage"] == "approved"
    assert approved["approved_by"]["id"] == str(team.editor.id)
    with app.app_context():
        version = db_session.query(DocumentVersion).filter_by(
            page_id=second["brief"]["id"], label="Approved brief"
        )
        assert version.count() == 1

    # Approved briefs are frozen until reopened.
    assert client.post(f"{team.url}/brief", headers=h).status_code == 409
    resp = client.put(f"{team.url}/sources", json={"page_ids": []}, headers=h)
    assert resp.status_code == 409
    reopened = client.post(f"{team.url}/reopen", headers=h).get_json()["factory"]
    assert reopened["stage"] == "brief" and reopened["approved_at"] is None


def test_a_blank_answer_reopens_a_question(client, team):
    h = team.editor.headers
    client.put(f"{team.url}/sources", json={"page_ids": [str(team.spec.id)]}, headers=h)
    q = client.post(f"{team.url}/brief", headers=h).get_json()["factory"]["questions"][0]
    client.patch(f"{team.url}/questions/{q['id']}", json={"answer": "Yes"}, headers=h)
    data = client.patch(
        f"{team.url}/questions/{q['id']}", json={"answer": "  "}, headers=h
    ).get_json()["factory"]
    reopened = next(x for x in data["questions"] if x["id"] == q["id"])
    assert reopened["status"] == "open" and reopened["answer"] is None


def test_ai_off_means_no_brief(app, client, team):
    from tests.test_ai import set_settings
    from ymer.ai import init_ai

    h = team.editor.headers
    client.put(f"{team.url}/sources", json={"page_ids": [str(team.spec.id)]}, headers=h)
    set_settings(app, ai_provider="off")
    init_ai(app, app.extensions["ymer_settings"])
    assert client.post(f"{team.url}/brief", headers=h).status_code == 503
