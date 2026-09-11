"""Runtime configuration (environment / .env driven)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse

from platformdirs import user_config_dir

APP_NAME = "nexus-mcp"
DEFAULT_URL = "https://nexus.union.edu"
DEFAULT_SERVICE = "moodle_mobile_app"
DEFAULT_TIMEZONE = "America/New_York"  # Nexus' site timezone (observed in M.cfg on the login page)
DEFAULT_URL_SCHEME = "nexusmcp"
# Nexus forces the mobile launch link to the Open LMS app scheme (tool_mobile | forcedurlscheme),
# observed 2026-09-11: the page link is ltgopenlmsapp://token=... regardless of the urlscheme we ask for.
DEFAULT_HANDLER_SCHEMES = ("nexusmcp", "ltgopenlmsapp")


def normalize_site_url(url: str) -> str:
    """Return the URL exactly as Moodle's ``$CFG->wwwroot`` would be written.

    The SSO token payload embeds ``md5(wwwroot . passport)``, so the string has
    to match byte for byte: scheme + host (+ optional path), no trailing slash.
    """
    url = (url or "").strip()
    if not url:
        return DEFAULT_URL
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


@dataclass(frozen=True)
class CacheTTLs:
    """Cache lifetimes in seconds. 0 disables caching for that category."""

    courses: int = 900
    course_info: int = 900
    assignments: int = 120
    grades: int = 30
    submission: int = 30
    calendar: int = 120
    announcements: int = 120
    materials: int = 600


@dataclass(frozen=True)
class Settings:
    base_url: str = DEFAULT_URL
    service: str = DEFAULT_SERVICE
    token: str | None = None  # explicit override (NEXUS_TOKEN); otherwise the stored SSO token is used
    timezone: str | None = None  # NEXUS_TIMEZONE override; otherwise the Moodle account timezone
    default_timezone: str = DEFAULT_TIMEZONE
    token_storage: str = "auto"  # auto | keyring | file
    url_scheme: str = DEFAULT_URL_SCHEME
    handler_schemes: tuple[str, ...] = DEFAULT_HANDLER_SCHEMES
    http_timeout: float = 30.0
    max_concurrency: int = 6
    config_dir: Path = field(default_factory=lambda: Path(user_config_dir(APP_NAME)))
    cache: CacheTTLs = field(default_factory=CacheTTLs)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, dotenv: bool = True) -> "Settings":
        if env is None:
            if dotenv:
                _load_dotenv()
            env = os.environ

        def get(name: str, default: str | None = None) -> str | None:
            value = env.get(name)
            return value if value not in (None, "") else default

        def get_int(name: str, default: int) -> int:
            raw = get(name)
            if raw is None:
                return default
            try:
                return int(raw)
            except ValueError:
                return default

        def get_float(name: str, default: float) -> float:
            raw = get(name)
            if raw is None:
                return default
            try:
                return float(raw)
            except ValueError:
                return default

        cache = CacheTTLs(
            courses=get_int("NEXUS_CACHE_COURSES_TTL", 900),
            course_info=get_int("NEXUS_CACHE_COURSE_INFO_TTL", 900),
            assignments=get_int("NEXUS_CACHE_ASSIGNMENTS_TTL", 120),
            grades=get_int("NEXUS_CACHE_GRADES_TTL", 30),
            submission=get_int("NEXUS_CACHE_SUBMISSION_TTL", 30),
            calendar=get_int("NEXUS_CACHE_CALENDAR_TTL", 120),
            announcements=get_int("NEXUS_CACHE_ANNOUNCEMENTS_TTL", 120),
            materials=get_int("NEXUS_CACHE_MATERIALS_TTL", 600),
        )
        config_dir = get("NEXUS_CONFIG_DIR")
        return cls(
            base_url=normalize_site_url(get("NEXUS_URL", DEFAULT_URL) or DEFAULT_URL),
            service=get("NEXUS_SERVICE", DEFAULT_SERVICE) or DEFAULT_SERVICE,
            token=get("NEXUS_TOKEN"),
            timezone=get("NEXUS_TIMEZONE"),
            default_timezone=get("NEXUS_DEFAULT_TIMEZONE", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE,
            token_storage=(get("NEXUS_TOKEN_STORAGE", "auto") or "auto").lower(),
            url_scheme=get("NEXUS_URL_SCHEME", DEFAULT_URL_SCHEME) or DEFAULT_URL_SCHEME,
            handler_schemes=tuple(
                x.strip() for x in (get("NEXUS_HANDLER_SCHEMES") or ",".join(DEFAULT_HANDLER_SCHEMES)).split(",") if x.strip()
            ),
            http_timeout=get_float("NEXUS_HTTP_TIMEOUT", 30.0),
            max_concurrency=max(1, get_int("NEXUS_MAX_CONCURRENCY", 6)),
            config_dir=Path(config_dir).expanduser() if config_dir else Path(user_config_dir(APP_NAME)),
            cache=cache,
        )


def _load_dotenv() -> None:
    """Load ``.env`` from NEXUS_ENV_FILE, the working directory, or the config dir."""
    try:
        from dotenv import find_dotenv, load_dotenv
    except ImportError:  # pragma: no cover
        return
    explicit = os.environ.get("NEXUS_ENV_FILE")
    candidates = [explicit] if explicit else []
    found = find_dotenv(usecwd=True)
    if found:
        candidates.append(found)
    candidates.append(str(Path(user_config_dir(APP_NAME)) / ".env"))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            load_dotenv(candidate, override=False)
