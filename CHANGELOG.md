# Changelog

## 0.3.0 (unreleased)

- OAuth sign-in for hosted servers: `serve --transport http --oauth --public-url https://…` makes the server its own OAuth 2.1 authorization server (dynamic client registration, PKCE, rotating refresh tokens, revocation), so Claude.ai, the Claude mobile apps and ChatGPT connect with a **Connect** button. The login step is Union Okta via Moodle's mobile launch flow; only the server's owner (the student whose token it holds) can connect, and a reconnect refreshes an expired Nexus token. Static bearer keys (`NEXUS_HTTP_TOKEN`) keep working alongside for Muse, Grok Bot and Claude Code.
- docs/HOSTING.md: free always-on VM + Caddy + systemd recipe, and per-app connection steps (Claude, ChatGPT, Meta Muse, Grok Bot, Claude Code).

## 0.2.0 (unreleased on PyPI; on `main`)

- Agent CLI: every MCP tool is a shell command with JSON output (`call <tool> key=value`, plus `briefing`, `due`, `overdue`, `next`, `grades`, `events`, `search`, `updates`, `courses`, `tools`).
- `skill install`: packaged SKILL.md for command-driven agents (Claude Code personal skills).
- `expose`: HTTPS endpoint for remote agents (Grok Bot, Claude.ai / ChatGPT custom connectors) via bearer auth + Cloudflare quick tunnel; `serve --transport http` with `--auth-token`, `--public-url`, `--stateless`. Tunnel hostname admitted by DNS-rebinding protection.
- `setup` / `install --client …`: one-command onboarding that registers the server with Claude Desktop, Claude Code, Cursor, Windsurf, VS Code, Gemini CLI and Codex (absolute launcher paths, config backups, `--remove`).
- Windows and Linux URL-scheme handlers for the SSO login (untested on those platforms; paste fallback remains).
- Course `term` and `academic` fields parsed from Union short names; `academic_only` filter.
- MCP registry manifest (`server.json`).
- `token export` / `token import` to move the Nexus token to a bot's computer or a server (docs/BOT_COMPUTER.md).

## 0.1.0 (2026-09-12)

- First release: read-only MCP server for Nexus over stdio; Okta SSO login through Moodle's mobile launch flow (handles Union's forced `ltgopenlmsapp://` scheme); courses, assignments, submission status, grades, materials, calendar, announcements, course updates; daily/weekly briefings, workload analysis, prioritised to-do; `test-connection` capability matrix; mocked test-suite.
