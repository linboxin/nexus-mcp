from __future__ import annotations

from pydantic import BaseModel, Field

from .common import FileRef, When


class Announcement(BaseModel):
    id: int  # first post id
    discussion_id: int
    course_id: int
    course: str
    forum_id: int
    forum: str
    subject: str
    author: str | None = None
    posted: When | None = None
    message: str
    url: str
    attachments: list[FileRef] = Field(default_factory=list)
    pinned: bool = False


class CourseUpdate(BaseModel):
    course_id: int
    course: str
    cmid: int
    module: str | None = None
    module_type: str | None = None
    changes: list[str] = Field(default_factory=list)
    url: str | None = None
    updated: When | None = None


class Notification(BaseModel):
    id: int
    subject: str
    message: str
    url: str | None = None
    created: When | None = None
    read: bool = False
    component: str | None = None
    event_type: str | None = None
