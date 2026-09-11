"""Helpers shared by the service modules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..models.common import FileRef

if TYPE_CHECKING:
    from .nexus import Nexus


def file_ref(nx: "Nexus", raw: dict[str, Any]) -> FileRef:
    return FileRef(
        filename=str(raw.get("filename") or raw.get("name") or "file"),
        url=raw.get("fileurl") or raw.get("url"),
        mimetype=raw.get("mimetype"),
        size=int(raw["filesize"]) if raw.get("filesize") not in (None, "") else None,
        modified=nx.when(raw.get("timemodified")),
    )


def as_float(value: Any) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
