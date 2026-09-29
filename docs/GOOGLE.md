# Reading linked Google Docs, Slides and Sheets

Many instructors post Google links instead of files ("Syllabus - LIVE",
"Timeline - LIVE", daily slides). Union shares those with Union accounts only,
so Nexus itself can't open them. Connect your Union Google account once
(read-only) and `get_material` returns their text, `course_updates` flags when
one is edited (with the added/removed lines), and folders list their files.

Without this step nothing breaks: those tools return the link and say how to
connect.

## 1. Create a Google OAuth client (once, ~5 minutes)

1. Go to <https://console.cloud.google.com/> signed in with any Google
   account, and create a project (e.g. "Nexus MCP").
2. **APIs & Services → Library** → enable **Google Drive API**.
3. **APIs & Services → OAuth consent screen**: choose *External*, fill in the
   app name and your email, and under **Test users** add your Union address
   (`you@union.edu`). Leave it in *Testing*.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID** →
   application type **Desktop app**. Copy the client ID and secret.

## 2. Sign in

```bash
uvx union-nexus-mcp google login --client-id '…apps.googleusercontent.com' --client-secret '…'
```

A browser opens; sign in with your **Union** Google account and allow
read-only Drive access. The refresh token is stored in `google-token.json`
(mode 0600) in the config directory. `google status` shows who is connected;
`google logout` removes it (also revoke at <https://myaccount.google.com/permissions>).

In *Testing* mode Google expires the sign-in after 7 days; publish the consent
screen (still unverified, just you) to keep it.

## 3. Hosted server

Sign in on your laptop, then copy the sign-in to the server:

```bash
uvx union-nexus-mcp google export | ssh ubuntu@<vm> '~/.local/bin/union-nexus-mcp google import'
ssh ubuntu@<vm> 'sudo systemctl restart nexus-mcp'
```

## If Union blocks it

Union's Google Workspace admins can block unverified apps from reading Drive.
If the sign-in page says the app is blocked, this route isn't available;
use your AI app's own Google Drive connector for those links instead (the
tools still hand it the exact file link).
