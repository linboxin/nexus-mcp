"""The ``Nexus`` facade: one authenticated session + all service objects."""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

from ..cache import CacheMeta, TTLCache
from ..config import Settings
from ..errors import NexusUnsupportedError
from ..models.common import Freshness, When
from ..timeutil import display, relative, resolve_timezone, short, to_local
from .assignments import AssignmentService
from .calendar import CalendarService
from .client import MoodleClient
from .courses import CourseService
from .grades import GradeService
from .materials import MaterialService
from .notifications import NotificationService
from .site import SiteContext, load_site_context


class Nexus:
    def __init__(
        self,
        client: MoodleClient,
        settings: Settings,
        *,
        cache: TTLCache | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.client = client
        self.settings = settings
        self._clock = clock
        self.cache = cache or TTLCache(clock=clock)
        self.ttl = settings.cache
        self._site: SiteContext | None = None
        self.courses = CourseService(self)
        self.assignments = AssignmentService(self)
        self.grades = GradeService(self)
        self.materials = MaterialService(self)
        self.calendar = CalendarService(self)
        self.notifications = NotificationService(self)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        token: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> "Nexus":
        client = MoodleClient(
            settings.base_url,
            token,
            timeout=settings.http_timeout,
            max_concurrency=settings.max_concurrency,
            transport=transport,
        )
        return cls(client, settings, clock=clock)

    # -- session -----------------------------------------------------------
    async def ensure_ready(self) -> SiteContext:
        if self._site is None:
            self._site = await load_site_context(self.client, self.settings)
        return self._site

    @property
    def ready(self) -> bool:
        return self._site is not None

    @property
    def site(self) -> SiteContext:
        if self._site is None:
            raise RuntimeError("Nexus.ensure_ready() must be awaited before using services")
        return self._site

    @property
    def site_url(self) -> str:
        return self.settings.base_url

    @property
    def tz(self) -> ZoneInfo:
        if self._site is not None:
            return self._site.tz
        tz, _, _ = resolve_timezone([(self.settings.timezone, "override"), (self.settings.default_timezone, "default")])
        return tz

    def has(self, function: str) -> bool:
        return self._site is not None and self._site.has(function)

    def require(self, function: str, *, feature: str | None = None) -> None:
        if not self.has(function):
            what = f" needed for {feature}" if feature else ""
            raise NexusUnsupportedError(
                f"Nexus does not expose the Moodle function '{function}'{what} to this account's web service.",
                details={"function": function},
            )

    # -- time --------------------------------------------------------------
    def now(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), self.tz)

    def now_ts(self) -> int:
        return int(self._clock())

    def when(self, ts: int | float | str | None) -> When | None:
        try:
            value = int(float(ts)) if ts not in (None, "") else 0
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        dt = to_local(value, self.tz)
        assert dt is not None
        return When(
            iso=dt.isoformat(timespec="minutes"),
            display=display(dt),
            short=short(dt),
            relative=relative(dt, self.now()),
            unix=value,
        )

    def freshness(self, meta: CacheMeta, *, note: str | None = None) -> Freshness:
        as_of = datetime.fromtimestamp(meta.fetched_at, self.tz).isoformat(timespec="seconds")
        if meta.cached and note is None:
            age = int(self._clock() - meta.fetched_at)
            note = f"Cached {age}s ago; not re-checked against Nexus."
        return Freshness(cached=meta.cached, as_of=as_of, ttl_seconds=meta.ttl, note=note)

    async def aclose(self) -> None:
        await self.client.aclose()

    def describe(self) -> dict[str, Any]:
        data: dict[str, Any] = {"site_url": self.site_url, "ready": self.ready}
        if self._site is not None:
            data.update(self._site.as_dict())
        return data
