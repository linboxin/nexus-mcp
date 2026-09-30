# Hosting it 24/7 with OAuth sign-in

`expose` needs your laptop awake. To use Nexus from your phone, Claude.ai,
ChatGPT, Meta Muse and Grok Bot at any hour, run the HTTP server on a small
always-on machine and turn on `--oauth`. Clients then show a normal
**Connect** button: you sign in with Union Okta, no key to paste.

```
Claude.ai / ChatGPT ──OAuth──┐
Muse / Grok / Claude Code ─key┤──> https://nexus.example.com/mcp ──> Caddy ──> nexus-mcp serve --oauth ──> Nexus
```

This is **single-owner**: the server acts as one student (the token stored on
it), and OAuth only proves "you are that student". Nobody else can connect,
even with a valid Union login.

## How the sign-in works

1. The client registers itself (`/register`) and sends you to `/authorize`.
2. You land on the server's **Connect Nexus** page. *Sign in with Union Okta*
   opens Nexus's mobile-app login in a new tab.
3. After Okta, Nexus says *"Your registration has been confirmed"*. Right-click
   (phone: long-press) **"Click here if the app does not open automatically"**
   → *Copy Link*, and paste it into the Connect page. (Union forces that link to
   the Open LMS app's `ltgopenlmsapp://` scheme, so a web server can't catch it
   automatically; the paste is the one manual step.)
4. The server checks the link came from *this* sign-in (passport check), asks
   Moodle whose token it is, and only continues if it is the owner. The fresh
   Nexus token replaces the stored one, so reconnecting also fixes an expired
   token.
5. Your client gets an access token (8 h) and a rotating refresh token (90 days).

Everything the server issues is stored as SHA-256 hashes in
`oauth-state.json` (mode 0600) in the config directory. Delete that file (or
its `access`/`refresh` entries) and restart to sign every app out.

## Setup on a free VM

Any Linux box with a public IP works. Oracle Cloud's *Always Free* tier
(VM.Standard.A1.Flex, 1 OCPU / 6 GB, Ubuntu 24.04) is plenty.

1. **Firewall.** Allow TCP 80 and 443 in the cloud firewall (Oracle: VCN →
   subnet → security list → ingress rules). Oracle's Ubuntu images also ship
   an iptables `REJECT` rule; insert the accepts *above* it:

   ```bash
   sudo iptables -I INPUT 5 -m state --state NEW -p tcp --dport 443 -j ACCEPT
   sudo iptables -I INPUT 5 -m state --state NEW -p tcp --dport 80 -j ACCEPT
   sudo netfilter-persistent save
   ```

2. **DNS.** Add an `A` record for a subdomain (e.g. `nexus`) pointing at the VM.

3. **Install** uv, the server and [Caddy](https://caddyserver.com/docs/install#debian-ubuntu-raspbian):

   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   uv tool install --with "pypdf>=4.0" git+https://github.com/linboxin/nexus-mcp   # pypdf: read PDF lecture slides
   ```

4. **Token.** Pipe it from your Mac straight into the VM's credential file
   (never printed, never in shell history):

   ```bash
   uvx union-nexus-mcp token export --format raw 2>/dev/null \
     | ssh ubuntu@<vm> 'NEXUS_TOKEN_STORAGE=file ~/.local/bin/union-nexus-mcp token import'
   ssh ubuntu@<vm> 'echo NEXUS_TOKEN_STORAGE=file >> ~/.config/nexus-mcp/.env'
   ```

   Keep it in the credential file rather than `NEXUS_TOKEN`, so an OAuth
   reconnect can replace it.

5. **Optional key for header clients** (Muse, Grok Bot, Claude Code):

   ```bash
   umask 077; echo "NEXUS_HTTP_TOKEN=nxs_$(openssl rand -hex 24)" > ~/.config/nexus-mcp/http.env
   ```

6. **Service** `/etc/systemd/system/nexus-mcp.service`:

   ```ini
   [Unit]
   Description=Nexus MCP
   After=network-online.target
   Wants=network-online.target

   [Service]
   User=ubuntu
   EnvironmentFile=/home/ubuntu/.config/nexus-mcp/http.env
   ExecStart=/home/ubuntu/.local/bin/union-nexus-mcp serve --transport http --host 127.0.0.1 --port 8765 --public-url https://nexus.example.com --oauth
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

   Drop `EnvironmentFile` if you skipped step 5 (OAuth only).

7. **Caddy** `/etc/caddy/Caddyfile` (certificates are automatic):

   ```
   nexus.example.com {
       reverse_proxy 127.0.0.1:8765
   }
   ```

   ```bash
   sudo systemctl daemon-reload && sudo systemctl enable --now nexus-mcp && sudo systemctl reload caddy
   ```

Updating later: `uv tool upgrade union-nexus-mcp` (or reinstall from git) and
`sudo systemctl restart nexus-mcp`.

## Connecting each app

| App | How |
|---|---|
| **Claude** (claude.ai, desktop, iPhone/Android) | Settings → Connectors → *Add custom connector* → URL `https://nexus.example.com/mcp` → *Connect*, then the sign-in above. Added once on the web, it shows up on every device. |
| **ChatGPT** | Settings → Apps & Connectors → developer mode → *Create* → same URL, authentication **OAuth**. |
| **Meta Muse** | Muse builds its own MCP client. Tell it: *"Build a custom integration to my Nexus MCP server at https://nexus.example.com/mcp. It uses streamable HTTP and an `Authorization: Bearer` header. Test every tool and save it as a skill."* Enter the key when Muse's secure credential prompt asks, not in the chat. |
| **Grok Bot** | Settings → Plugins → custom MCP connector → URL + header `Authorization: Bearer <key>`. |
| **Claude Code** (any computer) | `claude mcp add --transport http nexus https://nexus.example.com/mcp` and run `/mcp` to sign in, or add `--header "Authorization: Bearer <key>"`. |

## Security notes

- The Nexus token stored on the VM grants full mobile-app access to your
  account, not just reads. Keep the VM patched and SSH key-only; revoke on
  Nexus (Preferences → Security keys) if the machine is ever compromised.
- The key in `http.env` is a password for your data. Rotate it by writing a new
  value and restarting the service.
- Union's Acceptable Use Policy applies. This is for your own account only.
