from __future__ import annotations

from pydantic import BaseModel

from .common import When


class EventAction(BaseModel):
    name: str | None = None
    url: str | None = None
    actionable: bool | None = None
    item_count: int | None = None


class CalendarEvent(BaseModel):
    id: int
    name: str
    course_id: int | None = None
    course: str | None = None
    start: When | None = None
    end: When | None = None
    description: str | None = None
    url: str | None = None
    type: str = "unknown"  # Moodle eventtype: due, close, open, expectcompletionon, course, user, site, ...
    module: str | None = None  # assign, quiz, forum, ...
    instance: int | None = None
    cmid: int | None = None
    action: EventAction | None = None
    overdue: bool | None = None  # Moodle's own flag on action events
    source: str = "calendar"
