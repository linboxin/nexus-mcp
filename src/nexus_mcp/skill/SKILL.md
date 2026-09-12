---
name: nexus
description: Union College Nexus (Moodle) from the shell - what's due, overdue work, submission status, grades, course materials, calendar, daily briefing. Use whenever the user asks about their courses, assignments, deadlines, grades, or Nexus/Moodle and the `nexus` MCP tools are not connected.
---

# Nexus (Union Moodle) via CLI

Read-only access to the signed-in student's own Nexus account. Every command prints JSON
(briefings print a ready-to-show text summary; add `--json` for the structured version).
All times are already in the student's academic timezone.

Use `uvx union-nexus-mcp <command>` (or `uv run nexus-mcp <command>` inside a checkout).

## Commands

| Question | Command |
|---|---|
| Briefing / what's going on | `uvx union-nexus-mcp briefing` (`--weekly` for the next 7 days) |
| What's due this week | `uvx union-nexus-mcp due --days 7` (`--pending` hides submitted work, `--course ID`) |
| What's overdue | `uvx union-nexus-mcp overdue` |
| What should I work on | `uvx union-nexus-mcp next` |
| Grades | `uvx union-nexus-mcp grades` or `uvx union-nexus-mcp grades COURSE_ID` |
| Calendar | `uvx union-nexus-mcp events --days 14` |
| Find materials | `uvx union-nexus-mcp search shaders lecture` then `uvx union-nexus-mcp call get_material material_id=907904` |
| What changed | `uvx union-nexus-mcp updates --since 24h` |
| Courses | `uvx union-nexus-mcp courses` (`--all` past terms, `--academic` hides trainings) |
| Anything else | `uvx union-nexus-mcp call <tool> key=value ...`; `uvx union-nexus-mcp tools` lists them |

## Rules

- Prefer the one-shot commands (`briefing`, `due`, `next`) over chaining several.
- `NEXUS_AUTH_ERROR` means the token is missing or expired: tell the user to run
  `uvx union-nexus-mcp login` in a terminal (Okta sign-in in the browser). Don't retry.
- `NEXUS_PERMISSION_ERROR` / `NEXUS_UNSUPPORTED`: the student's role or Nexus config
  doesn't allow it; say so, don't guess.
- Never report "nothing due" when the command failed (non-zero exit).
- `freshness.cached: true` on grades/submission data means "as of <time>", not "right now".
- Nothing here can submit, post, or change anything on Nexus.
