"""Announcements, notifications, and "what changed since" (forum / popup / updates functions)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..errors import NexusError
from ..htmltext import html_to_text
from ..models.announcement import Announcement, CourseUpdate, Notification
from ..timeutil import unix
from .common import file_ref

if TYPE_CHECKING:
    from .nexus import Nexus

SORT_CREATED_DESC = 3  # mod_forum discussion_list vault: SORTORDER_CREATED_DESC

UPDATE_LABELS = {
    "configuration": "settings or description changed",
    "fileareas": "files added or changed",
    "introfiles": "files added or changed",
    "contentfiles": "files added or changed",
    "attachments": "files added or changed",
    "completion": "completion status changed",
    "gradeitems": "grade updated",
    "outcomes": "outcomes updated",
    "comments": "new comments",
    "ratings": "ratings changed",
    "discussions": "new discussion",
    "posts": "new posts",
    "submissions": "submission changed",
    "usersubmissions": "submission changed",
    "userfeedback": "new feedback",
    "grades": "grade updated",
    "attempts": "attempt recorded",
    "answers": "new answers",
    "entries": "new entries",
    "pages": "content changed",
    "chapters": "chapters changed",
    "usernotes": "notes changed",
    "tracking": "tracking changed",
}


class NotificationService:
    def __init__(self, nx: "Nexus") -> None:
        self.nx = nx

    # -- forums ------------------------------------------------------------
    async def raw_forums(self) -> list[dict[str, Any]]:
        nx = self.nx
        nx.require("mod_forum_get_forums_by_courses", feature="announcements")

        async def fetch() -> list[dict[str, Any]]:
            return list(await nx.client.call("mod_forum_get_forums_by_courses") or [])

        value, _ = await nx.cache.get_or_fetch(("forums",), nx.ttl.course_info, fetch)
        return value

    async def raw_discussions(self, forum_id: int, per_forum: int = 10) -> list[dict[str, Any]]:
        nx = self.nx
        nx.require("mod_forum_get_forum_discussions", feature="announcements")

        async def fetch() -> list[dict[str, Any]]:
            payload = await nx.client.call(
                "mod_forum_get_forum_discussions", forumid=int(forum_id), sortorder=SORT_CREATED_DESC, page=0, perpage=int(per_forum)
            )
            return list((payload or {}).get("discussions", []))

        value, _ = await nx.cache.get_or_fetch(("discussions", int(forum_id), int(per_forum)), nx.ttl.announcements, fetch)
        return value

    def _announcement(self, d: dict[str, Any], forum: dict[str, Any], course_name: str) -> Announcement:
        nx = self.nx
        return Announcement(
            id=int(d.get("id") or 0),
            discussion_id=int(d.get("discussion") or 0),
            course_id=int(forum.get("course") or 0),
            course=course_name,
            forum_id=int(forum.get("id") or 0),
            forum=forum.get("name") or "",
            subject=html_to_text(d.get("name") or d.get("subject")) or "",
            author=d.get("userfullname") or None,
            posted=nx.when(d.get("created") or d.get("timemodified")),
            message=html_to_text(d.get("message"), max_len=3000),
            url=f"{nx.site_url}/mod/forum/discuss.php?d={d.get('discussion')}",
            attachments=[file_ref(nx, f) for f in (d.get("attachments") or [])],
            pinned=bool(d.get("pinned")),
        )

    async def announcements(
        self, days: int = 7, course_id: int | None = None, *, per_forum: int = 10, include_all_forums: bool = False
    ) -> tuple[list[Announcement], list[str]]:
        nx = self.nx
        warnings: list[str] = []
        forums = await self.raw_forums()
        lookup = await nx.courses.lookup()
        if course_id is not None:
            allowed = {int(course_id)}
        else:
            allowed = set(await nx.courses.current_course_ids())
        selected = [
            f for f in forums
            if int(f.get("course") or 0) in allowed and (include_all_forums or f.get("type") == "news")
        ]
        since_ts = nx.now_ts() - max(1, int(days)) * 86400

        async def one(forum: dict[str, Any]) -> list[Announcement]:
            course_name = await nx.courses.course_name(int(forum.get("course") or 0), lookup)
            try:
                discussions = await self.raw_discussions(int(forum["id"]), per_forum)
            except NexusError as exc:
                warnings.append(f"{course_name} / {forum.get('name')}: {exc.message}")
                return []
            return [
                self._announcement(d, forum, course_name)
                for d in discussions
                if int(d.get("created") or d.get("timemodified") or 0) >= since_ts
            ]

        groups = await asyncio.gather(*(one(f) for f in selected))
        items = [a for g in groups for a in g]
        items.sort(key=lambda a: (a.posted.unix if a.posted else 0), reverse=True)
        return items, warnings

    # -- popup notifications ----------------------------------------------
    async def notifications(self, since_ts: int, *, limit: int = 30) -> list[Notification]:
        nx = self.nx
        if not nx.has("message_popup_get_popup_notifications"):
            return []

        async def fetch() -> list[dict[str, Any]]:
            payload = await nx.client.call(
                "message_popup_get_popup_notifications", useridto=nx.site.user_id, newestfirst=1, limit=int(limit), offset=0
            )
            return list((payload or {}).get("notifications", []))

        rows, _ = await nx.cache.get_or_fetch(("notifications", int(limit)), nx.ttl.announcements, fetch)
        out: list[Notification] = []
        for n in rows:
            created = int(n.get("timecreated") or 0)
            if created < since_ts:
                continue
            out.append(
                Notification(
                    id=int(n.get("id") or 0),
                    subject=html_to_text(n.get("subject") or n.get("shortenedsubject")) or "",
                    message=html_to_text(n.get("smallmessage") or n.get("fullmessage") or n.get("text"), max_len=1000),
                    url=n.get("contexturl") or None,
                    created=nx.when(created),
                    read=bool(n.get("read")),
                    component=n.get("component") or None,
                    event_type=n.get("eventtype") or None,
                )
            )
        return out

    # -- what changed since ------------------------------------------------
    async def course_updates(self, since: datetime, *, course_id: int | None = None) -> dict[str, Any]:
        nx = self.nx
        since_ts = unix(since)
        warnings: list[str] = []
        lookup = await nx.courses.lookup()
        if course_id is not None:
            courses = [lookup[int(course_id)]] if int(course_id) in lookup else []
            if not courses:
                warnings.append(f"Course {course_id} is not among your enrolled courses.")
        else:
            courses = await nx.courses.current_courses()

        updates: list[CourseUpdate] = []
        if nx.has("core_course_get_updates_since"):

            async def one(course: Any) -> None:
                try:
                    payload = await nx.client.call("core_course_get_updates_since", courseid=course.id, since=since_ts)
                except NexusError as exc:
                    warnings.append(f"{course.name}: {exc.message}")
                    return
                instances = (payload or {}).get("instances", []) or []
                if not instances:
                    return
                modules: dict[int, dict[str, Any]] = {}
                try:
                    for s in await nx.courses.contents(course.id):
                        for m in s.get("modules", []) or []:
                            modules[int(m["id"])] = m
                except NexusError:
                    pass
                for inst in instances:
                    cmid = int(inst.get("id") or 0)
                    changes = []
                    latest = 0
                    for u in inst.get("updates", []) or []:
                        name = str(u.get("name") or "")
                        changes.append(UPDATE_LABELS.get(name, name.replace("_", " ")))
                        latest = max(latest, int(u.get("timeupdated") or 0))
                    if not changes:
                        continue
                    mod = modules.get(cmid, {})
                    updates.append(
                        CourseUpdate(
                            course_id=course.id,
                            course=course.name,
                            cmid=cmid,
                            module=mod.get("name"),
                            module_type=mod.get("modname"),
                            changes=sorted(set(changes)),
                            url=mod.get("url"),
                            updated=nx.when(latest) if latest else None,
                        )
                    )

            await asyncio.gather(*(one(c) for c in courses))
        else:
            warnings.append("core_course_get_updates_since is not enabled on Nexus; module-level changes unavailable.")

        days = max(1, int((nx.now_ts() - since_ts) / 86400) + 1)
        try:
            announcements, w = await self.announcements(days=days, course_id=course_id)
            warnings.extend(w)
            announcements = [a for a in announcements if a.posted and a.posted.unix >= since_ts]
        except NexusError as exc:
            announcements = []
            warnings.append(f"announcements: {exc.message}")
        try:
            notes = await self.notifications(since_ts)
        except NexusError as exc:
            notes = []
            warnings.append(f"notifications: {exc.message}")
        updates.sort(key=lambda u: (u.updated.unix if u.updated else 0), reverse=True)
        return {
            "since": since.isoformat(timespec="minutes"),
            "courses_checked": [c.name for c in courses],
            "module_updates": [u.model_dump() for u in updates],
            "announcements": [a.model_dump() for a in announcements],
            "notifications": [n.model_dump() for n in notes],
            "warnings": warnings,
        }
