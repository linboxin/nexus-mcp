"""MCP server entry point (stdio). Protocol only; no Moodle logic lives here."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server.mcpserver import MCPServer

from . import __version__, runtime
from .tools import register_all

INSTRUCTIONS = """\
Nexus is Union College's Moodle. Everything here is READ-ONLY and comes from the signed-in student's own account.

Prefer the high-level tools so you don't chain many calls:
- "What's due?" / "this week" -> upcoming_assignments(days=7)
- "What's overdue?" -> overdue_assignments()
- "Did I submit X?" -> submission_status(assignment_id) or assignment_details(assignment_id)
- "What's my grade in X?" -> course_grade(course_id); all courses -> current_grades()
- "What changed?" -> course_updates(since="24h")
- "Find the notes on X" -> search_course_materials(query), then get_material(id) to read it
- "Briefing" -> daily_briefing(); "my week" -> weekly_briefing(); "what next?" -> what_should_i_do_next()
- list_courses / get_course only when the student asks about a specific course; nexus_status for identity/timezone.

All times are already in the student's academic timezone (see the `timezone` fields). Submission and grade data can be cached for a few seconds: if `freshness.cached` is true, say "as of <time>" instead of "right now".

Errors carry codes: NEXUS_AUTH_ERROR (tell the student to run `nexus-mcp login` in a terminal to sign in via Okta), NEXUS_PERMISSION_ERROR (their role can't see it), NEXUS_UNSUPPORTED (Nexus disabled that Moodle function), NEXUS_RESOURCE_NOT_FOUND, NEXUS_API_ERROR (Nexus down/unreachable). Never present an empty result as "nothing due" when a tool errored.
"""


@asynccontextmanager
async def _lifespan(_server: MCPServer) -> AsyncIterator[dict]:
    try:
        yield {}
    finally:
        await runtime.close()


def create_server() -> MCPServer:
    server = MCPServer(
        "nexus-mcp",
        title="Nexus (Union College Moodle)",
        version=__version__,
        instructions=INSTRUCTIONS,
        lifespan=_lifespan,
    )
    register_all(server)
    return server


def main() -> None:
    create_server().run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
