from __future__ import annotations

from typing import Any

from .. import runtime
from ._common import READ_ONLY, dump, nexus_tool


def register(server: Any) -> None:
    @server.tool(name="search_course_materials", annotations=READ_ONLY)
    @nexus_tool
    async def search_course_materials(query: str, course_id: int | None = None, limit: int = 25) -> dict[str, Any]:
        """Search course materials (PDFs, files, pages, books, links, folders, lecture notes)
        by title, filename, description or section name across current courses (or one
        course). Returns title, course, type, description, url and a material id for
        get_material. Nothing is downloaded by this call."""
        nx = await runtime.get_nexus()
        items, warnings = await nx.materials.search(query, course_id=course_id, limit=limit)
        return {"materials": dump(items), "count": len(items), "query": query, "warnings": warnings}

    @server.tool(name="get_material", annotations=READ_ONLY)
    @nexus_tool
    async def get_material(material_id: str) -> dict[str, Any]:
        """Metadata for one material plus its content when it is text-bearing (Moodle
        pages, books, text/HTML files, external links, and PDFs when the optional pdf
        extra is installed). material_id is the id from search_course_materials or a
        course-module id (cmid); folder files use "<cmid>/<n>"."""
        nx = await runtime.get_nexus()
        content = await nx.materials.get_material(str(material_id))
        return content.model_dump(mode="json")
