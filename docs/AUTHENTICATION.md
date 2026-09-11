# Authentication

Nexus MCP never sees a password, an Okta session, a cookie or a client secret.
It holds exactly one secret: the student's own Moodle **web-service token** for
the `moodle_mobile_app` service, obtained through the same flow the official
Moodle / Open LMS mobile app uses with SSO institutions.

## The flow (`nexus-mcp login`)

```
 terminal                      browser                         nexus.union.edu                 union.okta.com
    │  1. build launch URL         │                                   │                              │
    │  passport = random nonce     │                                   │                              │
    │─────── open ────────────────▶│  GET /admin/tool/mobile/launch.php?service=moodle_mobile_app     │
    │                              │      &passport=<nonce>&urlscheme=nexusmcp&confirmed=1            │
    │                              │──────────────────────────────────▶│  require_login()             │
    │                              │◀──────────────── 303 ─────────────│──── SAML AuthnRequest ──────▶│
    │                              │            2. student signs in with Okta (+ MFA) as usual          │
    │                              │◀──────────────── SAML Response (RelayState = launch.php) ─────────│
    │                              │──────────────────────────────────▶│  generate token for user      │
    │                              │◀── HTML page with link ───────────│  nexusmcp://token=<base64>    │
    │  3a. macOS handler app       │  (auto-click) ── nexusmcp:// ──▶ "Nexus MCP Login.app" writes the
    │      writes link to file  ◀──│                                    link to ~/Library/Application
    │  3b. or the student pastes   │                                    Support/nexus-mcp/sso-callback.txt
    │      the link into terminal  │
    │  4. verify md5(wwwroot+passport) == site id in the payload
    │  5. core_webservice_get_site_info with the token → identity
    │  6. store token in the macOS Keychain (or a 0600 file)
```

What Moodle puts in the link (`admin/tool/mobile/launch.php`, Moodle 4.5):

```
<scheme>://token=BASE64( md5($CFG->wwwroot . $passport) ":::" $token [":::" $privatetoken] )
```

* The `passport` check guarantees the token came from *this* login attempt on
  *this* site, not from a link someone else sent.
* `privatetoken` (only present right after login) is used by the mobile app for
  auto-login into the web UI. Nexus MCP discards it.
* `confirmed=1` asks Moodle to render the link on a page instead of a blind
  303 to the custom scheme, so the copy/paste fallback always works.
* **Nexus ignores our `urlscheme`.** Union's admins set Moodle's
  `forcedurlscheme` to `ltgopenlmsapp` (the Open LMS mobile app), so the page's
  link is `ltgopenlmsapp://token=…`. The handler app claims that scheme as
  well as `nexusmcp://` (override with `NEXUS_HANDLER_SCHEMES`). Without a
  handler for it, clicking the link does nothing at all in Chrome — no dialog.

## Where the token lives

| Backend | Location | When |
|---|---|---|
| `keyring` (default) | macOS Keychain item, service `nexus-mcp`, account `nexus.union.edu` | `NEXUS_TOKEN_STORAGE=auto` or `keyring` |
| file | `~/Library/Application Support/nexus-mcp/credentials.json`, mode 0600, directory 0700 | keyring unavailable, or `NEXUS_TOKEN_STORAGE=file` |
| environment | `NEXUS_TOKEN` | overrides both (for CI or another tool's token); never commit it |

`nexus-mcp logout` deletes the local copy. To revoke server-side, open
`https://nexus.union.edu/user/managetoken.php` (Preferences → Security keys)
and reset the *Moodle mobile web service* key.

## The macOS URL handler

`nexus-mcp login` compiles a 3-line AppleScript into
`~/Library/Application Support/nexus-mcp/Nexus MCP Login.app`, registers it for
the `nexusmcp://` and `ltgopenlmsapp://` schemes with Launch Services, and
ad-hoc signs it. The app has
no UI (`LSUIElement`); it writes the URL it receives to `sso-callback.txt`,
which the CLI is polling. The first time, Chrome/Safari/Firefox will ask
"Open Nexus MCP Login?" — allow it. Skip the handler with `--no-handler` (then
paste the link). On Linux/Windows the paste flow is used.

## Non-interactive use

The MCP server itself never starts a login: if no token is stored it returns
`NEXUS_AUTH_ERROR` with the instruction to run `nexus-mcp login`. For scripted
setups, a link obtained manually can be supplied with
`nexus-mcp login --token-url 'nexusmcp://token=…' [--passport <nonce>]`.

## What we deliberately do not do

* No password prompts, no `login/token.php` username/password calls.
* No cookie capture, no browser automation of the Okta pages.
* No use of the `privatetoken` / auto-login key.
* No write functions (`mod_assign_save_submission`, forum posting, messaging).
* No token in URLs (`wstoken` is sent in the POST body), no token in logs.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Browser shows the green "Your registration has been confirmed" banner and clicking the link does nothing | No handler for the link's scheme (Linux/Windows, `--no-handler`, or an older handler app that only knew `nexusmcp://`). Rerun `nexus-mcp login` once to rebuild the handler, or right-click the link → Copy Link Address → paste in the terminal. |
| `site/passport mismatch` | The link came from a different login attempt. Run `nexus-mcp login` again and use the new URL. |
| `NEXUS_AUTH_ERROR: Invalid token` after weeks of use | Union sets a token validity period, or the key was reset. Run `nexus-mcp login`. |
| Keychain prompt when the MCP server starts | A different Python binary is reading the item. Use the same `uv run`/venv for `login` and `serve`, or set `NEXUS_TOKEN_STORAGE=file`. |
| `servicenotavailable` on launch.php | The mobile service was disabled by Union; nothing client-side can fix that. |
