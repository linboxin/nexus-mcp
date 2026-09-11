import pytest

from nexus_mcp.errors import NexusUnsupportedError
from tests.conftest import DAY, HOUR, NOW, course, make_nexus, site_info

CSC = "CSC-385 - Computer Graphics"


def cal_event(id, name, start, *, courseid=101, eventtype="due", modulename="assign", instance=1, duration=0, description=""):
    return {"id": id, "name": name, "description": description, "format": 1, "courseid": courseid, "categoryid": 0, "groupid": 0, "userid": 0, "repeatid": 0, "modulename": modulename, "instance": instance, "eventtype": eventtype, "timestart": start, "timeduration": duration, "visible": 1, "uuid": "", "sequence": 1, "timemodified": NOW, "subscriptionid": None}


def action_event(id, name, start, *, courseid=101, eventtype="due", modulename="assign", instance=1, overdue=False, url=None):
    return {"id": id, "name": name, "description": "", "descriptionformat": 1, "location": "", "categoryid": None, "groupid": None, "userid": 7, "repeatid": None, "eventcount": None, "component": None, "modulename": modulename, "activityname": name, "activitystr": "Assignment", "instance": instance, "eventtype": eventtype, "timestart": start, "timeduration": 0, "timesort": start, "timeusermidnight": start, "visible": 1, "timemodified": NOW, "overdue": overdue, "icon": {}, "course": {"id": courseid, "fullname": CSC if courseid == 101 else "Web Programming", "shortname": "CSC-385" if courseid == 101 else "WEB", "viewurl": ""}, "canedit": False, "candelete": False, "deleteurl": "", "editurl": "", "viewurl": "", "formattedtime": "", "formattedlocation": "", "isactionevent": True, "iscourseevent": False, "iscategoryevent": False, "groupname": None, "normalisedeventtype": "course", "normalisedeventtypetext": "Course event", "action": {"name": "Add submission", "url": url or f"https://nexus.test/mod/{modulename}/view.php?id=10", "itemcount": 1, "actionable": True, "showitemcount": False}, "purpose": "assessment", "url": url or f"https://nexus.test/mod/{modulename}/view.php?id=10"}


@pytest.fixture
def world(fake):
    fake.on("core_enrol_get_users_courses", [course(101, CSC, "CSC-385"), course(102, "Web Programming", "WEB")])
    fake.on("core_course_get_courses_by_field", {"courses": [], "warnings": []})
    fake.on(
        "core_calendar_get_calendar_events",
        {
            "events": [
                cal_event(1, "Lab 3 is due", NOW + 8 * HOUR),
                cal_event(2, "Midterm exam", NOW + 3 * DAY, courseid=102, eventtype="course", modulename="", instance=0, duration=2 * HOUR, description="<p>Room 101</p>"),
                cal_event(3, "Quiz 2 closes", NOW + 2 * DAY, courseid=102, eventtype="close", modulename="quiz", instance=4),
                cal_event(4, "Dentist", NOW + DAY, courseid=0, eventtype="user", modulename="", instance=0),
                cal_event(5, "Way later", NOW + 40 * DAY, courseid=101, eventtype="course", modulename="", instance=0),
            ],
            "warnings": [],
        },
    )
    fake.on("core_calendar_get_action_events_by_timesort", {"events": [action_event(1, "Lab 3 is due", NOW + 8 * HOUR), action_event(3, "Quiz 2 closes", NOW + 2 * DAY, courseid=102, eventtype="close", modulename="quiz", instance=4, url="https://nexus.test/mod/quiz/view.php?id=40")], "firstid": 1, "lastid": 3})
    return fake


async def test_events_merged_and_sorted(world, nexus):
    events, warnings = await nexus.calendar.events(days=14)
    assert warnings == []
    assert [e.id for e in events] == [1, 4, 3, 2]
    lab = events[0]
    assert lab.source == "action" and lab.url == "https://nexus.test/mod/assign/view.php?id=10"
    assert lab.action.name == "Add submission" and lab.course == CSC and lab.module == "assign" and lab.instance == 1
    quiz = next(e for e in events if e.id == 3)
    assert quiz.url.endswith("quiz/view.php?id=40") and quiz.type == "close" and quiz.course == "Web Programming"
    exam = next(e for e in events if e.id == 2)
    assert exam.description == "Room 101" and exam.end is not None and exam.source == "calendar" and exam.type == "course"
    assert exam.url.startswith("https://nexus.test/calendar/view.php?view=day")
    dentist = next(e for e in events if e.id == 4)
    assert dentist.course_id is None and dentist.type == "user"
    form = world.calls_to("core_calendar_get_calendar_events")[0]
    assert form["events[courseids][0]"] == "101" and form["options[userevents]"] == "1" and form["options[timestart]"] == str(NOW)
    action_form = world.calls_to("core_calendar_get_action_events_by_timesort")[0]
    assert 1 <= int(action_form["limitnum"]) <= 50  # Nexus rejects anything above 50
    assert "aftereventid" not in action_form


async def test_action_events_paginate_past_50(world, nexus):
    pages = {
        0: [action_event(1000 + i, f"Item {i}", NOW + HOUR * (i + 1)) for i in range(50)],
        1049: [action_event(2000, "Item 50", NOW + 60 * HOUR)],
    }

    def respond(form):
        after = int(form.get("aftereventid", 0))
        rows = pages.get(after, [])
        return {"events": rows, "firstid": rows[0]["id"] if rows else 0, "lastid": rows[-1]["id"] if rows else 0}

    world.on("core_calendar_get_action_events_by_timesort", respond)
    events, warnings = await nexus.calendar.events(days=14)
    assert warnings == []
    assert len([e for e in events if e.source == "action"]) == 51
    assert [f.get("aftereventid") for f in world.calls_to("core_calendar_get_action_events_by_timesort")] == [None, "1049"]


async def test_events_course_filter(world, nexus):
    events, _ = await nexus.calendar.events(days=14, course_id=102)
    assert [e.id for e in events] == [4, 3, 2]


async def test_events_when_only_action_events_available(fake):
    fake.on("core_webservice_get_site_info", site_info(functions=["core_webservice_get_site_info", "core_user_get_users_by_field", "core_enrol_get_users_courses", "core_calendar_get_action_events_by_timesort"]))
    fake.on("core_enrol_get_users_courses", [course(101, CSC)])
    fake.on("core_calendar_get_action_events_by_timesort", {"events": [action_event(9, "Lab 9", NOW + HOUR)], "firstid": 9, "lastid": 9})
    nx = make_nexus()
    await nx.ensure_ready()
    events, _ = await nx.calendar.events(days=3)
    assert [e.id for e in events] == [9]
    await nx.aclose()


async def test_events_unsupported(fake):
    fake.on("core_webservice_get_site_info", site_info(functions=["core_webservice_get_site_info", "core_user_get_users_by_field", "core_enrol_get_users_courses"]))
    fake.on("core_enrol_get_users_courses", [course(101, CSC)])
    nx = make_nexus()
    await nx.ensure_ready()
    with pytest.raises(NexusUnsupportedError):
        await nx.calendar.events()
    await nx.aclose()
