"""The agent-facing CLI: every MCP tool as a shell command with JSON output."""

import json

from nexus_mcp import cli, runtime
from tests.conftest import HOUR, NOW, assignment, assignments_payload, course, make_nexus, submission, submissions_by_id


def _run(capsys, argv):
    args = cli.build_parser().parse_args(argv)
    rc = args.func(args)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _courses(fake):
    fake.on("core_enrol_get_users_courses", [course(3, "Web Programming", "26/FA.CSC-240-01"), course(2, "Academic Integrity Training", "AcademicIntegrity")])
    fake.on("core_course_get_courses_by_field", {"courses": [], "warnings": []})


def test_courses_alias_outputs_json(fake, capsys):
    _courses(fake)
    runtime.set_nexus(make_nexus())
    rc, out, _ = _run(capsys, ["courses", "--academic"])
    assert rc == 0
    data = json.loads(out)
    assert [c["id"] for c in data["courses"]] == [3] and data["courses"][0]["term"] == "Fall 2026"


def test_call_generic_tool_with_kv_args(fake, capsys):
    _courses(fake)
    runtime.set_nexus(make_nexus())
    rc, out, err = _run(capsys, ["call", "list_courses", "classification=all", "academic_only=true"])
    assert rc == 0 and err == ""
    assert json.loads(out)["count"] == 1
    assert cli._parse_kv(["a=1", "b=true", "c=x y", 'd={"k":[1]}']) == {"a": 1, "b": True, "c": "x y", "d": {"k": [1]}}


def test_briefing_prints_text_then_json(fake, capsys):
    _courses(fake)
    fake.on("mod_assign_get_assignments", assignments_payload({3: ("Web Programming", [assignment(11, 3, "Lab 1", due=NOW + 5 * HOUR)])}))
    fake.on("mod_assign_get_submission_status", submissions_by_id({11: submission("new")}))
    fake.on("core_calendar_get_calendar_events", {"events": [], "warnings": []})
    fake.on("core_calendar_get_action_events_by_timesort", {"events": [], "firstid": 0, "lastid": 0})
    fake.on("mod_forum_get_forums_by_courses", [])
    fake.on("core_course_get_updates_since", {"instances": [], "warnings": []})
    fake.on("message_popup_get_popup_notifications", {"notifications": [], "unreadcount": 0})
    runtime.set_nexus(make_nexus())
    rc, out, _ = _run(capsys, ["briefing"])
    assert rc == 0 and "TODAY" in out and "Lab 1" in out and not out.lstrip().startswith("{")
    runtime.set_nexus(make_nexus())
    rc, out, _ = _run(capsys, ["briefing", "--json"])
    assert rc == 0 and json.loads(out)["text"].startswith("Good")
    runtime.set_nexus(make_nexus())
    rc, out, _ = _run(capsys, ["due", "--days", "7", "--pending"])
    assert rc == 0 and json.loads(out)["assignments"][0]["name"] == "Lab 1"


def test_errors_are_clean(fake, capsys):
    runtime.set_nexus(make_nexus())
    rc, out, err = _run(capsys, ["call", "nope"])
    assert rc == 1 and out == "" and "nope" in err
    rc, out, err = _run(capsys, ["call", "list_courses", "bad"])
    assert rc == 2 and "key=value" in err


def test_tools_lists_every_tool(capsys):
    rc, out, _ = _run(capsys, ["tools"])
    assert rc == 0
    assert "daily_briefing()" in out and "upcoming_assignments(days, course_id, include_submitted)" in out


def test_skill_install_and_show(tmp_path, capsys):
    rc, _, err = _run(capsys, ["skill", "install", "--dir", str(tmp_path / "nexus")])
    assert rc == 0 and (tmp_path / "nexus" / "SKILL.md").read_text().startswith("---\nname: nexus")
    rc, out, _ = _run(capsys, ["skill", "show"])
    assert rc == 0 and "uvx union-nexus-mcp briefing" in out


def test_serve_parser_defaults():
    args = cli.build_parser().parse_args(["serve", "--transport", "http", "--port", "9000"])
    assert args.transport == "http" and args.host == "127.0.0.1" and args.port == 9000
