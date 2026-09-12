# Remote agents (Grok Bot, Claude.ai, ChatGPT, hosted assistants)

Local MCP clients launch `nexus-mcp serve` as a subprocess. Agents that run in
someone else's cloud can't do that; they need a **public HTTPS URL** and, in
Grok Bot's case, a static `Authorization: Bearer …` header (its connector form
has no OAuth client-id field). `nexus-mcp expose` gives you exactly that
without moving your Nexus token anywhere.

## Quick start

```bash
brew install cloudflared          # once
uvx union-nexus-mcp expose
```

It starts the HTTP transport on localhost with bearer auth, opens a Cloudflare
*quick tunnel*, and prints:

```text
Server URL:  https://<random>.trycloudflare.com/mcp
Header:      Authorization: Bearer nxs_…
```

**Grok Bot:** Settings → Plugins → add a custom MCP connector → paste both.
The bot's tools are the same 19 read-only tools local clients get.

**Claude.ai / ChatGPT custom connectors:** same URL; put the header in the
connector's auth field. Keep the terminal open; Ctrl-C stops server and tunnel.

## What is protected, and what is not

- The Moodle token stays in your keyring; remote clients only ever hold the
  bearer token, which you can rotate with `expose --new-token` or by deleting
  `http-token.txt` in the config directory.
- Anyone holding the bearer token can read your Nexus data. Treat it like a
  password; don't paste it into shared chats or commit it.
- Requests without the header get HTTP 401. The SDK also serves OAuth
  protected-resource metadata, which is harmless.
- Quick tunnels are ephemeral: the hostname changes each run, so you re-enter
  the URL in the client. For a stable hostname use a named tunnel on a domain
  you own (`cloudflared tunnel create nexus`, `cloudflared tunnel route dns …`),
  then run `nexus-mcp serve --transport http --auth-token … --public-url https://nexus.yourdomain.com`
  behind it. Tailscale Funnel or ngrok work the same way.
- Your machine has to be awake for remote agents to reach it. That is the
  trade-off for not hosting Union credentials on a server; a hosted deployment
  would need per-user OAuth and a real trust story, and is deliberately out of
  scope.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Client reports 401 | Header must be exactly `Authorization: Bearer <token>`; check for a trailing space. |
| Client connects but calls hang or fail after the first | Try `expose --stateless` (plain JSON responses, no SSE session). |
| `cloudflared did not report a public URL` | Network blocks the tunnel; run `cloudflared tunnel --url http://127.0.0.1:8765` by hand to see its error. |
| Tools return NEXUS_AUTH_ERROR | The Nexus token expired: run `nexus-mcp login` on this machine. |
