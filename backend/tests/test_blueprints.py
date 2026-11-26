"""Acceptance tests: the example workspaces in ``projects/``, imported and computed.

Each blueprint (``projects/secops.md``, ``projects/school_platform.md``) comes with a zip of
linked CSV/JSON tables and ground-truth numbers. These tests import the zip as it is,
add the formulas and rollups the blueprint describes, and check the numbers, plus the
"stress test" each blueprint suggests (move a row, watch the rollup follow).

The seed tables also carry the values their authors precomputed (``enrolled_count``,
``cumulative_gpa``, ``open_findings``…); those come in as plain columns, so every computed
property is checked against them on every row too, not only on the headline numbers.
"""

from pathlib import Path

import pytest

from tests.test_templates_import import upload
from ymer.models import Role

pytestmark = pytest.mark.db

PROJECTS = Path(__file__).resolve().parents[2] / "projects"
SECOPS_ZIP = PROJECTS / "ymer_secops_seed_data_bundle.zip"
SCHOOL_ZIP = PROJECTS / "ymer_school_seed_data_bundle (1).zip"


@pytest.fixture
def team(app, db):
    from tests.factories import make_user, make_workspace
    from ymer.db import db_session

    owner = make_user(app, "owner")
    editor = make_user(app, "editor")
    # A member whose name matches the "Lead" of two SecOps engagements.
    lind = make_user(app, "lind")
    lind.user.display_name = "K. Lind"
    db_session.commit()
    ws = make_workspace(owner, {editor: Role.EDITOR, lind: Role.EDITOR})
    base = f"/api/workspaces/{ws.id}"
    return type("Team", (), dict(locals()))


class Workspace:
    """Small helpers over the HTTP API, for one imported blueprint."""

    def __init__(self, client, team, root_id: str):
        self.client = client
        self.team = team
        self.base = team.base
        resp = client.get(f"{self.base}/pages", headers=team.owner.headers)
        assert resp.status_code == 200
        pages = resp.get_json()["pages"]
        self.root = next(p for p in pages if p["id"] == root_id)
        self.databases = {
            p["title"]: p["id"]
            for p in pages
            if p["type"] == "database" and p["parent_id"] == root_id
        }

    def schema(self, database: str) -> dict:
        resp = self.client.get(
            f"{self.base}/databases/{self.databases[database]}", headers=self.team.owner.headers
        )
        assert resp.status_code == 200
        return resp.get_json()

    def prop(self, database: str, name: str) -> dict:
        return next(p for p in self.schema(database)["properties"] if p["name"] == name)

    def option(self, database: str, prop: str, name: str) -> str:
        return next(
            o["id"] for o in self.prop(database, prop)["config"]["options"] if o["name"] == name
        )

    def option_name(self, database: str, prop: str, option_id: str | None) -> str | None:
        options = self.prop(database, prop)["config"]["options"]
        return next((o["name"] for o in options if o["id"] == option_id), None)

    def add(self, database: str, name: str, type: str, **config) -> dict:
        resp = self.client.post(
            f"{self.base}/databases/{self.databases[database]}/properties",
            json={"name": name, "type": type, "config": config},
            headers=self.team.owner.headers,
        )
        assert resp.status_code == 201, resp.get_json()
        return resp.get_json()["property"]

    def rows(self, database: str) -> dict[str, dict]:
        """Rows by their key (the imported id column, or the title), values by name."""
        schema = self.schema(database)
        names = {p["id"]: p["name"] for p in schema["properties"]}
        resp = self.client.get(
            f"{self.base}/databases/{self.databases[database]}/rows",
            headers=self.team.owner.headers,
        )
        assert resp.status_code == 200
        out = {}
        for row in resp.get_json()["rows"]:
            values = {names[k]: v for k, v in row["values"].items()}
            values["_id"] = row["id"]
            key = next(
                (v for k, v in values.items() if k.endswith(" ID") and isinstance(v, str)),
                None,
            )
            out[key or values[next(iter(names.values()))]] = values
        return out

    def set(self, database: str, row_id: str, prop_id: str, value) -> None:
        resp = self.client.patch(
            f"{self.base}/databases/{self.databases[database]}/rows/{row_id}",
            json={"values": {prop_id: value}},
            headers=self.team.owner.headers,
        )
        assert resp.status_code == 200, resp.get_json()


def import_zip(client, team, path: Path) -> Workspace:
    resp = upload(client, team, {path.name: path.read_bytes()}, actor=team.owner)
    assert resp.status_code == 201, resp.get_json()
    body = resp.get_json()
    assert len(body["pages"]) == 1
    return Workspace(client, team, body["pages"][0]["id"]), body["summary"]


