"""Bearer-token protection for the HTTP transport.

Remote agents (Grok Bot, hosted assistants) can only reach a public HTTPS URL,
and Grok Bot's connector form offers exactly one auth mechanism: a static
``Authorization: Bearer …`` header. So the HTTP door is gated by one long
random secret that lives on this machine; the Nexus token itself never leaves.
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
from pathlib import Path

from mcp.server.auth.provider import AccessToken, TokenVerifier

TOKEN_FILENAME = "http-token.txt"
_TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


class StaticTokenVerifier(TokenVerifier):
    """Accepts exactly one shared secret (constant-time compare)."""

    def __init__(self, secret: str) -> None:
        if not secret or len(secret) < 16:
            raise ValueError("the HTTP auth token must be at least 16 characters")
        self._secret = secret

    async def verify_token(self, token: str) -> AccessToken | None:
        if token and hmac.compare_digest(token, self._secret):
            return AccessToken(token=token, client_id="nexus-remote", scopes=[])
        return None


def new_http_token() -> str:
    return "nxs_" + secrets.token_urlsafe(32)


def load_or_create_http_token(config_dir: Path, *, rotate: bool = False) -> str:
    """Stable per-machine secret for the HTTP door (0600 file in the config dir)."""
    path = config_dir / TOKEN_FILENAME
    if path.exists() and not rotate:
        existing = path.read_text("utf-8").strip()
        if existing:
            return existing
    token = new_http_token()
    config_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", "utf-8")
    os.chmod(path, 0o600)
    return token


def find_tunnel_url(text: str) -> str | None:
    match = _TUNNEL_URL.search(text)
    return match.group(0) if match else None
