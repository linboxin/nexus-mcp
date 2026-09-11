"""Authentication + credential storage for Nexus.

Union authenticates Nexus users through Okta (SAML 2.0, ``auth_saml2``).
Moodle's sanctioned way to hand an SSO-authenticated user a web-service token
is the Moodle-app launch flow (``admin/tool/mobile/launch.php``):

1. We open the student's own browser at
   ``/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=<random>&urlscheme=nexusmcp&confirmed=1``
2. Moodle sends the browser through Okta. The student signs in exactly as they
   would on the website (password, MFA...). We never see any of it.
3. Moodle issues (or reuses) the student's ``moodle_mobile_app`` token and
   renders a page whose "launch the app" link is
   ``nexusmcp://token=<base64(md5(wwwroot+passport):::token[:::privatetoken])>``.
4. That link reaches us either through a tiny URL-scheme handler app we
   register on macOS, or by the student pasting the link into the terminal.
5. We verify ``md5(wwwroot + passport)`` matches the login we started, call
   ``core_webservice_get_site_info`` to confirm the token works, and store it in
   the system keyring.

Nothing here bypasses Union SSO: the token is the same one the official
Moodle / Open LMS mobile app would receive, and it is bound to the student's
own permissions. The student can revoke it at any time on
``/user/managetoken.php`` (Preferences → Security keys).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import plistlib
import re
import secrets
import select
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Sequence
from urllib.parse import unquote, urlencode, urlparse

from ..config import DEFAULT_HANDLER_SCHEMES, DEFAULT_SERVICE, DEFAULT_URL_SCHEME, Settings, normalize_site_url
from ..errors import NexusAuthError

KEYRING_SERVICE = "nexus-mcp"
CALLBACK_FILENAME = "sso-callback.txt"
HANDLER_APP_NAME = "Nexus MCP Login.app"
HANDLER_BUNDLE_ID = "edu.union.nexus-mcp.login-handler"
_TOKEN_RE = re.compile(r"^[a-f0-9]{32}$")
_TOKEN_IN_TEXT_RE = re.compile(r"token=([A-Za-z0-9+/=%_-]+)")


# --------------------------------------------------------------------------- #
# Launch URL + token payload
# --------------------------------------------------------------------------- #


def new_passport() -> str:
    """Random per-login nonce; Moodle echoes ``md5(wwwroot + passport)`` back."""
    return secrets.token_hex(16)


def build_launch_url(
    site_url: str,
    passport: str,
    *,
    service: str = DEFAULT_SERVICE,
    url_scheme: str = DEFAULT_URL_SCHEME,
    confirmed: bool = True,
) -> str:
    """URL of Moodle's mobile-app SSO launcher.

    ``confirmed=1`` makes Moodle render an HTML page containing the token link
    (instead of a bare 303 to the custom scheme), so the link is visible and
    copyable even when no URL-scheme handler is installed.
    """
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9\-+.]*", url_scheme):
        raise ValueError("url_scheme must start with a letter and contain only letters, digits, '.', '+', '-'")
    query = {"service": service, "passport": passport, "urlscheme": url_scheme}
    if confirmed:
        query["confirmed"] = "1"
    return f"{normalize_site_url(site_url)}/admin/tool/mobile/launch.php?{urlencode(query)}"


@dataclass(frozen=True)
class TokenBundle:
    token: str
    private_token: str | None
    site_id: str
    scheme: str | None


def parse_token_url(text: str, *, site_url: str | None = None, passport: str | None = None) -> TokenBundle:
    """Decode ``<scheme>://token=<base64>`` (or the bare base64 blob).

    When ``site_url`` and ``passport`` are given, the embedded site id is checked
    against ``md5(wwwroot + passport)`` so a token from a different login
    attempt (or a different site) is rejected.
    """
    raw = (text or "").strip().strip("'\"<> ")
    if not raw:
        raise NexusAuthError("No token link was provided.")
    if "token=" not in raw and re.search(r"token%3[Dd]", raw):
        raw = unquote(raw)
    scheme: str | None = None
    match = _TOKEN_IN_TEXT_RE.search(raw)
    if match:
        blob = unquote(match.group(1))
        if "://" in raw:
            scheme = raw.split("://", 1)[0].strip().lower() or None
    else:
        blob = unquote(raw)
    blob = blob.strip()
    try:
        decoded = base64.b64decode(blob + "=" * (-len(blob) % 4), validate=False).decode("utf-8")
    except Exception as exc:
        raise NexusAuthError(
            "Could not decode the token link. Paste the complete link (it looks like "
            "nexusmcp://token=...)."
        ) from exc
    parts = decoded.split(":::")
    if len(parts) < 2:
        raise NexusAuthError("The token link is incomplete (expected siteid:::token). Copy the whole link.")
    site_id, token = parts[0].strip(), parts[1].strip()
    private_token = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
    if not _TOKEN_RE.match(token):
        raise NexusAuthError("The token inside the link has an unexpected format.")
    if site_url is not None and passport is not None:
        expected = hashlib.md5((normalize_site_url(site_url) + passport).encode("utf-8")).hexdigest()
        if not hmac.compare_digest(expected, site_id):
            raise NexusAuthError(
                "This token link does not belong to the login attempt we started (site/passport mismatch). "
                "Run `nexus-mcp login` again and use the link it prints."
            )
    return TokenBundle(token=token, private_token=private_token, site_id=site_id, scheme=scheme)


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #


@dataclass
class StoredToken:
    token: str
    site_url: str
    user_id: int | None = None
    fullname: str | None = None
    username: str | None = None
    obtained_at: float = 0.0
    source: str = "sso"

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "StoredToken":
        data = json.loads(raw)
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @property
    def masked(self) -> str:
        return f"{self.token[:4]}…{self.token[-4:]}" if len(self.token) >= 8 else "…"


class TokenStore:
    """Persist the Nexus token in the OS keyring, with a 0600 file fallback.

    ``mode``: ``auto`` (keyring, then file), ``keyring`` (keyring only) or
    ``file`` (file only). The file lives in the app config directory
    (macOS: ``~/Library/Application Support/nexus-mcp/credentials.json``).
    """

    def __init__(self, site_url: str, *, mode: str = "auto", config_dir: Path | str | None = None) -> None:
        self.site_url = normalize_site_url(site_url)
        self.mode = mode if mode in ("auto", "keyring", "file") else "auto"
        self.config_dir = Path(config_dir) if config_dir else Path(Settings().config_dir)
        self.account = urlparse(self.site_url).netloc

    @property
    def file_path(self) -> Path:
        return self.config_dir / "credentials.json"

    # -- keyring -----------------------------------------------------------
    @staticmethod
    def _keyring():
        try:
            import keyring  # noqa: WPS433 (optional at runtime)

            return keyring
        except Exception:  # pragma: no cover - import failure
            return None

    def _keyring_get(self) -> StoredToken | None:
        kr = self._keyring()
        if kr is None:
            return None
        raw = kr.get_password(KEYRING_SERVICE, self.account)
        return StoredToken.from_json(raw) if raw else None

    def _keyring_set(self, stored: StoredToken) -> None:
        kr = self._keyring()
        if kr is None:
            raise RuntimeError("keyring module unavailable")
        kr.set_password(KEYRING_SERVICE, self.account, stored.to_json())

    def _keyring_delete(self) -> None:
        kr = self._keyring()
        if kr is None:
            return
        try:
            kr.delete_password(KEYRING_SERVICE, self.account)
        except Exception:
            pass

    # -- file --------------------------------------------------------------
    def _file_load_all(self) -> dict[str, dict]:
        if not self.file_path.exists():
            return {}
        try:
            return json.loads(self.file_path.read_text("utf-8") or "{}")
        except (OSError, ValueError):
            return {}

    def _file_get(self) -> StoredToken | None:
        entry = self._file_load_all().get(self.account)
        return StoredToken(**{k: v for k, v in entry.items() if k in StoredToken.__dataclass_fields__}) if entry else None

    def _file_set(self, stored: StoredToken) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.config_dir, 0o700)
        except OSError:
            pass
        data = self._file_load_all()
        data[self.account] = asdict(stored)
        tmp = self.file_path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8", opener=lambda p, f: os.open(p, f, 0o600)) as fh:
            json.dump(data, fh)
        os.replace(tmp, self.file_path)
        os.chmod(self.file_path, 0o600)

    def _file_delete(self) -> None:
        data = self._file_load_all()
        if self.account in data:
            data.pop(self.account)
            if data:
                self.file_path.write_text(json.dumps(data), "utf-8")
            else:
                self.file_path.unlink(missing_ok=True)

    # -- public ------------------------------------------------------------
    def load(self) -> StoredToken | None:
        if self.mode in ("auto", "keyring"):
            try:
                stored = self._keyring_get()
                if stored:
                    return stored
            except Exception as exc:
                if self.mode == "keyring":
                    raise NexusAuthError(f"Could not read the Nexus token from the system keyring: {exc}") from exc
        if self.mode in ("auto", "file"):
            return self._file_get()
        return None

    def save(self, stored: StoredToken) -> str:
        """Store the token; returns the backend used ("keyring" or "file")."""
        if self.mode in ("auto", "keyring"):
            try:
                self._keyring_set(stored)
                self._file_delete()  # never keep a stale copy in the fallback file
                return "keyring"
            except Exception as exc:
                if self.mode == "keyring":
                    raise NexusAuthError(f"Could not store the Nexus token in the system keyring: {exc}") from exc
        self._file_set(stored)
        return "file"

    def clear(self) -> None:
        if self.mode in ("auto", "keyring"):
            self._keyring_delete()
        if self.mode in ("auto", "file"):
            self._file_delete()


def resolve_token(settings: Settings) -> tuple[str, str]:
    """Return ``(token, source)``; source is ``env``, ``keyring`` or ``file``."""
    if settings.token:
        return settings.token, "env"
    store = TokenStore(settings.base_url, mode=settings.token_storage, config_dir=settings.config_dir)
    stored = store.load()
    if stored is None:
        raise NexusAuthError(
            "No Nexus token is stored for this account. Run `nexus-mcp login` in a terminal to sign in "
            "through Union's Okta SSO (or set NEXUS_TOKEN)."
        )
    source = "keyring"
    if store.mode == "file" or (store.mode == "auto" and store.file_path.exists() and stored == store._file_get()):
        source = "file"
    return stored.token, source


# --------------------------------------------------------------------------- #
# Receiving the token link
# --------------------------------------------------------------------------- #

_APPLESCRIPT = """on open location theURL
\tdo shell script "/usr/bin/printf '%s' " & quoted form of theURL & " > " & quoted form of "__CALLBACK__"
end open location
"""


def ensure_macos_url_handler(
    url_schemes: str | Sequence[str], config_dir: Path, callback_path: Path
) -> Path | None:
    """Create + register a tiny AppleScript app that handles the given URL schemes.

    Besides our own ``nexusmcp://`` the app claims ``ltgopenlmsapp://``: Nexus
    overrides the scheme in the launch link with the Open LMS app's scheme
    (Moodle's ``tool_mobile | forcedurlscheme`` setting), so that is what the
    browser is actually asked to open.

    The app does one thing: write the URL it receives to ``callback_path``.
    Returns the app path, or ``None`` when this is not macOS or creation failed
    (the copy/paste fallback still works).
    """
    schemes = [url_schemes] if isinstance(url_schemes, str) else [x for x in url_schemes if x]
    if sys.platform != "darwin" or not shutil.which("osacompile") or not schemes:
        return None
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(config_dir, 0o700)
        except OSError:
            pass
        app_path = config_dir / HANDLER_APP_NAME
        stamp_path = config_dir / ".login-handler-version"
        stamp = f"v2|{','.join(schemes)}|{callback_path}"
        if app_path.exists() and stamp_path.exists() and stamp_path.read_text("utf-8") == stamp:
            return app_path
        script_path = config_dir / "login-handler.applescript"
        script_path.write_text(_APPLESCRIPT.replace("__CALLBACK__", str(callback_path)), "utf-8")
        if app_path.exists():
            shutil.rmtree(app_path)
        subprocess.run(["osacompile", "-o", str(app_path), str(script_path)], check=True, capture_output=True)
        plist_path = app_path / "Contents" / "Info.plist"
        with open(plist_path, "rb") as fh:
            info = plistlib.load(fh)
        info["CFBundleIdentifier"] = HANDLER_BUNDLE_ID
        info["CFBundleName"] = "Nexus MCP Login"
        info["CFBundleDisplayName"] = "Nexus MCP Login"
        info["CFBundleURLTypes"] = [
            {"CFBundleURLName": "Nexus MCP SSO callback", "CFBundleURLSchemes": list(schemes)}
        ]
        info["LSUIElement"] = True
        with open(plist_path, "wb") as fh:
            plistlib.dump(info, fh)
        codesign = shutil.which("codesign")
        if codesign:
            subprocess.run([codesign, "--force", "--deep", "--sign", "-", str(app_path)], capture_output=True)
        lsregister = (
            "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/"
            "Support/lsregister"
        )
        if os.path.exists(lsregister):
            subprocess.run([lsregister, "-f", str(app_path)], capture_output=True)
        stamp_path.write_text(stamp, "utf-8")
        return app_path
    except Exception:
        return None


def wait_for_callback(
    callback_path: Path,
    *,
    timeout: float = 600.0,
    allow_stdin: bool = True,
    poll: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Block until the handler app writes the link or the user pastes it."""
    deadline = time.monotonic() + timeout
    stdin_ok = allow_stdin and sys.stdin is not None and sys.stdin.isatty()
    while time.monotonic() < deadline:
        if callback_path.exists():
            try:
                text = callback_path.read_text("utf-8").strip()
            finally:
                callback_path.unlink(missing_ok=True)
            if text:
                return text
        if stdin_ok:
            ready, _, _ = select.select([sys.stdin], [], [], poll)
            if ready:
                line = sys.stdin.readline().strip()
                if line:
                    return line
        else:
            sleep(poll)
    raise NexusAuthError("Timed out waiting for the Nexus login to finish. Run `nexus-mcp login` again.")


@dataclass
class LoginSession:
    """One SSO login attempt (a fresh passport each time)."""

    site_url: str
    service: str = DEFAULT_SERVICE
    url_scheme: str = DEFAULT_URL_SCHEME
    config_dir: Path = field(default_factory=lambda: Path(Settings().config_dir))
    passport: str = field(default_factory=new_passport)
    handler_schemes: tuple[str, ...] = DEFAULT_HANDLER_SCHEMES
    handler_app: Path | None = None

    def __post_init__(self) -> None:
        self.site_url = normalize_site_url(self.site_url)
        self.config_dir = Path(self.config_dir)

    @property
    def callback_path(self) -> Path:
        return self.config_dir / CALLBACK_FILENAME

    @property
    def launch_url(self) -> str:
        return build_launch_url(self.site_url, self.passport, service=self.service, url_scheme=self.url_scheme)

    def prepare(self, *, register_handler: bool = True) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.callback_path.unlink(missing_ok=True)
        if register_handler:
            schemes = [self.url_scheme] + [x for x in self.handler_schemes if x != self.url_scheme]
            self.handler_app = ensure_macos_url_handler(schemes, self.config_dir, self.callback_path)

    def open_browser(self) -> bool:
        try:
            return bool(webbrowser.open(self.launch_url, new=2))
        except Exception:
            return False

    def wait(self, *, timeout: float = 600.0, allow_stdin: bool = True) -> TokenBundle:
        text = wait_for_callback(self.callback_path, timeout=timeout, allow_stdin=allow_stdin)
        return parse_token_url(text, site_url=self.site_url, passport=self.passport)
