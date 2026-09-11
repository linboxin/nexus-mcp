"""Composition layer: briefings, workload analysis, prioritisation.

Nothing here talks to Moodle directly; it composes the service layer. Any
estimate is labelled as a nexus-mcp heuristic, never as instructor data.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Awaitable, TypeVar

from .errors import NexusAuthError, NexusError
from .models.assignment import Assignment
from .models.event import CalendarEvent
from .moodle.nexus import Nexus
from .timeutil import end_of_day, unix

T = TypeVar("T")

ASSESSMENT_MODULES = {"quiz", "lesson", "workshop", "h5pactivity", "scorm", "turnitintooltwo", "choice", "feedback"}
DEADLINE_EVENT_TYPES = {"due", "close", "expectcompletionon"}
LEVEL_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def dedupe_warnings(warnings: list[str]) -> list[str]:
    """Drop repeated notes. The same 'N assignments skipped' line arrives once per
    underlying assignment call (all courses, current courses); keep the first."""
    seen: set[str] = set()
    out: list[str] = []
    for w in warnings:
        key = re.sub(r"^\d+ ", "", w.strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return out


async def _safe(coro: Awaitable[T], default: T, warnings: list[str], label: str) -> T:
    try:
        return await coro
    except NexusAuthError:
        raise
    except NexusError as exc:
        warnings.append(f"{label}: {exc.message}")
        return default


def _status_label(a: Assignment) -> str:
    if a.status is None:
        return "status unknown"
    s = a.status
    if s.status == "graded":
        return f"graded ({s.grade})" if s.grade else "graded"
    if s.status == "submitted":
        return "submitted"
    if s.status == "draft":
        return "draft saved, not submitted"
    if s.status == "overdue":
        return "OVERDUE, not submitted"
    return "not started" if s.submission_required else "no submission required"


def _course_label(a: Assignment) -> str:
    return a.course_short or a.course


def _event_label(e: CalendarEvent) -> str:
    kind = {"close": "closes", "open": "opens", "due": "due", "expectcompletionon": "expected done"}.get(e.type, e.type)
    return f"{e.name} ({e.module or 'event'} {kind})" if e.module else e.name


# --------------------------------------------------------------------------- #
# Prioritisation
# --------------------------------------------------------------------------- #


def prioritize(
    upcoming: list[Assignment],
    overdue: list[Assignment],
    events: list[CalendarEvent],
    now: datetime,
) -> list[dict[str, Any]]:
    """Rank work items. Returns dicts with a ``priority`` and a human ``reason``."""
    now_ts = unix(now)
    items: list[dict[str, Any]] = []

    def add(**item: Any) -> None:
        items.append(item)

    for a in overdue:
        closed = a.can_still_submit is False
        due_rel = a.due.relative if a.due else "unknown"
        if closed:
            reason = f"Overdue (due {due_rel}) and Nexus no longer accepts submissions; contact the instructor."
            level = "MEDIUM"
        else:
            reason = f"Overdue since {a.due.short if a.due else '?'} ({due_rel}) and still not submitted."
            level = "HIGH"
        add(kind="assignment", assignment_id=a.id, title=a.name, course=a.course, course_short=a.course_short,
            due=a.due.model_dump() if a.due else None, status=a.status.status if a.status else "unknown",
            status_detail=a.status.detail if a.status else a.status_note, priority=level, reason=reason, url=a.url,
            _sort=(LEVEL_RANK[level], a.due.unix if a.due else 0))

    overdue_ids = {a.id for a in overdue}
    for a in upcoming:
        if a.id in overdue_ids:
            continue
        state = a.status.status if a.status else "unknown"
        if state in ("submitted", "graded"):
            continue
        hours = (a.due.unix - now_ts) / 3600 if a.due else 24 * 30
        if hours <= 24:
            level, why = "HIGH", f"Due {a.due.relative if a.due else 'soon'}"
        elif hours <= 72:
            level, why = "MEDIUM", f"Due {a.due.relative if a.due else 'soon'}"
        else:
            level, why = "LOW", f"Due {a.due.short if a.due else 'later'} ({a.due.relative if a.due else ''})".replace(" ()", "")
        if state == "draft":
            why += "; a draft is saved but not submitted"
        elif state == "not_started":
            why += "; not started"
        elif state == "unknown":
            why += "; submission status could not be checked"
        if a.points:
            why += f"; worth {a.points:g} points"
        add(kind="assignment", assignment_id=a.id, title=a.name, course=a.course, course_short=a.course_short,
            due=a.due.model_dump() if a.due else None, status=state,
            status_detail=a.status.detail if a.status else a.status_note, priority=level, reason=why + ".", url=a.url,
            _sort=(LEVEL_RANK[level], a.due.unix if a.due else 2**40))

    assignment_instances = {a.id for a in upcoming} | overdue_ids
    for e in events:
        if e.module == "assign" and (e.instance in assignment_instances):
            continue
        if not e.start:
            continue
        is_deadline = e.type in DEADLINE_EVENT_TYPES and (e.module in ASSESSMENT_MODULES or e.module is None)
        if e.module and not is_deadline and e.type not in DEADLINE_EVENT_TYPES:
            if e.type == "open":
                continue  # "quiz opens" is informational, not work
        hours = (e.start.unix - now_ts) / 3600
        if e.overdue:
            level, why = "HIGH", f"Moodle marks this as overdue ({e.start.relative})"
        elif is_deadline and hours <= 24:
            level, why = "HIGH", f"{_event_label(e)} {e.start.relative}"
        elif is_deadline and hours <= 72:
            level, why = "MEDIUM", f"{_event_label(e)} {e.start.relative}"
        elif is_deadline:
            level, why = "LOW", f"{_event_label(e)} {e.start.short}"
        else:
            level, why = "LOW", f"Calendar event {e.start.relative}"
        add(kind="event", event_id=e.id, title=e.name, course=e.course, course_short=None,
            due=e.start.model_dump(), status=None, status_detail=(e.action.name if e.action else None),
            priority=level, reason=why + ".", url=e.url, _sort=(LEVEL_RANK[level], e.start.unix))

    items.sort(key=lambda i: i["_sort"])
    for rank, item in enumerate(items, start=1):
        item.pop("_sort", None)
        item["rank"] = rank
    return items


def render_priorities(items: list[dict[str, Any]], *, limit: int | None = None) -> str:
    if not items:
        return "Nothing is waiting on you right now."
    lines = []
    for item in items[:limit] if limit else items:
        due = item["due"]["short"] if item.get("due") else "no date"
        status = item.get("status_detail") or item.get("status") or ""
        course = item.get("course_short") or item.get("course") or ""
        lines.append(f"{item['rank']}. {course} — {item['title']}" if course else f"{item['rank']}. {item['title']}")
        lines.append(f"   Due: {due}")
        if status:
            lines.append(f"   Status: {status}")
        lines.append(f"   Priority: {item['priority']}")
        lines.append(f"   Why: {item['reason']}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Briefings
# --------------------------------------------------------------------------- #


def _greeting(now: datetime) -> str:
    if now.hour < 12:
        return "Good morning."
    if now.hour < 18:
        return "Good afternoon."
    return "Good evening."


def _assignment_line(a: Assignment, *, with_day: bool = False) -> str:
    when = "no due date"
    if a.due:
        when = a.due.short if with_day else f"due {a.due.short.split(', ', 1)[-1]}"
    return f"{_course_label(a)} — {a.name} — {when} — {_status_label(a)}"


async def daily_briefing(nx: Nexus) -> dict[str, Any]:
    now = nx.now()
    today_end = unix(end_of_day(now))
    tomorrow_end = today_end + 86400
    warnings: list[str] = []

    upcoming, w = await _safe(nx.assignments.upcoming(days=7), ([], []), warnings, "upcoming assignments")
    warnings.extend(w)
    overdue, w = await _safe(nx.assignments.overdue(), ([], []), warnings, "overdue assignments")
    warnings.extend(w)
    events, w = await _safe(nx.calendar.events(days=2), ([], []), warnings, "calendar")
    warnings.extend(w)
    announcements, w = await _safe(nx.notifications.announcements(days=2), ([], []), warnings, "announcements")
    warnings.extend(w)
    updates = await _safe(nx.notifications.course_updates(now - timedelta(hours=24)), None, warnings, "course updates")

    today = [a for a in upcoming if a.due and a.due.unix <= today_end]
    tomorrow = [a for a in upcoming if a.due and today_end < a.due.unix <= tomorrow_end]
    soon = [a for a in upcoming if a.due and a.due.unix > tomorrow_end]
    assignment_ids = {a.id for a in upcoming} | {a.id for a in overdue}
    other_events = [e for e in events if not (e.module == "assign" and e.instance in assignment_ids)]
    events_today = [e for e in other_events if e.start and e.start.unix <= today_end]
    events_tomorrow = [e for e in other_events if e.start and today_end < e.start.unix <= tomorrow_end]
    module_updates = list((updates or {}).get("module_updates", []))
    update_warnings = list((updates or {}).get("warnings", []))
    priorities = prioritize(upcoming, overdue, other_events, now)

    lines = [f"{_greeting(now)} It is {now:%A}, {now:%B} {now.day}.", ""]
    lines.append("🔴 TODAY")
    lines.extend([_assignment_line(a) for a in today] or ["Nothing due today."])
    if events_today:
        lines.extend(f"{e.course or 'Nexus'} — {_event_label(e)} — {e.start.short.split(', ', 1)[-1] if e.start else ''}" for e in events_today)
    lines.append("")
    lines.append("🟡 TOMORROW")
    lines.extend([_assignment_line(a) for a in tomorrow] or ["Nothing due tomorrow."])
    if events_tomorrow:
        lines.extend(f"{e.course or 'Nexus'} — {_event_label(e)}" for e in events_tomorrow)
    lines.append("")
    if soon:
        lines.append("🟢 COMING UP (next 7 days)")
        lines.extend(_assignment_line(a, with_day=True) for a in soon)
        lines.append("")
    lines.append("📢 NEW")
    new_lines = [
        f"{a.course} — announcement: “{a.subject}” ({a.author or 'staff'}, {a.posted.relative if a.posted else ''})"
        for a in announcements[:6]
    ]
    new_lines += [f"{u['course']} — {u.get('module') or 'module ' + str(u['cmid'])}: {', '.join(u['changes'])}" for u in module_updates[:8]]
    lines.extend(new_lines or ["No new announcements or course changes in the last day."])
    lines.append("")
    lines.append("⚠️ OVERDUE")
    lines.extend([_assignment_line(a, with_day=True) for a in overdue] or ["None."])
    lines.append("")
    lines.append("Recommended priority:")
    top = priorities[:4]
    lines.extend(
        f"{i}. {item.get('course_short') or item.get('course') or ''} {item['title']} — {item['reason']}".replace("  ", " ")
        for i, item in enumerate(top, start=1)
    ) if top else lines.append("You're clear. Nothing urgent is pending.")
    all_warnings = dedupe_warnings(warnings + update_warnings)
    if all_warnings:
        lines.append("")
        lines.append("Notes: " + "; ".join(all_warnings))

    return {
        "generated_at": now.isoformat(timespec="minutes"),
        "timezone": nx.site.tz_name,
        "text": "\n".join(lines),
        "today": [a.model_dump() for a in today],
        "tomorrow": [a.model_dump() for a in tomorrow],
        "due_soon": [a.model_dump() for a in soon],
        "overdue": [a.model_dump() for a in overdue],
        "events_today": [e.model_dump() for e in events_today],
        "events_tomorrow": [e.model_dump() for e in events_tomorrow],
        "announcements": [a.model_dump() for a in announcements],
        "course_updates": module_updates,
        "priorities": priorities[:8],
        "warnings": all_warnings,
    }


async def weekly_briefing(nx: Nexus, days: int = 7) -> dict[str, Any]:
    now = nx.now()
    warnings: list[str] = []
    upcoming, w = await _safe(nx.assignments.upcoming(days=days), ([], []), warnings, "upcoming assignments")
    warnings.extend(w)
    overdue, w = await _safe(nx.assignments.overdue(), ([], []), warnings, "overdue assignments")
    warnings.extend(w)
    events, w = await _safe(nx.calendar.events(days=days), ([], []), warnings, "calendar")
    warnings.extend(w)
    assignment_ids = {a.id for a in upcoming}
    other_events = [e for e in events if not (e.module == "assign" and e.instance in assignment_ids)]

    by_course: dict[str, list[tuple[int, str, dict[str, Any]]]] = {}
    for a in upcoming:
        key = a.course
        by_course.setdefault(key, []).append(
            (a.due.unix if a.due else 0, f"- {a.name} — {a.due.short if a.due else 'no date'} ({_status_label(a)})", a.model_dump())
        )
    for e in other_events:
        key = e.course or "Nexus (site/personal events)"
        by_course.setdefault(key, []).append(
            (e.start.unix if e.start else 0, f"- {_event_label(e)} — {e.start.short if e.start else ''}", e.model_dump())
        )
    end = now + timedelta(days=days)
    lines = [f"Week of {now:%b} {now.day} – {end:%b} {end.day} ({nx.site.tz_name})", ""]
    if overdue:
        lines.append("⚠️ OVERDUE")
        lines.extend(_assignment_line(a, with_day=True) for a in overdue)
        lines.append("")
    if not by_course:
        lines.append("No assignments or events with dates in this window.")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for course_name in sorted(by_course):
        entries = sorted(by_course[course_name], key=lambda t: t[0])
        lines.append(course_name)
        lines.extend(line for _, line, _ in entries)
        lines.append("")
        grouped[course_name] = [payload for _, _, payload in entries]
    warnings = dedupe_warnings(warnings)
    if warnings:
        lines.append("Notes: " + "; ".join(warnings))
    return {
        "generated_at": now.isoformat(timespec="minutes"),
        "window_days": days,
        "text": "\n".join(lines).rstrip(),
        "by_course": grouped,
        "overdue": [a.model_dump() for a in overdue],
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- #
# Workload
# --------------------------------------------------------------------------- #


def estimate_hours(a: Assignment) -> tuple[float, str]:
    hours = 2.0
    basis = ["2h baseline for an assignment"]
    name = a.name.lower()
    desc_len = len(a.description or "")
    if desc_len > 1200:
        hours += 1.0
        basis.append("long instructions (+1h)")
    if a.points and a.points >= 50:
        hours += 1.5
        basis.append(f"worth {a.points:g} points (+1.5h)")
    if any(k in name for k in ("project", "paper", "essay", "report", "final", "presentation", "portfolio")):
        hours += 2.0
        basis.append("project/paper-type name (+2h)")
    if any(k in name for k in ("quiz", "reading", "reflection", "discussion", "survey", "check-in", "checkin", "attendance")):
        hours = max(0.5, hours - 1.0)
        basis.append("light-weight type (-1h)")
    if "file upload" in a.submission_types and "online text" not in a.submission_types:
        basis.append("file submission")
    return round(hours, 1), "; ".join(basis)


async def workload_analysis(nx: Nexus, days: int = 7) -> dict[str, Any]:
    now = nx.now()
    warnings: list[str] = []
    upcoming, w = await _safe(nx.assignments.upcoming(days=days), ([], []), warnings, "upcoming assignments")
    warnings.extend(w)
    overdue, w = await _safe(nx.assignments.overdue(), ([], []), warnings, "overdue assignments")
    warnings.extend(w)
    events, w = await _safe(nx.calendar.events(days=days), ([], []), warnings, "calendar")
    warnings.extend(w)
    assignment_ids = {a.id for a in upcoming}
    assessments = [
        e for e in events
        if e.module in ASSESSMENT_MODULES and e.type in DEADLINE_EVENT_TYPES and not (e.module == "assign" and e.instance in assignment_ids)
    ]
    other_events = [e for e in events if e not in assessments and not (e.module == "assign" and e.instance in assignment_ids)]

    by_course: dict[str, int] = {}
    by_day: dict[str, list[str]] = {}
    points = 0.0
    for a in upcoming:
        by_course[a.course] = by_course.get(a.course, 0) + 1
        day = a.due.iso[:10] if a.due else "undated"
        by_day.setdefault(day, []).append(f"{_course_label(a)}: {a.name}")
        points += a.points or 0
    for e in assessments:
        day = e.start.iso[:10] if e.start else "undated"
        by_day.setdefault(day, []).append(f"{e.course or 'Nexus'}: {e.name} ({e.module})")
    pending = [a for a in upcoming if not (a.status and a.status.status in ("submitted", "graded"))]
    facts = {
        "window_days": days,
        "assignments_due": len(upcoming),
        "assignments_pending": len(pending),
        "assignments_submitted_or_graded": len(upcoming) - len(pending),
        "assignments_not_started": sum(1 for a in pending if a.status and a.status.status == "not_started"),
        "drafts": sum(1 for a in pending if a.status and a.status.status == "draft"),
        "overdue": len(overdue),
        "assessments_closing": len(assessments),
        "other_calendar_events": len(other_events),
        "total_points_due": points,
        "by_course": by_course,
        "by_day": dict(sorted(by_day.items())),
        "source": "Moodle-provided facts (mod_assign, calendar, submission status)",
    }
    estimates = []
    total_hours = 0.0
    for a in pending:
        hours, basis = estimate_hours(a)
        total_hours += hours
        estimates.append({"title": a.name, "course": a.course, "due": a.due.short if a.due else None, "estimated_hours": hours, "basis": basis})
    for e in assessments:
        h = 1.5 if e.module == "quiz" else 2.0
        total_hours += h
        estimates.append({"title": e.name, "course": e.course, "due": e.start.short if e.start else None, "estimated_hours": h, "basis": f"{e.module} deadline; flat {h}h heuristic"})
    for a in overdue:
        hours, basis = estimate_hours(a)
        total_hours += hours
        estimates.append({"title": a.name, "course": a.course, "due": a.due.short if a.due else None, "estimated_hours": hours, "basis": basis + "; overdue"})
    busiest = max(by_day.items(), key=lambda kv: len(kv[1]))[0] if by_day else None
    level = "light" if total_hours < 6 else "moderate" if total_hours < 15 else "heavy"
    text = (
        f"Next {days} days: {facts['assignments_due']} assignment(s) due ({facts['assignments_pending']} still pending), "
        f"{facts['assessments_closing']} quiz/assessment deadline(s), {facts['overdue']} overdue."
        + (f" Busiest day: {busiest} ({len(by_day[busiest])} items)." if busiest else "")
        + f"\nEstimated effort (nexus-mcp heuristic, not from instructors): about {total_hours:g} hours — {level} workload."
    )
    return {
        "generated_at": now.isoformat(timespec="minutes"),
        "text": text,
        "facts": facts,
        "estimates": {
            "disclaimer": (
                "Hour estimates are generated by nexus-mcp from assignment names, description length and point "
                "values. They are NOT instructor estimates; Nexus provides no official effort data."
            ),
            "total_estimated_hours": round(total_hours, 1),
            "workload_level": level,
            "items": estimates,
        },
        "warnings": dedupe_warnings(warnings),
    }


async def what_should_i_do_next(nx: Nexus) -> dict[str, Any]:
    now = nx.now()
    warnings: list[str] = []
    upcoming, w = await _safe(nx.assignments.upcoming(days=14), ([], []), warnings, "upcoming assignments")
    warnings.extend(w)
    overdue, w = await _safe(nx.assignments.overdue(), ([], []), warnings, "overdue assignments")
    warnings.extend(w)
    events, w = await _safe(nx.calendar.events(days=14), ([], []), warnings, "calendar")
    warnings.extend(w)
    announcements, w = await _safe(nx.notifications.announcements(days=3), ([], []), warnings, "announcements")
    warnings.extend(w)
    items = prioritize(upcoming, overdue, events, now)
    recent_by_course = {}
    for a in announcements:
        recent_by_course.setdefault(a.course, []).append(a.subject)
    for item in items:
        subjects = recent_by_course.get(item.get("course") or "")
        if subjects:
            item["recent_announcements"] = subjects[:3]
            item["reason"] += f" Recent announcement in this course: “{subjects[0]}”."
    done = [
        {"title": a.name, "course": a.course, "status": a.status.status if a.status else None}
        for a in upcoming
        if a.status and a.status.status in ("submitted", "graded")
    ]
    text = render_priorities(items, limit=10)
    if done:
        text += "\n\nAlready handled: " + "; ".join(f"{d['course']} {d['title']} ({d['status']})" for d in done)
    return {
        "generated_at": now.isoformat(timespec="minutes"),
        "text": text,
        "items": items,
        "already_handled": done,
        "method": (
            "Priority = overdue-and-submittable first, then due within 24h (HIGH), within 72h (MEDIUM), later (LOW); "
            "submitted/graded work is excluded; drafts and point values are mentioned in the reason."
        ),
        "warnings": dedupe_warnings(warnings),
    }
