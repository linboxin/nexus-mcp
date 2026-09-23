"""OAuth 2.1 sign-in for the hosted HTTP server ("Connect" button in Claude.ai, ChatGPT, …).

Clients that can't paste a bearer header (Claude.ai / Claude mobile custom
connectors, ChatGPT) speak MCP's OAuth profile: dynamic client registration,
then authorization code + PKCE. This module is that authorization server.

The "login" step is Nexus itself. ``/authorize`` sends the browser to our
``/oauth/connect`` page, which walks the student through Union's Okta sign-in
(Moodle's mobile launch flow) and asks for the ``ltgopenlmsapp://token=…``
link Nexus shows at the end. We verify the link's passport, ask Moodle who the
token belongs to, and only issue an authorization code if that is the server's
owner. As a bonus the fresh Nexus token replaces the stored one, so
reconnecting also repairs an expired token.

Single-owner by design: the server already acts as one student (its stored
token), so OAuth only proves "this is that student". Every secret we issue is
stored hashed in a 0600 JSON file; static bearer tokens keep working for
clients that prefer a header (Grok Bot, Claude Code, scripts).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from .auth import StoredToken, build_launch_url, new_passport, parse_token_url
from .errors import NexusError

STATE_FILENAME = "oauth-state.json"
CONNECT_PATH = "/oauth/connect"
SCOPE = "nexus"

PENDING_TTL = 20 * 60  # time to finish the Okta sign-in and paste the link
CODE_TTL = 5 * 60
ACCESS_TTL = 8 * 3600
REFRESH_TTL = 90 * 86400
MAX_ATTEMPTS = 5

Identify = Callable[[str], Awaitable[StoredToken]]
OnToken = Callable[[StoredToken], Awaitable[None] | None]


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _new_secret(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)  # 256 bits


@dataclass
class _State:
    owner_user_id: int | None = None
    clients: dict[str, dict[str, Any]] = field(default_factory=dict)
    pending: dict[str, dict[str, Any]] = field(default_factory=dict)
    codes: dict[str, dict[str, Any]] = field(default_factory=dict)  # sha256(code) -> record
    access: dict[str, dict[str, Any]] = field(default_factory=dict)  # sha256(token) -> record
    refresh: dict[str, dict[str, Any]] = field(default_factory=dict)


class NexusOAuthProvider:
    """``OAuthAuthorizationServerProvider`` whose login is a Nexus (Okta) sign-in."""

    def __init__(
        self,
        *,
        public_url: str,
        site_url: str,
        service: str,
        state_dir: Path,
        identify: Identify,
        owner_user_id: int | None = None,
        on_new_token: OnToken | None = None,
        static_tokens: list[str] | None = None,
        url_scheme: str = "nexusmcp",
    ) -> None:
        self.public_url = public_url.rstrip("/")
        self.site_url = site_url
        self.service = service
        self.url_scheme = url_scheme
        self._path = Path(state_dir) / STATE_FILENAME
        self._identify = identify
        self._on_new_token = on_new_token
        self._static = [t for t in (static_tokens or []) if t]
        self._lock = asyncio.Lock()
        self._state = self._load()
        if owner_user_id is not None:
            self.set_owner(owner_user_id)

    # ------------------------------------------------------------------ storage

    def _load(self) -> _State:
        try:
            raw = json.loads(self._path.read_text("utf-8"))
        except FileNotFoundError:
            return _State()
        return _State(**{k: v for k, v in raw.items() if k in _State.__dataclass_fields__})

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self._state.__dict__, fh)
        os.replace(tmp, self._path)

    def _prune(self, now: float) -> None:
        s = self._state
        s.pending = {k: v for k, v in s.pending.items() if v["expires_at"] > now}
        s.codes = {k: v for k, v in s.codes.items() if v["expires_at"] > now}
        s.access = {k: v for k, v in s.access.items() if v["expires_at"] > now}
        s.refresh = {k: v for k, v in s.refresh.items() if v["expires_at"] > now}

    @property
    def owner_user_id(self) -> int | None:
        return self._state.owner_user_id

    def set_owner(self, user_id: int) -> None:
        self._state.owner_user_id = int(user_id)
        self._save()

    # ------------------------------------------------------------------ clients

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = self._state.clients.get(client_id)
        return OAuthClientInformationFull.model_validate(data) if data else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        async with self._lock:
            self._state.clients[client_info.client_id] = client_info.model_dump(mode="json", exclude_none=True)
            self._save()

    # ------------------------------------------------------------------ authorize

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if self._state.owner_user_id is None:
            raise AuthorizeError(
                error="temporarily_unavailable",
                error_description="This Nexus server has no owner yet: run `nexus-mcp login` on the server first.",
            )
        now = time.time()
        request_id = secrets.token_urlsafe(24)
        async with self._lock:
            self._prune(now)
            self._state.pending[request_id] = {
                "client_id": client.client_id,
                "client_name": client.client_name or "",
                "params": params.model_dump(mode="json"),
                "passport": new_passport(),
                "attempts": 0,
                "expires_at": now + PENDING_TTL,
            }
            self._save()
        return f"{self.public_url}{CONNECT_PATH}?req={request_id}"

    def launch_url(self, request_id: str) -> str | None:
        pending = self._state.pending.get(request_id)
        if not pending or pending["expires_at"] <= time.time():
            return None
        return build_launch_url(self.site_url, pending["passport"], service=self.service, url_scheme=self.url_scheme)

    def pending_client_name(self, request_id: str) -> str | None:
        pending = self._state.pending.get(request_id)
        return None if pending is None else (pending["client_name"] or pending["client_id"])

    async def complete(self, request_id: str, token_link: str) -> str:
        """Check the pasted Nexus link; return the client redirect URL carrying the code.

        Raises ``ConnectError`` with a user-facing message on failure.
        """
        async with self._lock:
            now = time.time()
            self._prune(now)
            pending = self._state.pending.get(request_id)
            if pending is None:
                raise ConnectError("This sign-in request expired. Go back to your app and press Connect again.")
            pending["attempts"] += 1
            if pending["attempts"] > MAX_ATTEMPTS:
                self._state.pending.pop(request_id, None)
                self._save()
                raise ConnectError("Too many attempts. Go back to your app and press Connect again.")
            self._save()
            passport = pending["passport"]
        try:
            bundle = parse_token_url(token_link, site_url=self.site_url, passport=passport)
            identity = await self._identify(bundle.token)
        except NexusError as exc:
            raise ConnectError(exc.message) from exc
        if identity.user_id is None or identity.user_id != self._state.owner_user_id:
            raise ConnectError(
                f"You signed in to Nexus as {identity.fullname or 'another user'}, but this server belongs to a "
                "different student. Only the server's owner can connect."
            )
        if self._on_new_token is not None:
            result = self._on_new_token(identity)
            if asyncio.iscoroutine(result):
                await result
        params = AuthorizationParams.model_validate(pending["params"])
        code = _new_secret("nxc_")
        async with self._lock:
            self._state.pending.pop(request_id, None)
            self._state.codes[_hash(code)] = {
                "client_id": pending["client_id"],
                "scopes": params.scopes or [SCOPE],
                "code_challenge": params.code_challenge,
                "redirect_uri": str(params.redirect_uri),
                "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "resource": params.resource,
                "subject": str(identity.user_id),
                "expires_at": time.time() + CODE_TTL,
            }
            self._save()
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    # ------------------------------------------------------------------ codes + tokens

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        record = self._state.codes.get(_hash(authorization_code))
        if not record or record["client_id"] != client.client_id or record["expires_at"] <= time.time():
            return None
        return AuthorizationCode(code=authorization_code, **record)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        async with self._lock:
            if self._state.codes.pop(_hash(authorization_code.code), None) is None:  # single use
                raise TokenError(error="invalid_grant", error_description="authorization code already used")
            token = self._issue(client.client_id, authorization_code.scopes, authorization_code.resource,
                                authorization_code.subject)
            self._save()
            return token

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        record = self._state.refresh.get(_hash(refresh_token))
        if not record or record["client_id"] != client.client_id or record["expires_at"] <= time.time():
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=record["client_id"],
            scopes=record["scopes"],
            expires_at=int(record["expires_at"]),
            resource=record.get("resource"),
            subject=record.get("subject"),
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        async with self._lock:
            old = self._state.refresh.pop(_hash(refresh_token.token), None)  # rotate
            if old is None:
                raise TokenError(error="invalid_grant", error_description="refresh token already used or revoked")
            self._state.access.pop(old.get("access_hash", ""), None)
            token = self._issue(client.client_id, scopes or refresh_token.scopes, refresh_token.resource,
                                refresh_token.subject)
            self._save()
            return token

    def _issue(self, client_id: str, scopes: list[str], resource: str | None, subject: str | None) -> OAuthToken:
        now = time.time()
        self._prune(now)
        access, refresh = _new_secret("nxa_"), _new_secret("nxr_")
        base = {"client_id": client_id, "scopes": scopes, "resource": resource, "subject": subject}
        self._state.access[_hash(access)] = {**base, "expires_at": now + ACCESS_TTL, "refresh_hash": _hash(refresh)}
        self._state.refresh[_hash(refresh)] = {**base, "expires_at": now + REFRESH_TTL, "access_hash": _hash(access)}
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=ACCESS_TTL,
            refresh_token=refresh,
            scope=" ".join(scopes) if scopes else None,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        for secret in self._static:
            if token and hmac.compare_digest(token, secret):
                return AccessToken(token=token, client_id="nexus-remote", scopes=[SCOPE])
        record = self._state.access.get(_hash(token))
        if not record or record["expires_at"] <= time.time():
            return None
        return AccessToken(
            token=token,
            client_id=record["client_id"],
            scopes=record["scopes"],
            expires_at=int(record["expires_at"]),
            resource=record.get("resource"),
            subject=record.get("subject"),
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        async with self._lock:
            h = _hash(token.token)
            access = self._state.access.pop(h, None)
            refresh = self._state.refresh.pop(h, None)
            if access:
                self._state.refresh.pop(access.get("refresh_hash", ""), None)
            if refresh:
                self._state.access.pop(refresh.get("access_hash", ""), None)
            self._save()

    async def revoke_all(self) -> int:
        """Sign every connected app out (keeps registered clients and the owner)."""
        async with self._lock:
            n = len(self._state.refresh)
            self._state.access.clear()
            self._state.refresh.clear()
            self._state.codes.clear()
            self._save()
            return n


class ConnectError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# --------------------------------------------------------------------------- #
# The /oauth/connect page
# --------------------------------------------------------------------------- #

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect Nexus</title>
<style>
:root {{ --bg:#f6f5f2; --card:#fff; --ink:#1d1d1b; --muted:#6b6a66; --line:#e3e1dc; --accent:#822433; --err:#b3261e; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#161615; --card:#201f1e; --ink:#eeede9; --muted:#a3a19b; --line:#3a3936; --accent:#e0848f; --err:#ff8a80; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 -apple-system, system-ui, sans-serif; }}
main {{ max-width:520px; margin:0 auto; padding:32px 16px 48px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; padding:24px; }}
h1 {{ font-size:22px; margin:0 0 4px; }}
p.sub {{ color:var(--muted); margin:0 0 20px; }}
ol {{ padding-left:20px; margin:0; }} li {{ margin:0 0 18px; }}
a.btn, button {{ display:inline-block; background:var(--accent); color:#fff; border:0; border-radius:10px;
  padding:11px 18px; font:600 15px system-ui, sans-serif; text-decoration:none; cursor:pointer; }}
button.ghost {{ background:transparent; color:var(--accent); border:1px solid var(--line); }}
textarea {{ width:100%; min-height:84px; margin:8px 0 10px; padding:10px; border:1px solid var(--line); border-radius:10px;
  background:var(--bg); color:var(--ink); font:13px ui-monospace, monospace; }}
.hint {{ color:var(--muted); font-size:14px; }}
.err {{ color:var(--err); background:color-mix(in srgb, var(--err) 10%, transparent); padding:10px 12px; border-radius:10px; margin:0 0 16px; }}
.row {{ display:flex; gap:8px; flex-wrap:wrap; }}
</style></head>
<body><main><div class="card">
{body}
</div><p class="hint" style="margin-top:14px">Read-only access to your Union College Nexus. Unofficial; not affiliated with Union College.</p></main>
<script>
const b = document.getElementById("paste");
if (b && navigator.clipboard && navigator.clipboard.readText) {{
  b.addEventListener("click", async () => {{
    try {{ document.getElementById("link").value = await navigator.clipboard.readText(); }} catch (e) {{}}
  }});
}} else if (b) {{ b.style.display = "none"; }}
</script>
</body></html>"""


