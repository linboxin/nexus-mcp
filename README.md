# Nexus MCP

[![CI](https://github.com/linboxin/nexus-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/linboxin/nexus-mcp/actions/workflows/ci.yml) [![PyPI](https://img.shields.io/pypi/v/union-nexus-mcp)](https://pypi.org/project/union-nexus-mcp/) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

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

## Setup (2 minutes)

You need [uv](https://docs.astral.sh/uv/) (one-line installer below) and a Union student account.

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh      # skip if you already have uv
uvx union-nexus-mcp setup
```

`setup` does everything: it opens Nexus in your browser for the Okta sign-in, registers the server with every AI client it finds on your machine (Claude Desktop, Claude Code, Cursor, Windsurf, VS Code, Gemini CLI, Codex CLI), and runs the connection test. Restart the client and ask it what's due.

On the Nexus page that says *"Your registration has been confirmed"*, click **"Click here if the app does not open automatically."** and allow **Open Nexus MCP Login**. That hands the token to the terminal; it is stored in your OS keyring. No password is ever typed into this tool. If no prompt appears, right-click that link → *Copy Link Address* → paste it into the terminal. Details: [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md).

Pick clients explicitly with `uvx union-nexus-mcp setup --client claude-desktop --client cursor`, or add a client later with `uvx union-nexus-mcp install --client <key>` (`clients` lists the keys). `install --dry-run` prints the snippet if you'd rather edit a config by hand:

```json
{ "mcpServers": { "nexus": { "command": "uvx", "args": ["union-nexus-mcp", "serve"] } } }
```

GUI apps often can't see `uvx` on their PATH; `install` writes the absolute path for you (`which uvx` if editing manually).

## Use it from any agent

One service layer, three doors:

| Agent | How |
|---|---|
| MCP clients (Claude Desktop/Code, Cursor, Windsurf, VS Code, Gemini, Codex, custom MCP clients) | `uvx union-nexus-mcp setup` — stdio server, tools listed above |
| Command-driven agents, scripts, cron | `uvx union-nexus-mcp briefing`, `due --days 7`, `overdue`, `next`, `grades`, `events`, `search <q>`, `updates --since 24h`, or `call <tool> key=value` for any tool. JSON out. `uvx union-nexus-mcp skill install` drops a skill file into `~/.claude/skills` so Claude Code knows the commands. |
| Your own Python agent | `from nexus_mcp.moodle.nexus import Nexus` → `Nexus.from_settings(Settings.from_env(), token)` gives every operation as async methods, no protocol in between. |
| Remote agents (Grok Bot, Claude.ai / ChatGPT custom connectors, hosted assistants) | `uvx union-nexus-mcp expose` — serves over a Cloudflare tunnel with a bearer token and prints the two values to paste (Server URL + `Authorization: Bearer …`). Your Nexus token never leaves the machine. Details and a stable-hostname setup: [docs/REMOTE.md](docs/REMOTE.md). |

## Commands

```bash
uvx union-nexus-mcp setup              # login + client config + test
uvx union-nexus-mcp login              # sign in again (token expired / new machine)
uvx union-nexus-mcp test-connection    # reachability, auth, identity, capability matrix
uvx union-nexus-mcp list-courses       # --all includes past terms
uvx union-nexus-mcp install --client cursor --dry-run
uvx union-nexus-mcp tools                  # every MCP tool with its parameters
uvx union-nexus-mcp call daily_briefing --text
uvx union-nexus-mcp expose                 # public HTTPS endpoint for Grok Bot etc. (needs cloudflared)
uvx union-nexus-mcp logout
```

Working from a clone instead: `uv sync`, then `uv run nexus-mcp <command>`; `setup`/`install` then register the checkout itself. `uv run pytest` runs the mocked test-suite; `uv run scripts/test_moodle_api.py --all` is the verbose API diagnostic. Releases: [docs/RELEASING.md](docs/RELEASING.md).

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

More: [docs/API_FEASIBILITY.md](docs/API_FEASIBILITY.md) (what Nexus exposes and why) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [CHANGELOG.md](CHANGELOG.md).

<!-- MCP registry ownership marker -->
mcp-name: io.github.linboxin/nexus-mcp
