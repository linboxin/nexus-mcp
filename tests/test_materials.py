import pytest

from nexus_mcp.errors import NexusNotFoundError
from tests.conftest import NOW, SITE, course, file_content, module, section

CSC = "CSC-385 - Computer Graphics"
WEB = "Web Programming"


@pytest.fixture
def world(fake):
    fake.on("core_enrol_get_users_courses", [course(1, CSC, "CSC-385"), course(2, WEB, "WEB")])
    fake.on("core_course_get_courses_by_field", {"courses": [], "warnings": []})
    contents = {
        1: [
            section(0, "General", [module(11, "forum", "Announcements"), module(12, "resource", "Syllabus", contents=[file_content("syllabus.pdf", "1/mod_resource/content/1")])]),
            section(
                1,
                "Week 3: Shaders",
                [
                    module(13, "resource", "Lecture 5 - Fragment shaders", contents=[file_content("lecture05-shaders.pdf", "2/mod_resource/content/1")], description="<p>Slides on GLSL fragment shaders</p>"),
                    module(14, "page", "Lab 3 notes", contents=[file_content("index.html", "3/mod_page/content/1", mimetype="text/html")]),
                    module(15, "folder", "Starter code", contents=[file_content("main.cpp", "4/mod_folder/content/0", mimetype="text/x-c++src", size=300), file_content("shader.glsl", "4/mod_folder/content/0", mimetype="text/plain", size=120)]),
                    module(16, "url", "WebGL fundamentals", contents=[{"type": "url", "filename": "WebGL fundamentals", "fileurl": "https://webglfundamentals.org", "timemodified": NOW}]),
                    module(17, "assign", "Lab 3"),
                ],
            ),
            section(2, "Archive", [module(18, "resource", "Syllabus (copy)", contents=[file_content("syllabus.pdf", "1/mod_resource/content/1")])]),
        ],
        2: [
            section(0, "Week 2", [module(21, "resource", "JavaScript promises - lecture notes", contents=[file_content("promises.md", "5/mod_resource/content/1", mimetype="text/markdown", size=40)]), module(22, "book", "Async JS handbook", contents=[{"type": "content", "filename": "structure", "filepath": "/", "fileurl": None, "content": "[]"}, file_content("index.html", "6/mod_book/chapter/1", mimetype="text/html"), file_content("index.html", "6/mod_book/chapter/2", mimetype="text/html")])]),
        ],
    }
    fake.on("core_course_get_contents", lambda form: contents[int(form["courseid"])])
    fake.on("core_course_get_course_module", lambda form: {"cm": {"id": int(form["cmid"]), "course": 1 if int(form["cmid"]) < 20 else 2, "modname": "resource", "name": "x", "instance": 1}} if int(form["cmid"]) in (12, 13, 14, 15, 16, 17, 21, 22) else {"exception": "dml_missing_record_exception", "errorcode": "invalidrecord", "message": "nope"})
    fake.on("mod_page_get_pages_by_courses", {"pages": [{"id": 3, "coursemodule": 14, "course": 1, "name": "Lab 3 notes", "content": "<h1>Lab 3</h1><p>Write a <b>fragment</b> shader.</p>"}], "warnings": []})
    fake.files[f"{SITE}/webservice/pluginfile.php/5/mod_resource/content/1/promises.md"] = (b"# Promises\n\nA promise resolves later.", "text/markdown")
    fake.files[f"{SITE}/webservice/pluginfile.php/6/mod_book/chapter/1/index.html"] = (b"<p>Chapter one</p>", "text/html")
    fake.files[f"{SITE}/webservice/pluginfile.php/6/mod_book/chapter/2/index.html"] = (b"<p>Chapter two</p>", "text/html")
    return fake


async def test_course_materials_inventory(world, nexus):
    mats = await nexus.materials.course_materials(1)
    by_id = {m.id: m for m in mats}
    assert by_id["13"].type == "pdf" and by_id["13"].filename == "lecture05-shaders.pdf" and by_id["13"].section == "Week 3: Shaders"
    assert by_id["14"].type == "page"
    assert by_id["15"].type == "folder" and by_id["15/0"].filename == "main.cpp" and by_id["15/1"].type == "text"
    assert by_id["16"].type == "url" and by_id["16"].file_url == "https://webglfundamentals.org"
    assert "17" not in by_id and "11" not in by_id  # activities are not materials
    assert by_id["13"].url == f"{SITE}/mod/resource/view.php?id=13"


async def test_search_ranks_and_dedupes(world, nexus):
    results, warnings = await nexus.materials.search("shaders")
    assert warnings == []
    assert results[0].id == "13"  # title match first
    titles = [r.title for r in results]
    assert "Starter code / shader.glsl" in titles
    results, _ = await nexus.materials.search("syllabus")
    assert [r.id for r in results] == ["12"]  # duplicate resource (same file) reported once
    results, _ = await nexus.materials.search("javascript promises")
    assert results[0].course == WEB and results[0].id == "21"
    results, _ = await nexus.materials.search("promises", course_id=1)
    assert results == []
    with pytest.raises(ValueError):
        await nexus.materials.search("   ")


async def test_get_material_page_book_text_and_pdf(world, nexus):
    page = await nexus.materials.get_material("14")
    assert page.content_type == "text" and "Write a fragment shader." in page.content
    text = await nexus.materials.get_material("21")
    assert text.content.startswith("# Promises") and text.content_type == "text"
    book = await nexus.materials.get_material("22")
    assert "Chapter one" in book.content and "Chapter two" in book.content
    url = await nexus.materials.get_material("16")
    assert url.content == "https://webglfundamentals.org" and url.content_type == "url"
    pdf = await nexus.materials.get_material("13")
    assert pdf.type == "pdf" and pdf.note  # extracted only with the optional pdf extra
    folder_file = await nexus.materials.get_material("15/1")
    assert folder_file.filename == "shader.glsl"


async def test_get_material_not_found_or_not_material(world, nexus):
    with pytest.raises(NexusNotFoundError):
        await nexus.materials.get_material("999")
    with pytest.raises(NexusNotFoundError, match="activity"):
        await nexus.materials.get_material("17")
    with pytest.raises(ValueError):
        await nexus.materials.get_material("abc")
