"""Calendar events (core_calendar functions).

Two sources are merged:

* ``core_calendar_get_calendar_events`` — every event in the window (course,
  user, site and activity events), but without URLs/actions.
* ``core_calendar_get_action_events_by_timesort`` — the "timeline" events
  (assignment due, quiz close, ...) with URLs, action info and Moodle's own
  ``overdue`` flag.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..errors import NexusError
from ..htmltext import html_to_text
from ..models.event import CalendarEvent, EventAction

if TYPE_CHECKING:
    from .nexus import Nexus


ACTION_EVENT_PAGE_SIZE = 50  # hard limit enforced by Moodle ("Limit must be between 1 and 50")
ACTION_EVENT_PAGES = 4


class CalendarService:
    def __init__(self, nx: "Nexus") -> None:
        self.nx = nx

    def _from_calendar(self, e: dict[str, Any], names: dict[int, str]) -> CalendarEvent:
        nx = self.nx
        course_id = int(e.get("courseid") or 0)
        course_id = course_id if course_id > 1 else None
        start = int(e.get("timestart") or 0)
        duration = int(e.get("timeduration") or 0)
        return CalendarEvent(
            id=int(e["id"]),
            name=html_to_text(e.get("name")) or "",
            course_id=course_id,
            course=names.get(course_id) if course_id else None,
            start=nx.when(start),
            end=nx.when(start + duration) if duration else None,
            description=html_to_text(e.get("description"), max_len=600) or None,
            url=f"{nx.site_url}/calendar/view.php?view=day&time={start}" if start else None,
            type=e.get("eventtype") or "unknown",
            module=e.get("modulename") or None,
            instance=int(e["instance"]) if e.get("instance") else None,
            source="calendar",
        )

    def _from_action(self, e: dict[str, Any], names: dict[int, str]) -> CalendarEvent:
        nx = self.nx
        course = e.get("course") or {}
        course_id = int(course.get("id") or e.get("courseid") or 0)
        course_id = course_id if course_id > 1 else None
        start = int(e.get("timestart") or 0)
        duration = int(e.get("timeduration") or 0)
        action = e.get("action") or None
        return CalendarEvent(
            id=int(e["id"]),
            name=html_to_text(e.get("name")) or "",
            course_id=course_id,
            course=(course.get("fullname") or names.get(course_id)) if course_id else None,
            start=nx.when(start),
            end=nx.when(start + duration) if duration else None,
            description=html_to_text(e.get("description"), max_len=600) or None,
            url=e.get("url") or None,
            type=e.get("eventtype") or "unknown",
            module=e.get("modulename") or None,
            instance=int(e["instance"]) if e.get("instance") else None,
            action=EventAction(
                name=action.get("name"),
                url=action.get("url"),
                actionable=bool(action.get("actionable")) if "actionable" in action else None,
                item_count=int(action["itemcount"]) if action.get("itemcount") is not None else None,
            )
            if action
            else None,
            overdue=bool(e["overdue"]) if "overdue" in e else None,
            source="action",
        )

    async def events(self, days: int = 14, *, course_id: int | None = None) -> tuple[list[CalendarEvent], list[str]]:
        nx = self.nx
        days = max(1, int(days))
        now_ts = nx.now_ts()
        end_ts = now_ts + days * 86400
        warnings: list[str] = []
        lookup = await nx.courses.lookup()
        names = {cid: c.name for cid, c in lookup.items()}
        if course_id is not None:
            course_ids = [int(course_id)]
        else:
            course_ids = await nx.courses.current_course_ids()
        cache_key = ("calendar", tuple(course_ids), now_ts // 300, days)

        async def fetch() -> dict[str, Any]:
            merged: dict[int, CalendarEvent] = {}
            if nx.has("core_calendar_get_calendar_events"):
                try:
                    payload = await nx.client.call(
                        "core_calendar_get_calendar_events",
                        events={"courseids": course_ids, "groupids": [], "categoryids": []},
                        options={"userevents": 1, "siteevents": 1, "timestart": now_ts, "timeend": end_ts, "ignorehidden": 1},
                    )
                    for e in (payload or {}).get("events", []):
                        if course_id is not None and int(e.get("courseid") or 0) not in (int(course_id), 0, 1):
                            continue
                        merged[int(e["id"])] = self._from_calendar(e, names)
                    for w in (payload or {}).get("warnings", []):
                        warnings.append(f"{w.get('warningcode', 'warning')}: {w.get('message', '')}")
                except NexusError as exc:
                    warnings.append(f"core_calendar_get_calendar_events: {exc.message}")
            if nx.has("core_calendar_get_action_events_by_timesort"):
                try:
                    after_id = 0
                    for _page in range(ACTION_EVENT_PAGES):  # Moodle caps limitnum at 50; page with aftereventid
                        params = {
                            "timesortfrom": now_ts,
                            "timesortto": end_ts,
                            "limitnum": ACTION_EVENT_PAGE_SIZE,
                            "limittononsuspendedevents": 1,
                        }
                        if after_id:
                            params["aftereventid"] = after_id
                        payload = await nx.client.call("core_calendar_get_action_events_by_timesort", **params)
                        rows = (payload or {}).get("events", []) or []
                        for e in rows:
                            ev = self._from_action(e, names)
                            if course_id is not None and ev.course_id != int(course_id):
                                continue
                            existing = merged.get(ev.id)
                            if existing is not None:
                                ev.description = ev.description or existing.description
                            merged[ev.id] = ev
                        last_id = int((payload or {}).get("lastid") or 0)
                        if len(rows) < ACTION_EVENT_PAGE_SIZE or not last_id or last_id == after_id:
                            break
                        after_id = last_id
                except NexusError as exc:
                    warnings.append(f"core_calendar_get_action_events_by_timesort: {exc.message}")
            if not merged and not nx.has("core_calendar_get_calendar_events") and not nx.has(
                "core_calendar_get_action_events_by_timesort"
            ):
                nx.require("core_calendar_get_calendar_events", feature="calendar events")
            events = [e for e in merged.values() if e.start and now_ts - 60 <= e.start.unix <= end_ts]
            events.sort(key=lambda e: (e.start.unix if e.start else 0, e.name))
            return {"events": events, "warnings": list(warnings)}

        value, _ = await nx.cache.get_or_fetch(cache_key, nx.ttl.calendar, fetch)
        return list(value["events"]), list(value["warnings"])
