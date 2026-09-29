"""Notice edits to text that Moodle never reports as an update.

``core_course_get_updates_since`` only knows about activities. The text at the
top of a course page (section summaries, where instructors often keep a
running "News" list) and text boxes (``label`` modules) can change without any
event, and Google Docs linked from the course change outside Nexus entirely.

We keep a fingerprint of every such text in ``watch-state.json`` (config dir)
and compare on each look. Moodle has no edit time for these, so a change is
dated by when we first *noticed* it; the hosted server looks often enough for
that to be close.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

STATE_FILENAME = "watch-state.json"
MAX_LINES = 12


def _fingerprint(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


class TextWatch:
    """Remembers text blocks per key and reports what changed since last time."""

    def __init__(self, state_dir: Path) -> None:
        self._path = Path(state_dir) / STATE_FILENAME
        self._lock = threading.Lock()
        try:
            self._state: dict[str, Any] = json.loads(self._path.read_text("utf-8"))
        except (FileNotFoundError, ValueError):
            self._state = {}
        self._state.setdefault("blocks", {})

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(self._state, fh)
        os.replace(tmp, self._path)

    def observe(self, key: str, text: str, *, now: float | None = None) -> dict[str, Any] | None:
        """Record ``text`` under ``key``. Returns a change record when it differs from last time.

        The first sighting is a baseline, not a change.
        """
        now = time.time() if now is None else now
        fp = _fingerprint(text)
        with self._lock:
            blocks = self._state["blocks"]
            prev = blocks.get(key)
            if prev is not None and prev["fp"] == fp:
                return None
            record = {"fp": fp, "text": text, "seen": now, "changed": prev["changed"] if prev else None}
            change = None
            if prev is not None:
                old, new = _lines(prev["text"]), _lines(text)
                old_set, new_set = set(old), set(new)
                change = {
                    "added": [line for line in new if line not in old_set][:MAX_LINES],
                    "removed": [line for line in old if line not in new_set][:MAX_LINES],
                    "noticed": now,
                }
                record["changed"] = now
                record["last_change"] = change
            blocks[key] = record
            self._save()
            return change

    def last_seen(self, key: str) -> float | None:
        block = self._state["blocks"].get(key)
        return block.get("seen") if block else None

    def last_change(self, key: str) -> dict[str, Any] | None:
        block = self._state["blocks"].get(key)
        return block.get("last_change") if block else None

    def changes_since(self, prefix: str, since_ts: float) -> list[tuple[str, dict[str, Any]]]:
        """Every block under ``prefix`` whose last change was noticed at or after ``since_ts``."""
        out = []
        for key, block in self._state["blocks"].items():
            change = block.get("last_change")
            if key.startswith(prefix) and change and change["noticed"] >= since_ts:
                out.append((key, change))
        return out
