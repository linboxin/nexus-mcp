"""Process-wide Nexus session used by the MCP tools (lazy, replaceable in tests)."""

from __future__ import annotations

import asyncio

from .auth import resolve_token
from .config import Settings
from .moodle.nexus import Nexus

_nexus: Nexus | None = None
_lock: asyncio.Lock | None = None


def set_nexus(nexus: Nexus | None) -> None:
    global _nexus
    _nexus = nexus


async def get_nexus() -> Nexus:
    global _nexus, _lock
    if _nexus is not None and _nexus.ready:
        return _nexus
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if _nexus is None:
            settings = Settings.from_env()
            token, _source = resolve_token(settings)
            _nexus = Nexus.from_settings(settings, token)
        await _nexus.ensure_ready()
        return _nexus


async def close() -> None:
    global _nexus
    if _nexus is not None:
        await _nexus.aclose()
        _nexus = None
