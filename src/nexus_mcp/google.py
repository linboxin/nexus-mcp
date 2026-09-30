"""Read Google Docs / Slides / Sheets that courses link to (optional).

Instructors often post a Google link instead of a file ("Syllabus - LIVE",
lecture slides). Union shares them with Union accounts only, so reading them
needs the student's own Google sign-in: ``nexus-mcp google login`` stores a
read-only Drive refresh token (0600 file, config dir). Without it, tools
still return the link and say how to connect.

Uses the Drive v3 REST API directly (httpx): file metadata for "was it
edited?" (``modifiedTime``) and ``export`` for the text.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .errors import NexusAuthError, NexusError

TOKEN_FILENAME = "google-token.json"
SCOPE = "https://www.googleapis.com/auth/drive.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/drive/v3"
EXPORT_TYPES = {
    "application/vnd.google-apps.document": "text/plain",
    "application/vnd.google-apps.presentation": "text/plain",
    "application/vnd.google-apps.spreadsheet": "text/csv",
}
KINDS = {"document": "Google Doc", "presentation": "Google Slides", "spreadsheets": "Google Sheet",
         "forms": "Google Form", "file": "Drive file", "folder": "Drive folder"}

_PATTERNS = [
    (re.compile(r"docs\.google\.com/(document|presentation|spreadsheets|forms)/(?:u/\d+/)?d/(?:e/)?([A-Za-z0-9_-]{20,})"), None),
    (re.compile(r"drive\.google\.com/(?:u/\d+/)?file/d/([A-Za-z0-9_-]{20,})"), "file"),
    (re.compile(r"drive\.google\.com/(?:u/\d+/)?drive/(?:u/\d+/)?folders/([A-Za-z0-9_-]{20,})"), "folder"),
    (re.compile(r"drive\.google\.com/open\?(?:.*&)?id=([A-Za-z0-9_-]{20,})"), "file"),
]


@dataclass(frozen=True)
class GoogleLink:
    kind: str  # document | presentation | spreadsheets | forms | file | folder
    file_id: str
    url: str

    @property
    def label(self) -> str:
        return KINDS.get(self.kind, "Google file")

    @property
    def readable(self) -> bool:
        return self.kind not in ("forms",)  # form responses aren't ours to read


def parse_google_link(url: str | None) -> GoogleLink | None:
    for pattern, kind in _PATTERNS:
        m = pattern.search(url or "")
        if m:
            if kind is None:
                return GoogleLink(kind=m.group(1), file_id=m.group(2), url=url or "")
            return GoogleLink(kind=kind, file_id=m.group(1), url=url or "")
    return None


def not_connected_note(link: GoogleLink) -> str:
    return (
        f"This is a {link.label} shared with Union accounts only, so Nexus can't open it. "
        "Connect Google once with `nexus-mcp google login` (read-only) to read it here, "
        "or open it with your AI app's Google Drive connector."
    )


# --------------------------------------------------------------------------- #
# Credentials
# --------------------------------------------------------------------------- #


class GoogleCredentials:
    def __init__(self, config_dir: Path) -> None:
        self.path = Path(config_dir) / TOKEN_FILENAME
        self._access: tuple[str, float] | None = None

    def load(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (FileNotFoundError, ValueError):
            return None
        return data if data.get("refresh_token") and data.get("client_id") else None

    @property
    def connected(self) -> bool:
        return self.load() is not None

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    async def access_token(self, http: httpx.AsyncClient) -> str:
        if self._access and self._access[1] > time.time() + 60:
            return self._access[0]
        data = self.load()
        if data is None:
            raise NexusAuthError("Google isn't connected. Run `nexus-mcp google login`.")
        resp = await http.post(
            TOKEN_URL,
            data={
                "client_id": data["client_id"],
                "client_secret": data.get("client_secret", ""),
                "refresh_token": data["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code != 200:
            raise NexusAuthError(
                "Google sign-in expired or was revoked. Run `nexus-mcp google login` again.",
                details={"status": resp.status_code},
            )
        body = resp.json()
        self._access = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
        return self._access[0]


# --------------------------------------------------------------------------- #
# Drive reader
# --------------------------------------------------------------------------- #


class GoogleDrive:
    def __init__(self, config_dir: Path, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.creds = GoogleCredentials(config_dir)
        self._transport = transport

    @property
    def connected(self) -> bool:
        return self.creds.connected

    async def _get(self, http: httpx.AsyncClient, path: str, **params: Any) -> httpx.Response:
        token = await self.creds.access_token(http)
        resp = await http.get(f"{API}{path}", params=params, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code in (403, 404):
            raise NexusError(
                "Google says this file isn't available to your account (not shared with you, or deleted).",
                details={"status": resp.status_code},
            )
        if resp.status_code >= 400:
            raise NexusError(f"Google Drive error {resp.status_code}", details={"body": resp.text[:300]})
        return resp

    async def metadata(self, link: GoogleLink) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30, transport=self._transport) as http:
            resp = await self._get(
                http, f"/files/{link.file_id}",
                fields="id,name,mimeType,modifiedTime,lastModifyingUser(displayName),webViewLink",
                supportsAllDrives="true",
            )
            return resp.json()

    async def read(self, link: GoogleLink, *, max_chars: int = 200_000) -> dict[str, Any]:
        """Metadata plus text (Docs/Slides as plain text, Sheets as CSV, folders as a file list)."""
        async with httpx.AsyncClient(timeout=60, transport=self._transport) as http:
            if link.kind == "folder":
                resp = await self._get(
                    http, "/files", q=f"'{link.file_id}' in parents and trashed = false",
                    fields="files(id,name,mimeType,modifiedTime,webViewLink)", orderBy="modifiedTime desc",
                    pageSize="100", supportsAllDrives="true", includeItemsFromAllDrives="true",
                )
                files = resp.json().get("files", [])
                text = "\n".join(f"- {f['name']} (edited {f.get('modifiedTime', '?')[:10]}) {f.get('webViewLink', '')}" for f in files)
                return {"name": None, "mime_type": "folder", "modified": None, "modified_by": None, "text": text, "files": files}
            meta = (await self._get(
                http, f"/files/{link.file_id}",
                fields="id,name,mimeType,modifiedTime,lastModifyingUser(displayName),webViewLink",
                supportsAllDrives="true",
            )).json()
            mime = meta.get("mimeType", "")
            text = None
            if mime in EXPORT_TYPES:
                resp = await self._get(http, f"/files/{link.file_id}/export", mimeType=EXPORT_TYPES[mime])
                text = resp.text
            elif mime.startswith("text/") or mime in ("application/json",):
                resp = await self._get(http, f"/files/{link.file_id}", alt="media")
                text = resp.text
            truncated = bool(text and len(text) > max_chars)
            return {
                "name": meta.get("name"),
                "mime_type": mime,
                "modified": meta.get("modifiedTime"),
                "modified_by": (meta.get("lastModifyingUser") or {}).get("displayName"),
                "text": text[:max_chars] if text else None,
                "truncated": truncated,
            }


# --------------------------------------------------------------------------- #
# `nexus-mcp google login` (installed-app flow, loopback redirect + PKCE)
# --------------------------------------------------------------------------- #


def login(config_dir: Path, client_id: str, client_secret: str, *, open_browser: bool = True, timeout: float = 300) -> str:
    """Interactive sign-in; stores the refresh token. Returns the Google account email if known."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            q = parse_qs(urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                result.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in result
            self.wfile.write(
                ("<h2>Google connected to Nexus MCP. You can close this tab.</h2>" if ok
                 else "<h2>Google sign-in didn't finish. Go back to the terminal.</h2>").encode()
            )

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}"
    url = AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": f"{SCOPE} email",
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    print(f"Open this URL to connect Google (read-only Drive access):\n  {url}\n", flush=True)
    if open_browser:
        webbrowser.open(url)
    server.timeout = timeout
    deadline = time.time() + timeout
    while "code" not in result and "error" not in result and time.time() < deadline:
        server.handle_request()
    server.server_close()
    if "code" not in result:
        raise NexusAuthError(f"Google sign-in failed: {result.get('error', 'timed out')}")
    resp = httpx.post(TOKEN_URL, data={
        "client_id": client_id, "client_secret": client_secret, "code": result["code"],
        "code_verifier": verifier, "grant_type": "authorization_code", "redirect_uri": redirect,
    }, timeout=30)
    if resp.status_code != 200:
        raise NexusAuthError(f"Google token exchange failed ({resp.status_code}): {resp.text[:200]}")
    body = resp.json()
    if not body.get("refresh_token"):
        raise NexusAuthError("Google didn't return a refresh token; remove the app's access in your Google account and retry.")
    email = None
    if body.get("id_token"):
        try:
            payload = body["id_token"].split(".")[1]
            email = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("email")
        except (IndexError, ValueError):
            email = None
    GoogleCredentials(config_dir).save({
        "client_id": client_id, "client_secret": client_secret, "refresh_token": body["refresh_token"],
        "email": email, "obtained_at": time.time(),
    })
    return email or "your Google account"
