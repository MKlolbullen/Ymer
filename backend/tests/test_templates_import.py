"""Templates, imports (Markdown, Notion exports, CSV) and duplicating pages."""

import io
import zipfile
from datetime import date

import pycrdt
import pytest
from sqlalchemy import select

from tests.factories import make_page, make_user, make_workspace
from tests.fakes import FakeS3
from ymer.content import FRAGMENT, outline
from ymer.documents import save_state
from ymer.importing.bundle import ImportProblem, build_plan, read_uploads
from ymer.importing.markdown import Asset, Mention, blocks_preview, markdown_to_state, prepare
from ymer.importing.tables import csv_spec, parse_date
from ymer.models import (
    AuditEvent,
    DatabaseProperty,
    DatabaseRow,
    DatabaseView,
    DocumentContent,
    File,
    Page,
    PageLink,
    PageType,
    Role,
)
from ymer.templates import BY_ID, PAGE_TEMPLATES, WORKSPACE_TEMPLATES, plan_for

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def xml(text: str, resolve=None) -> list[str]:
    return [b["xml"] for b in blocks_preview(markdown_to_state(text, resolve).state)]


def zipped(files: dict[str, bytes | str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


# --- Markdown to documents ------------------------------------------------------------


def test_markdown_blocks_and_marks_map_onto_the_document_schema():
    blocks = xml(
        "Some **bold _both_** ~~gone~~ `code` [site](https://example.com) and [x](ftp://h/f)\n\n"
        "- [ ] open\n- [x] done\n\n"
        "1. one\n2. two\n   - nested\n\n"
        "> quoted\n\n"
        "```python\nprint(1)\n```\n\n"
        "---\n\n"
        "#### Deep heading\n"
    )
    assert blocks[0] == (
        "<paragraph>Some <bold>bold </bold><bold><italic>both</italic></bold> "
        "<strike>gone</strike> <code>code</code> "
        '<link href="https://example.com">site</link> and x</paragraph>'
    )
    assert blocks[1] == (
        '<taskList><taskItem checked="false"><paragraph>open</paragraph></taskItem>'
        '<taskItem checked="true"><paragraph>done</paragraph></taskItem></taskList>'
    )
    assert blocks[2].startswith("<orderedList><listItem><paragraph>one</paragraph></listItem>")
    assert "<bulletList><listItem><paragraph>nested</paragraph>" in blocks[2]
    assert blocks[3] == "<blockquote><paragraph>quoted</paragraph></blockquote>"
    assert blocks[4] == '<codeBlock language="python">print(1)</codeBlock>'
    assert blocks[5] == "<horizontalRule></horizontalRule>"
    assert blocks[6] == '<heading level="3">Deep heading</heading>'


def test_markdown_tables_callouts_and_html_become_text():
    blocks = xml("|a|b|\n|-|-|\n|1|22|\n\n<aside>💡 Remember <b>this</b></aside>\n")
    assert blocks[0] == "<codeBlock>a  b\n-  --\n1  22</codeBlock>"
    assert blocks[1] == "<blockquote><paragraph>💡 Remember this</paragraph></blockquote>"


def test_links_resolve_to_mentions_images_and_file_cards():
    def resolve(href, _label):
        return {
            "Other.md": Mention("1b4e28ba-2fa1-11d2-883f-0016d3cca427", "Other"),
            "pic.png": Asset("f-1", "pic.png", 10, True),
            "doc.pdf": Asset("f-2", "doc.pdf", 20, False),
        }.get(href)

    blocks = xml(
        "See [the other page](Other.md).\n\n![A picture](pic.png) caption\n\n[pdf](doc.pdf)",
        resolve,
    )
    # Attribute order in Yjs XML is not fixed, so check the mention's attributes one by one.
    assert blocks[0].startswith("<paragraph>See <pageMention ")
    assert blocks[0].endswith("></pageMention>.</paragraph>")
    assert 'id="1b4e28ba-2fa1-11d2-883f-0016d3cca427"' in blocks[0]
    assert 'label="the other page"' in blocks[0]
    # Attribute order in Yjs XML is not fixed, so compare tag and attributes.
    previews = blocks_preview(
        markdown_to_state("![A picture](pic.png)\n\n[pdf](doc.pdf)", resolve).state
    )
    assert (previews[0]["tag"], previews[0]["attrs"]) == (
        "image",
        {"fileId": "f-1", "alt": "A picture"},
    )
    assert (previews[1]["tag"], previews[1]["attrs"]) == (
        "fileAttachment",
        {"fileId": "f-2", "filename": "doc.pdf", "size": 20},
    )
    assert blocks[2] == "<paragraph>caption</paragraph>"


def test_prepare_reads_front_matter_titles_and_wiki_links():
    prepared = prepare(
        "﻿---\ntitle: 'From front matter'\ntags: x\n---\n# Heading\n\nSee [[Other page|that]]."
    )
    assert prepared.title == "From front matter"
    assert "# Heading" not in prepared.text
    assert "[that](<wiki:Other page>)" in prepared.text
    assert prepare("# Only a heading\nbody").title == "Only a heading"
    assert prepare("No title here").title is None


def test_document_text_is_searchable_after_conversion():
    state = markdown_to_state("Hello **world**\n\n- item").state
    assert outline(state, PageType.DOCUMENT).splitlines() == ["Hello world", "- item"]


# --- CSV to databases -------------------------------------------------------------------


def test_csv_types_are_inferred_the_way_notion_exports_them():
    spec = csv_spec(
        "﻿Name,Done,Points,Due,Status,Tags,Site,Related\n"
        'A,Yes,3,"November 25, 2026",Todo,"x, y",https://a.example,"B (Other%20abc.md)"\n'
        'B,No,1.5,"December 1, 2026 → December 3, 2026",Done,x,https://b.example,\n'
        'C,No,"1,200",2026-12-24,Todo,y,,\n'
        "D,No,,,Todo,,,\n"
    )
    types = {p["name"]: p["type"] for p in spec["properties"]}
    assert types == {
        "Name": "title",
        "Done": "checkbox",
        "Points": "number",
        "Due": "date_range",  # one row is a "start → end" range
        "Status": "select",
        "Tags": "multi_select",
        "Site": "url",
        "Related": "text",
    }
    first = {v["property"]: v["value"] for v in spec["rows"][0]["values"]}
    assert first["Due"] == "2026-11-25"
    second = {v["property"]: v["value"] for v in spec["rows"][1]["values"]}
    assert second["Due"] == "2026-12-01 → 2026-12-03"
    assert first["Related"] == "B"
    third = {v["property"]: v["value"] for v in spec["rows"][2]["values"]}
    assert third["Points"] == "1200"
    assert parse_date("December 1, 2026 3:30 PM") == "2026-12-01T15:30:00"


def test_csv_schema_pins_types():
    spec = csv_spec(
        "Task,Status\nA,Doing\n",
        {
            "properties": [
                {"name": "Status", "type": "select", "options": ["To do", "Doing"]},
                {"name": "Due", "type": "date"},
            ]
        },
    )
    assert [(p["name"], p["type"]) for p in spec["properties"]] == [
        ("Task", "title"),
        ("Status", "select"),
        ("Due", "date"),
    ]
    assert spec["properties"][1]["options"] == ["To do", "Doing"]


# --- planning imports -------------------------------------------------------------------

NOTION_ID = "0123456789abcdef0123456789abcdef"
DB_ID = "fedcba9876543210fedcba9876543210"


def notion_export() -> dict[str, bytes | str]:
    return {
        f"Home {NOTION_ID}.md": (
            "# Home\n\nSee [Projects](Home%20" + NOTION_ID + "/Projects%20" + DB_ID + ".csv) and "
            "[Zeta](Home%20" + NOTION_ID + "/Zeta%20" + "1" * 32 + ".md), "
            "then [Alpha](Home%20" + NOTION_ID + "/Alpha%20" + "2" * 32 + ".md).\n\n"
            "![Logo](Home%20" + NOTION_ID + "/logo.png)\n"
        ),
        f"Home {NOTION_ID}/logo.png": PNG,
        f"Home {NOTION_ID}/unused.png": PNG,
        f"Home {NOTION_ID}/Zeta {'1' * 32}.md": (
            f"# Zeta\n\nBack to [Home](https://www.notion.so/Home-{NOTION_ID}).\n"
        ),
        f"Home {NOTION_ID}/Alpha {'2' * 32}.md": "# Alpha\n\n| a | b |\n|---|---|\n| 1 | 2 |\n",
        f"Home {NOTION_ID}/Projects {DB_ID}.csv": "Name,Status\nOne,Done\n",
        f"Home {NOTION_ID}/Projects {DB_ID}_all.csv": (
            "Name,Status\nOne,Done\nTwo,Doing\nThree,Doing\nFour,Done\n"
        ),
        f"Home {NOTION_ID}/Projects {DB_ID}/One {'3' * 32}.md": (
            "# One\n\nStatus: Done\n\nNotes about one.\n"
        ),
        f"Home {NOTION_ID}/Projects {DB_ID}/Two {'4' * 32}.md": "# Two\n\nStatus: Doing\n",
        "__MACOSX/._junk": b"x",
        ".DS_Store": b"x",
    }


def test_notion_exports_become_a_page_tree():
    files = read_uploads([("Export.zip", zipped({"Part-1.zip": zipped(notion_export())}))])
    plan = build_plan(files)
    assert [n.title for n in plan.roots] == ["Home"]
    home = plan.roots[0]
    # Sub-pages follow the order the parent links to them (Notion's order).
    assert [(c.title, c.kind) for c in home.children] == [
        ("Projects", PageType.DATABASE),
        ("Zeta", PageType.DOCUMENT),
        ("Alpha", PageType.DOCUMENT),
    ]
    projects = home.children[0]
    assert len(projects.spec["rows"]) == 4  # the _all copy
    # Row pages with only their properties are dropped; ones with a body are kept.
    assert [(c.title, c.text.strip()) for c in projects.children] == [("One", "Notes about one.")]
    assert set(plan.assets) == {f"Home {NOTION_ID}/logo.png", f"Home {NOTION_ID}/unused.png"}
    assert plan.by_notion_id[NOTION_ID] is home


def test_folders_without_a_page_become_folders_and_unsafe_paths_are_ignored():
    files = read_uploads(
        [
            (
                "notes.zip",
                zipped(
                    {
                        "Vault/Daily/2026-11-25.md": "Today",
                        "Vault/Ideas.md": "[[2026-11-25]]",
                        "../escape.md": "nope",
                        "Vault/image.gif": b"GIF89a",
                    }
                ),
            ),
            ("Loose.md", b"# Loose page\n"),
        ]
    )
    assert "escape.md" not in {f.path for f in files}
    plan = build_plan(files)
    assert [(n.title, n.kind) for n in plan.roots] == [
        ("Loose page", PageType.DOCUMENT),
        ("Vault", PageType.FOLDER),
    ]
    vault = plan.roots[1]
    assert [(n.title, n.kind) for n in vault.children] == [
        ("Daily", PageType.FOLDER),
        ("Ideas", PageType.DOCUMENT),
    ]


def test_imports_with_nothing_to_import_or_bad_zips_are_refused():
    with pytest.raises(ImportProblem, match="Nothing to import"):
        build_plan(read_uploads([("photo.png", PNG)]))
    with pytest.raises(ImportProblem, match="could not be opened"):
        read_uploads([("broken.zip", b"not a zip")])


def test_every_builtin_template_plans_cleanly():
    today = date(2026, 11, 25)
    for template in PAGE_TEMPLATES + WORKSPACE_TEMPLATES:
        plan = plan_for(template, today)
        assert plan.roots, template.id
        for node in plan.nodes():
            assert "{{" not in node.title and "{{" not in node.text, template.id
    meeting = plan_for(BY_ID["meeting-notes"], today).roots[0]
    assert meeting.title == "Meeting notes 2026-11-25"
    welcome = plan_for(BY_ID["getting-started"], today)
    assert [n.title for n in welcome.roots] == ["Welcome to Ymer", "Brainstorm", "Tasks"]


# --- API --------------------------------------------------------------------------------


@pytest.fixture
def s3(app):
    fake = FakeS3()
    app.extensions["s3"] = app.extensions["s3_public"] = fake
    return fake


@pytest.fixture
def team(app, db, s3):
    owner = make_user(app, "owner")
    editor = make_user(app, "editor")
    viewer = make_user(app, "viewer")
    ws = make_workspace(owner, {editor: Role.EDITOR, viewer: Role.VIEWER})
    base = f"/api/workspaces/{ws.id}"
    return type("Team", (), dict(locals()))


def upload(client, team, files: dict[str, bytes | str], actor=None, parent_id=None):
    data = {
        "file": [
            (io.BytesIO(c if isinstance(c, bytes) else c.encode()), n) for n, c in files.items()
        ]
    }
    if parent_id:
        data["parent_id"] = str(parent_id)
    return client.post(
        f"{team.base}/import",
        data=data,
        content_type="multipart/form-data",
        headers=(actor or team.editor).headers,
    )


def doc_xml(page_id) -> str:
    from ymer.db import db_session

    content = db_session.get(DocumentContent, page_id)
    doc = pycrdt.Doc()
    doc.apply_update(content.state)
    return str(doc.get(FRAGMENT, type=pycrdt.XmlFragment))


@pytest.mark.db
def test_importing_a_notion_export(client, team, db, s3):
    resp = upload(client, team, {"Export.zip": zipped(notion_export())})
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    summary = body["summary"]
    assert (summary["pages"], summary["databases"], summary["rows"], summary["files"]) == (
        5,
        1,
        4,
        1,
    )
    assert any("tables" in w for w in summary["warnings"])
    assert any("not linked from any page" in w for w in summary["warnings"])
    [home] = body["pages"]
    assert home["title"] == "Home" and home["parent_id"] is None

    pages = {p.title: p for p in db.scalars(select(Page).where(Page.workspace_id == team.ws.id))}
    assert pages["Zeta"].parent_id == pages["Home"].id
    assert pages["One"].parent_id == pages["Projects"].id
    # Links between imported pages are mentions, so backlinks work both ways.
    links = {(link.source_page_id, link.target_page_id) for link in db.scalars(select(PageLink))}
    assert (pages["Home"].id, pages["Projects"].id) in links
    assert (pages["Zeta"].id, pages["Home"].id) in links
    # The picture is stored and attached to the page that shows it.
    [file] = db.scalars(select(File).where(File.page_id == pages["Home"].id)).all()
    assert file.filename == "logo.png" and file.content_type == "image/png"
    assert s3.objects[("ymer", file.storage_key)][0] == PNG
    assert f'fileId="{file.id}"' in doc_xml(pages["Home"].id)
    # The database has typed properties, rows, and a board view.
    props = db.scalars(
        select(DatabaseProperty).where(DatabaseProperty.database_id == pages["Projects"].id)
    ).all()
    assert {(p.name, p.type.value) for p in props} == {("Name", "title"), ("Status", "select")}
    rows = db.scalars(
        select(DatabaseRow).where(DatabaseRow.database_id == pages["Projects"].id)
    ).all()
    assert len(rows) == 4
    views = db.scalars(
        select(DatabaseView).where(DatabaseView.database_id == pages["Projects"].id)
    ).all()
    assert {v.type.value for v in views} == {"table", "board"}
    # Imported pages are searchable straight away.
    search = client.get(f"{team.base}/search?q=Notes", headers=team.editor.headers).get_json()
    assert "One" in {r["title"] for r in search["results"]}
    event = db.scalar(select(AuditEvent).where(AuditEvent.action == "import"))
    assert event.details["pages"] == 5


@pytest.mark.db
def test_importing_markdown_files_under_a_page(client, team, db):
    parent = make_page(team.ws, "Inbox")
    resp = upload(
        client, team, {"a.md": "# First\n\nSee [[Second]].", "Second.md": "Hi"}, parent_id=parent.id
    )
    assert resp.status_code == 201
    titles = {p["title"]: p for p in resp.get_json()["pages"]}
    assert set(titles) == {"First", "Second"}
    assert all(p["parent_id"] == str(parent.id) for p in titles.values())
    assert f'id="{titles["Second"]["id"]}"' in doc_xml(titles["First"]["id"])


@pytest.mark.db
def test_import_permissions_and_bad_uploads(client, team, db):
    assert upload(client, team, {"a.md": "x"}, actor=team.viewer).status_code == 403
    resp = upload(client, team, {"photo.png": PNG})
    assert resp.status_code == 422
    assert "Nothing to import" in resp.get_json()["message"]
    resp = client.post(
        f"{team.base}/import",
        data={},
        content_type="multipart/form-data",
        headers=team.editor.headers,
    )
    assert resp.status_code == 400


@pytest.mark.db
def test_template_catalog_and_applying_builtin_templates(client, team, db):
    catalog = client.get(f"{team.base}/templates", headers=team.viewer.headers).get_json()
    assert {t["id"] for t in catalog["page"]} >= {"meeting-notes", "task-tracker", "brainstorm"}
    assert catalog["custom"] == []
    assert client.get("/api/templates", headers=team.viewer.headers).status_code == 200

    resp = client.post(
        f"{team.base}/templates/apply",
        json={"template_id": "task-tracker"},
        headers=team.editor.headers,
    )
    assert resp.status_code == 201
    page = resp.get_json()["page"]
    assert (page["title"], page["type"]) == ("Tasks", "database")
    status = db.scalar(
        select(DatabaseProperty).where(
            DatabaseProperty.database_id == page["id"], DatabaseProperty.name == "Status"
        )
    )
    assert [o["name"] for o in status.config["options"]] == ["To do", "Doing", "Done"]

    resp = client.post(
        f"{team.base}/templates/apply",
        json={"template_id": "meeting-notes"},
        headers=team.editor.headers,
    )
    assert resp.get_json()["page"]["title"] == f"Meeting notes {date.today().isoformat()}"
    resp = client.post(
        f"{team.base}/templates/apply",
        json={"template_id": "brainstorm"},
        headers=team.editor.headers,
    )
    assert resp.get_json()["page"]["type"] == "mindmap"

    assert (
        client.post(
            f"{team.base}/templates/apply",
            json={"template_id": "retro"},
            headers=team.viewer.headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{team.base}/templates/apply",
            json={"template_id": "nope"},
            headers=team.editor.headers,
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{team.base}/templates/apply", json={}, headers=team.editor.headers
        ).status_code
        == 422
    )


@pytest.mark.db
def test_new_workspaces_can_start_from_a_template(client, team, db):
    resp = client.post(
        "/api/workspaces",
        json={"name": "Team", "template": "team-wiki"},
        headers=team.owner.headers,
    )
    assert resp.status_code == 201
    ws_id = resp.get_json()["workspace"]["id"]
    pages = client.get(f"/api/workspaces/{ws_id}/pages", headers=team.owner.headers).get_json()[
        "pages"
    ]
    roots = sorted((p for p in pages if p["parent_id"] is None), key=lambda p: p["position"])
    assert roots[0]["title"] == "Team home"
    assert {p["title"] for p in pages} >= {
        "Onboarding",
        "Projects",
        "Decision log",
        "Meeting notes",
    }
    # Without a template (the API default) a workspace starts empty.
    resp = client.post("/api/workspaces", json={"name": "Empty"}, headers=team.owner.headers)
    ws_id = resp.get_json()["workspace"]["id"]
    assert (
        client.get(f"/api/workspaces/{ws_id}/pages", headers=team.owner.headers).get_json()["pages"]
        == []
    )
    assert (
        client.post(
            "/api/workspaces", json={"name": "X", "template": "nope"}, headers=team.owner.headers
        ).status_code
        == 400
    )


def _doc_with_mention(target_id) -> bytes:
    doc = pycrdt.Doc()
    fragment = doc.get(FRAGMENT, type=pycrdt.XmlFragment)
    with doc.transaction():
        para = fragment.children.append(pycrdt.XmlElement("paragraph", {}))
        para.children.append(pycrdt.XmlElement("pageMention", {"id": str(target_id), "label": "x"}))
    return doc.get_update()


@pytest.mark.db
def test_saving_a_page_as_a_template_and_duplicating(client, team, db, s3):
    # A project page with a sub-page it mentions, a database, and an attached file.
    resp = upload(
        client,
        team,
        {
            "proj.zip": zipped(
                {
                    "Project.md": "# Project\n\n[Plan](Project/Plan.md)\n\n![Pic](Project/pic.png)",
                    "Project/Plan.md": "# Plan\n\nBack to [Project](../Project.md)",
                    "Project/Tasks.csv": "Task,Status\nA,Doing\nB,Doing\nC,Done\nD,Done\n",
                    "Project/pic.png": PNG,
                }
            )
        },
    )
    project = resp.get_json()["pages"][0]

    resp = client.patch(
        f"{team.base}/pages/{project['id']}",
        json={"is_template": True},
        headers=team.editor.headers,
    )
    assert resp.get_json()["page"]["is_template"] is True
    custom = client.get(f"{team.base}/templates", headers=team.editor.headers).get_json()["custom"]
    assert [c["id"] for c in custom] == [project["id"]]

    resp = client.post(
        f"{team.base}/templates/apply", json={"page_id": project["id"]}, headers=team.editor.headers
    )
    assert resp.status_code == 201
    copy = resp.get_json()["page"]
    assert copy["title"] == "Project" and copy["is_template"] is False
    assert resp.get_json()["summary"]["files"] == 1

    copies = {p.title: p for p in db.scalars(select(Page).where(Page.parent_id == copy["id"]))}
    assert set(copies) == {"Plan", "Tasks"}
    # Mentions inside the copy point at the copied pages, not the originals.
    plan_xml = doc_xml(copies["Plan"].id)
    assert f'id="{copy["id"]}"' in plan_xml and project["id"] not in plan_xml
    project_xml = doc_xml(copy["id"])
    assert f'id="{copies["Plan"].id}"' in project_xml
    [copied_file] = db.scalars(select(File).where(File.page_id == copy["id"])).all()
    assert f'fileId="{copied_file.id}"' in project_xml
    assert ("ymer", copied_file.storage_key) in s3.objects
    # The database came along with its rows and a board grouped by its own property.
    status = db.scalar(
        select(DatabaseProperty).where(
            DatabaseProperty.database_id == copies["Tasks"].id, DatabaseProperty.name == "Status"
        )
    )
    rows = db.scalars(
        select(DatabaseRow).where(DatabaseRow.database_id == copies["Tasks"].id)
    ).all()
    assert len(rows) == 4 and all(str(status.id) in r.values for r in rows)
    board = db.scalar(
        select(DatabaseView).where(
            DatabaseView.database_id == copies["Tasks"].id, DatabaseView.type == "board"
        )
    )
    assert board.config["group_by"] == str(status.id)

    # Duplicating needs editor where the copy goes (next to the original by default).
    resp = client.post(
        f"{team.base}/pages/{copies['Plan'].id}/duplicate", json={}, headers=team.viewer.headers
    )
    assert resp.status_code == 403  # viewers cannot add pages next to it
    resp = client.post(
        f"{team.base}/pages/{copies['Plan'].id}/duplicate", json={}, headers=team.editor.headers
    )
    assert resp.status_code == 201
    dup = resp.get_json()["page"]
    assert dup["title"] == "Plan (copy)" and dup["parent_id"] == copy["id"]
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "page.duplicate"))


@pytest.mark.db
def test_duplicate_keeps_links_to_pages_outside_the_copy(client, team, db):
    root = make_page(team.ws, "Root")
    make_page(team.ws, "Child", parent=root)
    elsewhere = make_page(team.ws, "Elsewhere")
    save_state(db, root.id, _doc_with_mention(elsewhere.id), user_id=None, version_interval=600)
    db.commit()
    resp = client.post(
        f"{team.base}/pages/{root.id}/duplicate", json={}, headers=team.editor.headers
    )
    assert resp.status_code == 201
    copy = resp.get_json()["page"]
    assert copy["title"] == "Root (copy)" and copy["parent_id"] is None
    assert f'id="{elsewhere.id}"' in doc_xml(copy["id"])
    assert {p.title for p in db.scalars(select(Page).where(Page.parent_id == copy["id"]))} == {
        "Child"
    }
    backlinks = client.get(
        f"{team.base}/pages/{elsewhere.id}/backlinks", headers=team.editor.headers
    ).get_json()["pages"]
    assert "Root (copy)" in {p["title"] for p in backlinks}
