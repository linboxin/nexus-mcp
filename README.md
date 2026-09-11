# Nexus MCP

A read-only [MCP](https://modelcontextprotocol.io) server for Union College's **Nexus** (Moodle 4.5, hosted by Open LMS). Connect it to Claude and ask:

> "What's due this week?" · "What's overdue?" · "Did I submit Lab 3?" · "What's my grade in CSC-385?" · "What changed in my courses today?" · "Find the lecture notes on shaders." · "Give me my daily briefing." · "What should I work on next?"

It uses Moodle's official web-service API with your own student token. It never scrapes the website and never writes anything. Unofficial: not affiliated with or endorsed by Union College or Open LMS.

## Tools

| Area | Tools |
|---|---|
| Courses | `list_courses` (active/past/future, term, teacher), `get_course` (sections, materials, activities), `nexus_status` |
| Assignments | `upcoming_assignments`, `overdue_assignments`, `assignment_details`, `submission_status` |
| Grades | `current_grades`, `course_grade`, `grade_history` |
| Materials | `search_course_materials`, `get_material` (pages, books, links, text files; PDFs with the `pdf` extra) |
| Calendar | `upcoming_events` |
| Announcements | `recent_announcements`, `course_updates` |
| Intelligence | `daily_briefing`, `weekly_briefing`, `workload_analysis`, `what_should_i_do_next` |

Times are in your academic timezone (America/New_York). Errors carry a code: `NEXUS_AUTH_ERROR`, `NEXUS_PERMISSION_ERROR`, `NEXUS_UNSUPPORTED`, `NEXUS_RESOURCE_NOT_FOUND`, `NEXUS_API_ERROR`.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and a Union student account.

```bash
git clone <this repo> nexus-mcp && cd nexus-mcp
uv sync
uv run nexus-mcp login
uv run nexus-mcp test-connection
```

`login` opens Nexus in your browser. Sign in with Okta as usual. On the page that says *"Your registration has been confirmed"*, click **"Click here if the app does not open automatically."** and allow **Open Nexus MCP Login**. The token lands in your macOS Keychain. No password is ever typed into this tool. Details: [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md).

If the browser shows no prompt (Linux/Windows, or the handler is missing), right-click that link → *Copy Link Address* → paste it into the terminal.

## Connect a client

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "nexus": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/nexus-mcp", "nexus-mcp", "serve"]
    }
  }
}
```

Claude Code:

```bash
claude mcp add nexus -- uv run --directory /ABSOLUTE/PATH/TO/nexus-mcp nexus-mcp serve
```

## Commands

```bash
uv run nexus-mcp test-connection   # reachability, auth, identity, capability matrix
uv run nexus-mcp list-courses      # the Phase 0 proof; --all includes past terms
uv run nexus-mcp whoami
uv run nexus-mcp logout
uv run scripts/test_moodle_api.py --all   # verbose read-only API diagnostics
uv run pytest                      # mocked Moodle, no credentials needed
```

## Configuration

Copy `.env.example` to `.env` if you need to change anything. Defaults target `nexus.union.edu`. Notable keys: `NEXUS_TOKEN` (use an explicit token), `NEXUS_TOKEN_STORAGE=keyring|file`, `NEXUS_TIMEZONE`, and the `NEXUS_CACHE_*_TTL` lifetimes. Cached grade and submission data is always labelled `cached` with an `as_of` time.

## Security

- Read-only: no submission, grading, posting, messaging or enrolment functions exist in this codebase.
- The only secret is your Moodle token: Keychain (or a 0600 file), sent in POST bodies, never logged.
- `.env` and credential files are git-ignored. Revoke the token on Nexus under Preferences → Security keys.

## Responsible use

- Personal use only, on your own account, reading your own data. Union's [Acceptable Use Policy](https://www.union.edu/sites/default/files/information-technology-services/201808/acceptable-use-information-technology-resources.pdf) applies; access to a system is not by itself authorization, so ask ITS if you want that confirmed.
- Never share your token or credential file. Revoke it on Nexus (Preferences → Security keys) if in doubt.
- What your AI client does with course content is governed by each course's AI policy and Union's Honor Code. Use this for planning and finding materials; don't use it to have an AI complete graded work unless the instructor allows it.
- Course materials belong to instructors and publishers. Don't redistribute them.

## Limitations

- Login needs you at the keyboard (Okta/MFA). If Union expires the token, run `login` again.
- Moodle has no grade-history API; `grade_history` returns current grades with dates and says so.
- Material search is a text match over titles, filenames, descriptions and sections. PDF text needs `uv sync --extra pdf`.
- Third-party activities (Turnitin, H5P, LTI) appear as modules without readable content.
- Non-academic enrolments (trainings, campus resources) never end, so they count as "in progress"; use `academic_only` to hide them.

More: [docs/API_FEASIBILITY.md](docs/API_FEASIBILITY.md) (what Nexus exposes and why) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