def render_connect_page(*, client_name: str, launch_url: str, request_id: str, error: str | None = None) -> str:
    esc = html.escape
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    body = f"""
<h1>Connect Nexus</h1>
<p class="sub"><b>{esc(client_name)}</b> wants read-only access to your Nexus courses, assignments and grades.</p>
{err}
<ol>
<li><b>Sign in to Nexus.</b><br>
<a class="btn" href="{esc(launch_url)}" target="_blank" rel="noopener">Sign in with Union Okta</a><br>
<span class="hint">Opens in a new tab. Finish the Okta sign-in until Nexus says <i>"Your registration has been confirmed"</i>.</span></li>
<li><b>Copy the app link.</b><br>
<span class="hint">On that page, right-click (on a phone: long-press) <i>"Click here if the app does not open automatically"</i>
and choose <b>Copy Link</b>. Don't tap it.</span></li>
<li><b>Paste it here.</b>
<form method="post" action="{CONNECT_PATH}">
<input type="hidden" name="req" value="{esc(request_id)}">
<textarea id="link" name="link" placeholder="ltgopenlmsapp://token=…" required autocomplete="off" autocapitalize="off" spellcheck="false"></textarea>
<div class="row"><button type="submit">Connect</button><button type="button" class="ghost" id="paste">Paste from clipboard</button></div>
</form></li>
</ol>"""
    return _PAGE.format(body=body)


