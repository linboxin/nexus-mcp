# Architecture

```
┌──────────────────────────┐
│ AI client (Claude, …)    │
└────────────┬─────────────┘
             │ MCP over stdio
┌────────────▼─────────────┐   src/nexus_mcp/server.py, tools/*.py
│ MCP layer                │   tool schemas, ToolError mapping, instructions
├──────────────────────────┤
│ Intelligence             │   src/nexus_mcp/intelligence.py — briefings, priorities, workload
├──────────────────────────┤   (composes services; never calls Moodle directly)
│ Nexus/Moodle services    │   src/nexus_mcp/moodle/{courses,assignments,grades,materials,calendar,notifications}.py
│  + models                │   src/nexus_mcp/models/*.py — pydantic output types; moodle/nexus.py facade
├──────────────────────────┤
│ Auth + HTTP              │   src/nexus_mcp/auth/client.py (SSO flow, keyring), moodle/client.py (REST, errors)
└────────────┬─────────────┘
             │ Moodle Web Services (REST, JSON)
┌────────────▼─────────────┐
│ Union Nexus (Moodle 4.5) │
└──────────────────────────┘
```

## Layers and rules

* **MCP layer** (`server.py`, `tools/`) only knows about tool names, argument
  schemas and how to turn `NexusError` into an MCP tool error with its code.
  Tools call `runtime.get_nexus()` and return plain dicts.
* **Intelligence** (`intelligence.py`) composes service calls and renders text.
  Estimates are labelled as heuristics.
* **Service layer** (`moodle/`) owns Moodle knowledge: which function to call,
  how to normalise the payload, which cache TTL applies. `moodle/nexus.py`
  is the facade holding the client, cache, site context and timezone.
* **Auth + HTTP** (`auth/`, `moodle/client.py`) owns the token, the REST
  encoding (`courseids[0]=…`), error mapping, downloads and concurrency.

## Data flow example: "What's due this week?"

```
upcoming_assignments(days=7)
  └─ AssignmentService.upcoming
       ├─ mod_assign_get_assignments (all enrolled courses, 1 call, cached 120 s)
       ├─ filter by effective due date in [now, now+7d]
       └─ mod_assign_get_submission_status ×N (≤6 concurrent, cached 30 s, labelled)
            └─ normalize_submission → not_started/draft/submitted/graded/overdue
```

## Tool → Moodle function map

| Tool | Functions |
|---|---|
| `list_courses` | `core_enrol_get_users_courses`, `core_course_get_courses_by_field` |
| `get_course` | + `core_course_get_contents` |
| `upcoming_assignments`, `overdue_assignments` | `mod_assign_get_assignments`, `mod_assign_get_submission_status` |
| `assignment_details`, `submission_status` | same |
| `current_grades` | `gradereport_overview_get_course_grades` (fallback `gradereport_user_get_grade_items`) |
| `course_grade`, `grade_history` | `gradereport_user_get_grade_items` |
| `search_course_materials` | `core_course_get_contents` (per current course) |
| `get_material` | `core_course_get_course_module`, `mod_page_get_pages_by_courses`, `webservice/pluginfile.php` |
| `upcoming_events` | `core_calendar_get_calendar_events`, `core_calendar_get_action_events_by_timesort` |
| `recent_announcements` | `mod_forum_get_forums_by_courses`, `mod_forum_get_forum_discussions` |
| `course_updates` | `core_course_get_updates_since`, announcements, `message_popup_get_popup_notifications` |
| `daily_briefing`, `weekly_briefing`, `workload_analysis`, `what_should_i_do_next` | composition of the above |
| `nexus_status` | `core_webservice_get_site_info`, `core_user_get_users_by_field` |

## Caching

`cache.TTLCache` stores `(value, fetched_at, ttl)`. Services pass the TTL from
`Settings.cache` (env-configurable):

| Category | Default | Env |
|---|---|---|
| Courses | 15 min | `NEXUS_CACHE_COURSES_TTL` |
| Course info / contents | 15 min | `NEXUS_CACHE_COURSE_INFO_TTL` |
| Assignments list | 2 min | `NEXUS_CACHE_ASSIGNMENTS_TTL` |
| Grades | 30 s | `NEXUS_CACHE_GRADES_TTL` |
| Submission status | 30 s | `NEXUS_CACHE_SUBMISSION_TTL` |
| Calendar | 2 min | `NEXUS_CACHE_CALENDAR_TTL` |
| Announcements / notifications | 2 min | `NEXUS_CACHE_ANNOUNCEMENTS_TTL` |
| Materials (page bodies) | 10 min | `NEXUS_CACHE_MATERIALS_TTL` |

Submission status and grade payloads carry a `freshness` object
(`cached`, `as_of`, `ttl_seconds`, `note`) so a cached value is never presented
as live.

## Errors

`errors.map_moodle_error` maps Moodle's `errorcode`/`exception` to
`NEXUS_AUTH_ERROR`, `NEXUS_PERMISSION_ERROR`, `NEXUS_RESOURCE_NOT_FOUND`,
`NEXUS_UNSUPPORTED` or `NEXUS_API_ERROR`. A failure on a function that is not in
the token's function list is always `NEXUS_UNSUPPORTED`, whatever Moodle says.
Aggregating tools (briefings, `current_grades`, `overdue_assignments`) keep going
when one course fails and report it in `warnings`; they re-raise auth errors.

## Timezone

`SiteContext.tz` = `NEXUS_TIMEZONE` override → the Moodle account timezone
(`core_user_get_users_by_field`) → `America/New_York` (Nexus' site default).
Every timestamp is rendered as a `When {iso, display, short, relative, unix}` in
that zone; "today"/"tomorrow" boundaries use it too.

## Testing

`tests/` mock Moodle at the HTTP layer with `respx` (`FakeMoodle` in
`conftest.py` routes on `wsfunction`). No network, no credentials.

## Doors into the service layer

The MCP layer is deliberately thin so the same service layer serves several
kinds of agent:

| Door | Entry point | Notes |
|---|---|---|
| MCP stdio | `nexus_mcp.server.serve("stdio")` via `nexus-mcp serve` | Default; every tool is an async function over `runtime.get_nexus()`. |
| MCP streamable HTTP | `serve("http", host, port)` via `nexus-mcp serve --transport http` | Same tools at `/mcp`; unauthenticated, localhost/Tailscale only. |
| CLI (JSON) | `nexus-mcp call <tool> key=value`, plus aliases `briefing`, `due`, `overdue`, `next`, `grades`, `events`, `search`, `updates`, `courses` | Goes through `MCPServer.call_tool`, so CLI and MCP behave identically. `nexus-mcp skill install` gives command-driven agents a SKILL.md. |
| Python | `Nexus.from_settings(settings, token)` | Direct use of the service layer; the intelligence module is plain functions over it. |