def one(value):
    """A lookup or select name as a single value."""
    if isinstance(value, list):
        return value[0] if len(value) == 1 else value
    return value


def r1(value):
    return None if value is None else round(value + 1e-9, 1)


# --- SecOps ---------------------------------------------------------------------------


def test_secops_blueprint(client, team):
    ws, summary = import_zip(client, team, SECOPS_ZIP)

    # The zip has csv/ and json/ copies of five tables plus a combined JSON: five
    # databases, under a folder named after the zip, linked by their *_id columns.
    assert ws.root["title"] == "Ymer Secops Seed Data Bundle"
    assert sorted(ws.databases) == [
        "Assets",
        "Engagements",
        "Remediation Tasks",
        "Scan Runs",
        "Vulnerabilities",
    ]
    assert summary["rows"] == 54
    assert summary["relations"] == 7
    assert summary["warnings"] == []

    vulns = ws.schema("Vulnerabilities")["properties"]
    kinds = {p["name"]: p["type"] for p in vulns}
    assert kinds["Title"] == "title"
    assert kinds["Engagement"] == kinds["Asset"] == "relation"
    assert kinds["POC File"] == "file"
    assert "Asset Identifier" not in kinds  # a copy of the related asset's name
    engagement_kinds = {p["name"]: p["type"] for p in ws.schema("Engagements")["properties"]}
    assert engagement_kinds["Dates"] == "date_range"
    assert engagement_kinds["Lead"] == "person"
    assert engagement_kinds["Rules of Engagement PDF"] == "file"
    assert engagement_kinds["Vulnerabilities"] == "relation"  # the other side

    assets = ws.prop("Engagements", "Assets")
    findings = ws.prop("Engagements", "Vulnerabilities")
    status, severity = ws.prop("Vulnerabilities", "Status"), ws.prop("Vulnerabilities", "Severity")
    cvss = ws.prop("Vulnerabilities", "CVSS Score")
    open_filter = {
        "property_id": status["id"],
        "op": "not_in",
        "value": ["Closed", "False Positive"],
    }
    crit_high = {
        "property_id": severity["id"],
        "op": "in",
        "value": [
            ws.option("Vulnerabilities", "Severity", "Critical"),
            ws.option("Vulnerabilities", "Severity", "High"),
        ],
    }
    ws.add(
        "Engagements",
        "Asset Count",
        "rollup",
        relation_property_id=assets["id"],
        target_property_id=ws.prop("Assets", "Identifier")["id"],
        function="count",
    )
    ws.add(
        "Engagements",
        "Valid Findings",
        "rollup",
        relation_property_id=findings["id"],
        target_property_id=status["id"],
        function="count",
        filters=[{"property_id": status["id"], "op": "not_equals", "value": "False Positive"}],
    )
    open_ch = ws.add(
        "Engagements",
        "Open Crit/High",
        "rollup",
        relation_property_id=findings["id"],
        target_property_id=status["id"],
        function="count",
        filters=[open_filter, crit_high],
    )
    ws.add(
        "Engagements",
        "Highest CVSS",
        "rollup",
        relation_property_id=findings["id"],
        target_property_id=cvss["id"],
        function="max",
    )

    engagements = ws.rows("Engagements")
    benchmark = {  # projects/secops.md, "Rollup & Edge-Case Verification Benchmarks"
        "ENG-001": (4, 6, 3, 9.8),
        "ENG-002": (4, 4, 3, 9.4),
        "ENG-003": (3, 4, 2, 9.0),
        "ENG-004": (1, 1, 1, 8.2),
    }
    for key, (n_assets, valid, open_count, max_cvss) in benchmark.items():
        row = engagements[key]
        got = (
            row["Asset Count"],
            row["Valid Findings"],
            row["Open Crit/High"],
            row["Highest CVSS"],
        )
        assert got == (n_assets, valid, open_count, max_cvss), key
        # ...and the seed's own precomputed columns agree.
        assert got == (
            row["Total Assets"],
            row["Total Valid Findings"],
            row["Open Critical High"],
            row["Max CVSS"],
        )

    # New property types came in typed.
    first = engagements["ENG-001"]
    assert first["Dates"] == {"start": "2026-11-02", "end": "2026-12-04"}
    assert first["Rules of Engagement PDF"] == [
        {
            "name": "ENG-001_nordicfin_roe.pdf",
            "url": "s3://ymer-secops/roe/ENG-001_nordicfin_roe.pdf",
        }
    ]
    # "K. Lind" is a member: matched by name. "M. Vance" is not: kept as a name.
    assert first["Lead"] == [{"id": str(team.lind.id), "display_name": "K. Lind"}]
    assert engagements["ENG-002"]["Lead"] == [{"id": None, "display_name": "M. Vance"}]
    assert engagements["ENG-004"]["Rules of Engagement PDF"] is None

    # Per asset: open findings, and the worst open severity (option order: Critical first).
    asset_findings = ws.prop("Assets", "Vulnerabilities")
    ws.add(
        "Assets",
        "Open Now",
        "rollup",
        relation_property_id=asset_findings["id"],
        target_property_id=status["id"],
        function="count",
        filters=[open_filter],
    )
    ws.add(
        "Assets",
        "Worst Open",
        "rollup",
        relation_property_id=asset_findings["id"],
        target_property_id=severity["id"],
        function="top_option",
        filters=[open_filter],
    )
    for key, row in ws.rows("Assets").items():
        assert row["Open Now"] == row["Open Findings"], key
        expected = one(
            ws.option_name("Assets", "Highest Open Severity", row["Highest Open Severity"])
        )
        assert row["Worst Open"] == (None if expected == "None" else expected), key

    # Two relations: Engagement -> Assets -> Remediation Tasks still open.
    stage = ws.prop("Remediation Tasks", "Stage")
    ws.add(
        "Engagements",
        "Open Remediation",
        "rollup",
        relation_property_id=assets["id"],
        through_property_id=ws.prop("Assets", "Remediation Tasks")["id"],
        target_property_id=stage["id"],
        function="count",
        through_filters=[{"property_id": stage["id"], "op": "not_equals", "value": "Done"}],
    )
    assert ws.rows("Engagements")["ENG-001"]["Open Remediation"] == 4  # REM-001,2,3,5

    # Stress test: VULN-002 moves from Retest to Closed; ENG-001 drops from 3 to 2.
    vuln = ws.rows("Vulnerabilities")["VULN-002"]
    ws.set(
        "Vulnerabilities",
        vuln["_id"],
        status["id"],
        ws.option("Vulnerabilities", "Status", "Closed"),
    )
    after = ws.rows("Engagements")
    assert after["ENG-001"][open_ch["name"]] == 2
    assert after["ENG-001"]["Valid Findings"] == 6  # still valid, just closed


