import pytest

from nexus_mcp.timeutil import parse_since
from tests.conftest import DAY, HOUR, NOW, course, module, section

CSC = "CSC-385 - Computer Graphics"
WEB = "Web Programming"


def discussion(id, subject, created, message="<p>Body</p>", author="Prof. Ada Lovelace"):
    return {"id": id, "name": subject, "groupid": -1, "timemodified": created, "usermodified": 2, "timestart": 0, "timeend": 0, "discussion": id + 100, "parent": 0, "userid": 2, "created": created, "modified": created, "mailed": 1, "subject": subject, "message": message, "messageformat": 1, "messagetrust": 0, "attachment": "", "attachments": [], "totalscore": 0, "mailnow": 0, "userfullname": author, "usermodifiedfullname": author, "userpictureurl": "", "usermodifiedpictureurl": "", "numreplies": 0, "numunread": 0, "pinned": False, "locked": False, "starred": False, "canreply": False, "canlock": False, "canfavourite": False}


@pytest.fixture
def world(fake):
    fake.on("core_enrol_get_users_courses", [course(1, CSC, "CSC-385"), course(2, WEB, "WEB")])
    fake.on("core_course_get_courses_by_field", {"courses": [], "warnings": []})
    fake.on("mod_forum_get_forums_by_courses", [{"id": 5, "course": 1, "type": "news", "name": "Announcements"}, {"id": 6, "course": 2, "type": "news", "name": "Announcements"}, {"id": 7, "course": 2, "type": "general", "name": "Q&A"}])
    discussions = {
        5: [discussion(1, "Lab 3 deadline extended", NOW - 3 * HOUR), discussion(2, "Welcome", NOW - 30 * DAY)],
        6: [discussion(3, "Midterm info", NOW - DAY)],
        7: [discussion(4, "Question about promises", NOW - HOUR, author="A Student")],
    }
    fake.on("mod_forum_get_forum_discussions", lambda form: {"discussions": discussions[int(form["forumid"])], "warnings": []})
    fake.on("core_course_get_updates_since", lambda form: {"instances": [{"contextlevel": "module", "id": 14, "updates": [{"name": "configuration", "timeupdated": NOW - 2 * HOUR}, {"name": "fileareas", "timeupdated": NOW - 2 * HOUR, "itemids": [1]}]}], "warnings": []} if form["courseid"] == "1" else {"instances": [], "warnings": []})
    fake.on("core_course_get_contents", lambda form: [section(1, "Week 3", [module(14, "page", "Lab 3 notes")])] if form["courseid"] == "1" else [])
    fake.on("message_popup_get_popup_notifications", {"notifications": [{"id": 900, "useridfrom": 2, "useridto": 7, "subject": "Lab 2 graded", "shortenedsubject": "Lab 2 graded", "text": "", "fullmessage": "Your submission has been graded", "fullmessageformat": 1, "fullmessagehtml": "", "smallmessage": "Your submission has been graded", "contexturl": "https://nexus.test/mod/assign/view.php?id=30", "contexturlname": "", "timecreated": NOW - 5 * HOUR, "timecreatedpretty": "", "timeread": None, "read": False, "deleted": False, "iconurl": "", "component": "mod_assign", "eventtype": "assign_notification", "customdata": ""}, {"id": 901, "subject": "Old", "text": "", "fullmessage": "old", "smallmessage": "old", "contexturl": "", "timecreated": NOW - 10 * DAY, "read": True, "component": "moodle", "eventtype": "x"}], "unreadcount": 1})
    return fake


async def test_recent_announcements(world, nexus):
    items, warnings = await nexus.notifications.announcements(days=7)
    assert warnings == []
    assert [a.subject for a in items] == ["Lab 3 deadline extended", "Midterm info"]
    first = items[0]
    assert first.course == CSC and first.author == "Prof. Ada Lovelace" and first.message == "Body"
    assert first.url == "https://nexus.test/mod/forum/discuss.php?d=101"
    assert first.posted.relative == "3 hours ago"
    items, _ = await nexus.notifications.announcements(days=60)
    assert len(items) == 3
    items, _ = await nexus.notifications.announcements(days=7, include_all_forums=True)
    assert [a.subject for a in items][0] == "Question about promises"
    items, _ = await nexus.notifications.announcements(days=7, course_id=2)
    assert [a.subject for a in items] == ["Midterm info"]
    assert world.calls_to("mod_forum_get_forum_discussions")[0]["sortorder"] == "3"


async def test_course_updates(world, nexus):
    since = parse_since("24h", nexus.now())
    data = await nexus.notifications.course_updates(since)
    assert data["courses_checked"] == [CSC, WEB]
    assert len(data["module_updates"]) == 1
    upd = data["module_updates"][0]
    assert upd["module"] == "Lab 3 notes" and upd["module_type"] == "page"
    assert upd["changes"] == ["files added or changed", "settings or description changed"]
    assert [a["subject"] for a in data["announcements"]] == ["Lab 3 deadline extended", "Midterm info"]
    assert [n["subject"] for n in data["notifications"]] == ["Lab 2 graded"]
    assert data["warnings"] == []
    assert world.calls_to("core_course_get_updates_since")[0]["since"] == str(NOW - DAY)


def test_parse_since_variants():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.fromtimestamp(NOW, ZoneInfo("America/New_York"))
    assert parse_since("24h", now) == now.replace(hour=12) - __import__("datetime").timedelta(days=1)
    assert parse_since("2d", now).timestamp() == NOW - 2 * DAY
    assert parse_since("yesterday", now).isoformat() == "2026-09-10T00:00:00-04:00"
    assert parse_since("today", now).isoformat() == "2026-09-11T00:00:00-04:00"
    assert parse_since("2026-09-10T08:00", now).isoformat() == "2026-09-10T08:00:00-04:00"
    assert parse_since("2026-09-10T12:00:00Z", now).timestamp() == parse_since("2026-09-10T08:00", now).timestamp()
    assert parse_since(str(NOW - 60), now).timestamp() == NOW - 60
    assert parse_since(None, now).timestamp() == NOW - DAY
    with pytest.raises(ValueError):
        parse_since("whenever", now)
