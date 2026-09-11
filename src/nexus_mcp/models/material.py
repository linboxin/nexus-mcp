from __future__ import annotations

from pydantic import BaseModel

from .common import When


class Material(BaseModel):
    id: str  # "<cmid>" or "<cmid>/<n>" for the n-th file inside a folder/module
    cmid: int
    course_id: int
    course: str
    title: str
    type: str  # pdf | slides | document | page | book | url | folder | label | video | text | file | ...
    module_type: str  # Moodle modname
    filename: str | None = None
    description: str | None = None
    url: str | None = None
    file_url: str | None = None
    mimetype: str | None = None
    size: int | None = None
    section: str | None = None
    modified: When | None = None
    score: float | None = None


class MaterialContent(Material):
    content: str | None = None
    content_type: str | None = None
    truncated: bool = False
    note: str | None = None