# --- School ---------------------------------------------------------------------------


def test_school_blueprint(client, team):
    ws, summary = import_zip(client, team, SCHOOL_ZIP)
    assert ws.root["title"] == "Ymer School Seed Data Bundle"
    assert sorted(ws.databases) == [
        "Assignments",
        "Courses",
        "Enrollments",
        "Grades",
        "Semesters",
        "Students",
    ]
    assert summary["rows"] == 86
    assert summary["relations"] == 8
    assert summary["warnings"] == []

    grade_kinds = {p["name"]: p["type"] for p in ws.schema("Grades")["properties"]}
    # Copies of the related rows' names are gone; copied values are lookups now.
    assert "Student Name" not in grade_kinds and "Assignment Title" not in grade_kinds
    assert grade_kinds["Max Points"] == grade_kinds["Weight Pct"] == "rollup"
    enrollment_kinds = {p["name"]: p["type"] for p in ws.schema("Enrollments")["properties"]}
    assert "Semester Name" not in enrollment_kinds and "Student Name" not in enrollment_kinds
    assert enrollment_kinds["Semester"] == "relation"

    # Grades: Percentage = (Score / Max Points) * 100, Max Points looked up from the assignment.
    pct = ws.add(
        "Grades", "Percent", "formula", expression='prop("Score") / prop("Max Points") * 100'
    )
    for key, row in ws.rows("Grades").items():
        assert row["Percent"] == row["Percentage"], key

    # Enrollments: the weighted average of this student's grades in this course.
    grades_of_course = ws.prop("Courses", "Grades")
    ws.add(
        "Enrollments",
        "Weighted Grade",
        "rollup",
        relation_property_id=ws.prop("Enrollments", "Course")["id"],
        through_property_id=grades_of_course["id"],
        through_filters=[
            {
                "property_id": ws.prop("Grades", "Student")["id"],
                "op": "contains",
                "value": {"this": ws.prop("Enrollments", "Student")["id"]},
            }
        ],
        target_property_id=pct["id"],
        function="weighted_average",
        weight_property_id=ws.prop("Grades", "Weight Pct")["id"],
    )
    # Completed terms have no grade records, only the final grade: use that there.
    ws.add(
        "Enrollments",
        "Grade",
        "formula",
        expression='if(empty(prop("Weighted Grade")), prop("Current Numeric Grade"), '
        'round(prop("Weighted Grade"), 1))',
    )
    ws.add(
        "Enrollments",
        "Letter",
        "formula",
        expression='if(empty(prop("Grade")), "", ifs(prop("Grade") >= 90, "A", '
        'prop("Grade") >= 80, "B", prop("Grade") >= 70, "C", prop("Grade") >= 60, "D", "F"))',
    )
    ws.add(
        "Enrollments",
        "Points",
        "formula",
        expression='ifs(prop("Letter") == "A", 4, prop("Letter") == "B", 3, '
        'prop("Letter") == "C", 2, prop("Letter") == "D", 1, prop("Letter") == "F", 0, "")',
    )
    enrollments = ws.rows("Enrollments")
    for key, row in enrollments.items():
        if row["Weighted Grade"] is not None:
            assert r1(row["Weighted Grade"]) == row["Current Numeric Grade"], key
        assert row["Grade"] == row["Current Numeric Grade"], key
        letter = ws.option_name("Enrollments", "Letter Grade", row["Letter Grade"])
        assert row["Letter"] == letter, key
        assert row["Points"] == row["Grade Points"], key
    assert r1(enrollments["ENR-015"]["Weighted Grade"]) == 24.2  # the missing midterm counts as 0

    # Courses <- Enrollments: enrolled count (Enrolled or Completed) and average grade.
    course_enrollments = ws.prop("Courses", "Enrollments")
    enrollment_status = ws.prop("Enrollments", "Status")
    enrolled = ws.add(
        "Courses",
        "Enrolled",
        "rollup",
        relation_property_id=course_enrollments["id"],
        target_property_id=enrollment_status["id"],
        function="count",
        filters=[
            {
                "property_id": enrollment_status["id"],
                "op": "in",
                "value": ["Enrolled", "Completed"],
            }
        ],
    )
    ws.add(
        "Courses",
        "Average",
        "rollup",
        relation_property_id=course_enrollments["id"],
        target_property_id=ws.prop("Enrollments", "Grade")["id"],
        function="average",
    )
    courses = {row["Course Code"]: row for row in ws.rows("Courses").values()}
    for code, (count, average) in {  # projects/school_platform.md benchmarks
        "CS201": (4, 73.8),
        "MATH205": (4, 86.6),
        "PHYS150": (3, 88.6),
        "DS210": (3, 86.5),
    }.items():
        assert (courses[code]["Enrolled"], r1(courses[code]["Average"])) == (count, average), code
    for code, row in courses.items():
        assert row["Enrolled"] == row["Enrolled Count"], code
        assert r1(row["Average"]) == row["Avg Numeric Grade"], code

    # Students <- Enrollments: GPA, and credits of completed courses (two relations).
    student_enrollments = ws.prop("Students", "Enrollments")
    ws.add(
        "Students",
        "GPA",
        "rollup",
        relation_property_id=student_enrollments["id"],
        target_property_id=ws.prop("Enrollments", "Points")["id"],
        function="average",
    )
    ws.add(
        "Students",
        "Credits",
        "rollup",
        relation_property_id=student_enrollments["id"],
        filters=[
            {
                "property_id": enrollment_status["id"],
                "op": "equals",
                "value": ws.option("Enrollments", "Status", "Completed"),
            }
        ],
        through_property_id=ws.prop("Enrollments", "Course")["id"],
        target_property_id=ws.prop("Courses", "Credits")["id"],
        function="sum",
    )
    students = ws.rows("Students")
    for key, (gpa, credits) in {
        "STU-001": (3.5, 15),
        "STU-003": (4.0, 7.5),
        "STU-005": (0.0, 0),
    }.items():
        assert (students[key]["GPA"], students[key]["Credits"]) == (gpa, credits), key
    for key, row in students.items():
        assert row["GPA"] == row["Cumulative GPA"], key
        assert row["Credits"] == row["Credits Earned"], key

    first = students["STU-001"]
    assert first["Advisor"] == [{"id": None, "display_name": "Dr. Henrik Holm"}]
    assert first["Transcript PDF"][0]["name"] == "STU-001_transcript.pdf"
    semester = ws.rows("Semesters")["SEM-2026-FALL"]
    assert semester["Dates"] == {"start": "2026-08-31", "end": "2026-12-18"}

    # Stress test: ENR-023 (Maya Chen, DS210) moves from Waitlisted to Enrolled. The
    # enrolled count goes from 3 to 4; she has no grades yet, so the average holds.
    ws.set(
        "Enrollments",
        enrollments["ENR-023"]["_id"],
        enrollment_status["id"],
        ws.option("Enrollments", "Status", "Enrolled"),
    )
    ds210 = next(r for r in ws.rows("Courses").values() if r["Course Code"] == "DS210")
    assert ds210[enrolled["name"]] == 4
    assert r1(ds210["Average"]) == 86.5
