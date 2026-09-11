import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from nexus_mcp import runtime
from nexus_mcp.server import create_server
from tests.conftest import SITE, course, make_settings

EXPECTED_TOOLS = {
    "list_courses", "get_course", "upcoming_assignments", "overdue_assignments", "assignment_details", "submission_status",
    "current_grades", "course_grade", "grade_history", "search_course_materials", "get_material", "upcoming_events",
    "recent_announcements", "course_updates", "daily_briefing", "weekly_briefing", "workload_analysis", "what_should_i_do_next",
    "nexus_status",
}


async def test_all_tools_registered_and_read_only():
    server = create_server()
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names
    for t in tools:
        assert t.annotations is not None and t.annotations.read_only_hint is True, t.name
        assert t.description


async def test_call_tool_returns_structured_courses(fake, nexus):
    fake.on("core_enrol_get_users_courses", [course(1, "CSC-385 - Computer Graphics", "CSC-385")])
    fake.on("core_course_get_courses_by_field", {"courses": [{"id": 1, "contacts": [{"id": 9, "fullname": "Prof. Ada Lovelace"}]}], "warnings": []})
    runtime.set_nexus(nexus)
    try:
        server = create_server()
        result = await server.call_tool("list_courses", {})
        assert not result.is_error
        payload = result.structured_content
        assert payload["count"] == 1
        assert payload["courses"][0]["name"] == "CSC-385 - Computer Graphics"
        assert payload["courses"][0]["teacher"] == "Prof. Ada Lovelace"
        text = json.loads(result.content[0].text)
        assert text["courses"][0]["id"] == 1
    finally:
        runtime.set_nexus(None)


async def test_call_tool_without_login_reports_auth_error(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_URL", SITE)
    monkeypatch.setenv("NEXUS_TOKEN_STORAGE", "file")
    monkeypatch.setenv("NEXUS_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NEXUS_TOKEN", raising=False)
    monkeypatch.setenv("NEXUS_ENV_FILE", str(tmp_path / "nonexistent.env"))
    runtime.set_nexus(None)
    server = create_server()
    with pytest.raises(ToolError) as info:
        await server.call_tool("upcoming_assignments", {"days": 7})
    assert "NEXUS_AUTH_ERROR" in str(info.value)
    assert "nexus-mcp login" in str(info.value)


async def test_call_tool_invalid_argument(fake, nexus):
    runtime.set_nexus(nexus)
    try:
        server = create_server()
        with pytest.raises(ToolError, match="NEXUS_INVALID_ARGUMENT"):
            await server.call_tool("course_updates", {"since": "whenever"})
    finally:
        runtime.set_nexus(None)


def test_cli_parser_has_all_commands():
    from nexus_mcp.cli import build_parser

    parser = build_parser()
    for cmd in ["login", "logout", "test-connection", "whoami", "list-courses", "serve"]:
        args = parser.parse_args([cmd] if cmd != "login" else ["login", "--no-browser"])
        assert args.command == cmd
