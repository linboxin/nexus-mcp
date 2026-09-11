from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .common import FileRef, When


class Course(BaseModel):
    id: int
    name: str
    short_name: str
    teacher: str | None = None
    teachers: list[str] = Field(default_factory=list)
    url: str
    category: str | None = None
    starts: When | None = None
    ends: When | None = None
    classification: str = "inprogress"  # inprogress | past | future | hidden (from Moodle start/end dates)
    term: str | None = None  # parsed from Union short names like 26/FA.CSC-240-01 -> "Fall 2026"
    academic: bool = False  # True when the short name carries a term code
    progress: float | None = None
    hidden: bool = False
    summary: str | None = None


class CourseModule(BaseModel):
    id: int  # course-module id (cmid)
    instance: int | None = None
    name: str
    type: str  # Moodle modname: assign, quiz, resource, page, ...
    url: str | None = None
    description: str | None = None
    section: str | None = None
    visible: bool = True
    files: list[FileRef] = Field(default_factory=list)
    dates: list[dict[str, Any]] = Field(default_factory=list)
    completion: str | None = None
    availability: str | None = None


class CourseSection(BaseModel):
    id: int
    name: str
    summary: str | None = None
    modules: list[CourseModule] = Field(default_factory=list)


class CourseDetail(Course):
    description: str | None = None
    instructors: list[str] = Field(default_factory=list)
    sections: list[CourseSection] = Field(default_factory=list)
    materials: list[CourseModule] = Field(default_factory=list)
    activities: list[CourseModule] = Field(default_factory=list)