def render_message(title: str, message: str) -> str:
    esc = html.escape
    return _PAGE.format(body=f"<h1>{esc(title)}</h1><p class='sub'>{esc(message)}</p>")


def connect_routes(provider: NexusOAuthProvider):
    """Starlette endpoint for GET/POST ``/oauth/connect``."""
    from starlette.requests import Request
    from starlette.responses import HTMLResponse, RedirectResponse

    headers = {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"}

    async def endpoint(request: Request):
        if request.method == "POST":
            form = await request.form()
            request_id = str(form.get("req") or "")
            link = str(form.get("link") or "")
            try:
                redirect = await provider.complete(request_id, link)
            except ConnectError as exc:
                launch = provider.launch_url(request_id)
                if launch is None:
                    return HTMLResponse(render_message("Sign-in expired", exc.message), status_code=400, headers=headers)
                page = render_connect_page(
                    client_name=provider.pending_client_name(request_id) or "An app",
                    launch_url=launch,
                    request_id=request_id,
                    error=exc.message,
                )
                return HTMLResponse(page, status_code=400, headers=headers)
            return RedirectResponse(redirect, status_code=303, headers=headers)
        request_id = request.query_params.get("req", "")
        launch = provider.launch_url(request_id)
        if launch is None:
            return HTMLResponse(
                render_message("Sign-in expired", "Go back to your app and press Connect again."),
                status_code=400,
                headers=headers,
            )
        page = render_connect_page(
            client_name=provider.pending_client_name(request_id) or "An app", launch_url=launch, request_id=request_id
        )
        return HTMLResponse(page, headers=headers)

    return endpoint
