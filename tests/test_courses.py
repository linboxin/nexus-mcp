import pytest

from nexus_mcp.errors import NexusNotFoundError
from tests.conftest import DAY, NOW, SITE, course, file_content, module, section


async def test_empty_courses(fake, nexus):
    fake.on("core_enrol_get_users_courses", [])
    fake.on("core_course_get_courses_by_field", {"courses": [], "warnings": []})
    assert await nexus.courses.list_courses("all") == []
    assert await nexus.courses.current_courses() == []
    assert await nexus.courses.current_course_ids() == []


async def test_multiple_courses_classification_and_teacher(fake, nexus):
    fake.on(
        "core_enrol_get_users_courses",
        [
            course(1, "CSC-385 - Computer Graphics", "CSC-385"),
            course(2, "Web Programming", "WEB", start=NOW - 200 * DAY, end=NOW - 100 * DAY),
            course(3, "Future Seminar", "FUT", start=NOW + 30 * DAY, end=NOW + 120 * DAY),
            course(4, "Hidden One", "HID", hidden=True),
        ],
    )
    fake.on(
        "core_course_get_courses_by_field",
        {"courses": [{"id": 1, "contacts": [{"id": 9, "fullname": "Prof. Ada Lovelace"}], "categoryname": "Computer Science", "summary": "<p>Graphics!</p>"}], "warnings": []},
    )
    current = await nexus.courses.list_courses()
    assert [c.id for c in current] == [1]
    first = current[0]
    assert first.name == "CSC-385 - Computer Graphics"
    assert first.short_name == "CSC-385"
    assert first.teacher == "Prof. Ada Lovelace"
    assert first.category == "Computer Science"
    assert first.summary == "Graphics!"
    assert first.url == f"{SITE}/course/view.php?id=1"
    assert first.starts is not None and first.starts.iso.endswith("-04:00")

    everything = await nexus.courses.list_courses("all")
    assert {c.id: c.classification for c in everything} == {1: "inprogress", 2: "past", 3: "future", 4: "hidden"}
    assert [c.id for c in await nexus.courses.list_courses("past")] == [2]
    assert fake.calls_to("core_course_get_courses_by_field")[0]["value"] == "1,2,3,4"
    assert fake.calls_to("core_enrol_get_users_courses")[0]["userid"] == "7"


async def test_list_courses_uses_cache(fake, nexus):
    fake.on("core_enrol_get_users_courses", [course(1, "A"), course(2, "B")])
    fake.on("core_course_get_courses_by_field", {"courses": [], "warnings": []})
    await nexus.courses.list_courses("all")
    await nexus.courses.list_courses("all")
    await nexus.courses.current_course_ids()
    assert len(fake.calls_to("core_enrol_get_users_courses")) == 1
    assert len(fake.calls_to("core_course_get_courses_by_field")) == 1


async def test_courses_without_dates_count_as_in_progress(fake, nexus):
    fake.on("core_enrol_get_users_courses", [course(1, "Undated", start=0, end=0)])
    fake.on("core_course_get_courses_by_field", {"courses": [], "warnings": []})
    assert [c.classification for c in await nexus.courses.list_courses()] == ["inprogress"]


async def test_invalid_classification(nexus):
    with pytest.raises(ValueError):
        await nexus.courses.list_courses("bogus")


async def test_get_course_detail(fake, nexus):
    fake.on("core_enrol_get_users_courses", [course(1, "CSC-385 - Computer Graphics", "CSC-385", summary="<p>Course summary</p>")])
    fake.on("core_course_get_courses_by_field", {"courses": [{"id": 1, "contacts": [{"id": 9, "fullname": "Prof. Ada Lovelace"}]}], "warnings": []})
    fake.on(
        "core_course_get_contents",
        [
            section(0, "General", [module(11, "forum", "Announcements"), module(12, "label", "Welcome", description="<b>Hi</b> there")]),
            section(
                1,
                "Week 1",
                [
                    module(13, "assign", "Lab 1"),
                    module(14, "resource", "Syllabus", contents=[file_content("syllabus.pdf", "1/mod_resource/content/1")]),
                    module(15, "page", "Notes"),
                    module(16, "url", "MDN", contents=[{"type": "url", "filename": "MDN", "fileurl": "https://developer.mozilla.org"}]),
                ],
            ),
        ],
    )
    detail = await nexus.courses.get_course(1)
    assert detail.name == "CSC-385 - Computer Graphics"
    assert detail.description == "Course summary"
    assert detail.instructors == ["Prof. Ada Lovelace"]
    assert [s.name for s in detail.sections] == ["General", "Week 1"]
    assert {m.type for m in detail.materials} == {"label", "resource", "page", "url"}
    assert [m.name for m in detail.activities] == ["Announcements", "Lab 1"]
    syllabus = next(m for m in detail.materials if m.name == "Syllabus")
    assert syllabus.files[0].filename == "syllabus.pdf"
    assert syllabus.files[0].url.endswith("/syllabus.pdf")
    assert detail.sections[0].modules[1].description == "Hi there"
    assert detail.sections[1].modules[0].section == "Week 1"


async def test_get_course_not_enrolled(fake, nexus):
    fake.on("core_enrol_get_users_courses", [course(1, "A")])
    fake.on("core_course_get_courses_by_field", {"courses": [], "warnings": []})
    with pytest.raises(NexusNotFoundError) as info:
        await nexus.courses.get_course(99)
    assert info.value.code == "NEXUS_RESOURCE_NOT_FOUND"


def test_parse_term_union_patterns():
    from nexus_mcp.moodle.courses import parse_term

    assert parse_term("26/FA.CSC-240-01") == "Fall 2026"
    assert parse_term("25-WI.CSC-108-01") == "Winter 2025"
    assert parse_term("25-SP.CHN-202-01") == "Spring 2025"
    assert parse_term("24-FA.ESC-100-01_08") == "Fall 2024"
    assert parse_term("AcademicIntegrity") is None
    assert parse_term("") is None and parse_term(None) is None


async def test_academic_only_filter_and_ordering(fake, nexus):
    fake.on(
        "core_enrol_get_users_courses",
        [course(2, "Academic Integrity Training", "AcademicIntegrity"), course(3, "Web Programming", "26/FA.CSC-240-01"), course(4, "Computer Graphics", "26/FA.CSC-385-01")],
    )
    fake.on("core_course_get_courses_by_field", {"courses": [], "warnings": []})
    all_current = await nexus.courses.list_courses()
    assert [c.id for c in all_current] == [4, 3, 2]  # academic first, then by name
    assert all_current[0].term == "Fall 2026" and all_current[0].academic
    assert all_current[2].term is None and not all_current[2].academic
    academic = await nexus.courses.list_courses(academic_only=True)
    assert [c.id for c in academic] == [4, 3]
