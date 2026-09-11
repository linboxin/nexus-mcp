from __future__ import annotations

from pydantic import BaseModel


class When(BaseModel):
    """A timestamp rendered in the student's timezone."""

    iso: str
    display: str
    short: str
    relative: str
    unix: int


class FileRef(BaseModel):
    filename: str
    url: str | None = None
    mimetype: str | None = None
    size: int | None = None
    modified: When | None = None


class Freshness(BaseModel):
    """Whether a value came from cache, and when it was fetched."""

    cached: bool
    as_of: str
    ttl_seconds: int
    note: str | None = None
