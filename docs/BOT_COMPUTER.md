# Running Nexus MCP on a bot's own computer (Grok Bot)

Grok Bot won't attach a local MCP server, but its cloud computer has a shell,
so it can install this package and use the **CLI** directly: no tunnel, no
server on your side, nothing to keep running. The bot calls commands like
`union-nexus-mcp briefing` and reads the JSON.

The trade-off: your Nexus token lives on the bot's machine (xAI/Cursor
infrastructure). The token grants full mobile-app access to your account, not
just reads, so only do this for a bot you trust, and revoke the token on Nexus
(Preferences → Security keys) if you stop using it.

## 1. Export the token on your Mac

```bash
uvx union-nexus-mcp token export
```

It prints two lines (`NEXUS_URL=…`, `NEXUS_TOKEN=…`). Paste them only into the
bot, ideally through its secrets/environment setting rather than the chat.

## 2. Tell the bot what to do

Paste this into Grok Bot (fill in the token line where indicated):

```text
Install my Union College Nexus tool on your computer and use it whenever I ask about my courses, assignments, deadlines, grades or Nexus.

Setup (run these):
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  uv tool install union-nexus-mcp            # or: uv tool install git+https://github.com/linboxin/nexus-mcp
  mkdir -p ~/.config/nexus-mcp && chmod 700 ~/.config/nexus-mcp
  printf 'NEXUS_URL=<paste>\nNEXUS_TOKEN=<paste>\n' > ~/.config/nexus-mcp/.env && chmod 600 ~/.config/nexus-mcp/.env
  union-nexus-mcp test-connection            # every line should be ✓

Then read `union-nexus-mcp skill show` and keep its commands in your notes. In short:
  union-nexus-mcp briefing        (daily; --weekly for the week)
  union-nexus-mcp due --days 7    (assignments with submission status)
  union-nexus-mcp overdue
  union-nexus-mcp next            (prioritised to-do list)
  union-nexus-mcp grades [COURSE_ID]
  union-nexus-mcp events --days 14
  union-nexus-mcp search <words>  then  union-nexus-mcp call get_material material_id=<id>
  union-nexus-mcp updates --since 24h
  union-nexus-mcp call <tool> key=value   (any tool; `union-nexus-mcp tools` lists them)
Everything is read-only. If a command prints NEXUS_AUTH_ERROR, tell me the token expired; don't retry.
```

The package reads `~/.config/nexus-mcp/.env` automatically, so no environment
variable needs to be exported in every shell.

## 3. Check it

Ask the bot: "What's due this week in my Nexus courses?" It should run
`union-nexus-mcp due --days 7` and answer from the JSON.

## Rotating or revoking

- New token: run `nexus-mcp login` on your Mac, then `token export` again and
  update the bot's `.env`.
- Cut the bot off: revoke the token on Nexus. Every copy stops working at once.

## Other command-driven agents

The same recipe works for any agent with a shell (Codex cloud, Devin, a cron
job on a VPS): install the package, drop the two lines into
`~/.config/nexus-mcp/.env` (Linux) or the platform config dir, use the CLI.
`union-nexus-mcp token import` stores a token given on stdin into the local
keyring/file if you prefer that over `.env`.
