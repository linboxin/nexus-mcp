"""Course materials: files, pages, books, URLs, folders (from core_course_get_contents)."""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any

from ..errors import NexusError, NexusNotFoundError
from ..htmltext import html_to_text
from ..models.material import Material, MaterialContent
from .courses import ACTIVITY_MODULES, MATERIAL_MODULES

if TYPE_CHECKING:
    from .nexus import Nexus

MAX_TEXT_CHARS = 40_000
TEXT_MIMES = ("text/", "application/json", "application/xml", "application/xhtml+xml", "application/x-tex")
TEXT_EXTS = {"txt", "md", "markdown", "py", "java", "js", "ts", "c", "cpp", "h", "hpp", "ipynb", "tex", "json", "xml", "html", "htm", "css", "csv", "tsv", "rst", "yaml", "yml", "sql", "r", "m", "sh"}


def classify_file(mimetype: str | None, filename: str | None) -> str:
    mt = (mimetype or "").lower()
    name = (filename or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    if mt == "application/pdf" or ext == "pdf":
        return "pdf"
    if ext in ("ppt", "pptx", "key", "odp") or "presentation" in mt:
        return "slides"
    if ext in ("doc", "docx", "odt", "rtf", "pages") or "msword" in mt or "wordprocessing" in mt:
        return "document"
    if ext in ("xls", "xlsx", "ods", "numbers") or "spreadsheet" in mt or "ms-excel" in mt:
        return "spreadsheet"
    if mt.startswith("video/") or ext in ("mp4", "mov", "m4v", "webm", "mkv"):
        return "video"
    if mt.startswith("audio/") or ext in ("mp3", "m4a", "wav", "aac"):
        return "audio"
    if mt.startswith("image/") or ext in ("png", "jpg", "jpeg", "gif", "svg", "webp"):
        return "image"
    if ext in ("zip", "tar", "gz", "tgz", "7z", "rar") or "zip" in mt or "compressed" in mt:
        return "archive"
    if mt.startswith("text/") or ext in TEXT_EXTS:
        return "text"
    return "file"


def _is_textual(mimetype: str | None, filename: str | None) -> bool:
    mt = (mimetype or "").lower()
    ext = (filename or "").lower().rsplit(".", 1)[-1] if filename and "." in filename else ""
    return any(mt.startswith(p) for p in TEXT_MIMES) or ext in TEXT_EXTS


def _tokens(query: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) >= 2]


def score_material(m: Material, query: str, tokens: list[str]) -> float:
    q = query.lower().strip()
    title = m.title.lower()
    fname = (m.filename or "").lower()
    desc = (m.description or "").lower()
    section = (m.section or "").lower()
    score = 0.0
    if q and q in title:
        score += 5
    if q and q in fname:
        score += 4
    for t in tokens:
        if t in title:
            score += 3
        if t in fname:
            score += 2
        if t in desc:
            score += 1
        if t in section:
            score += 1
    return score


class MaterialService:
    def __init__(self, nx: "Nexus") -> None:
        self.nx = nx

    # -- inventory ---------------------------------------------------------
    async def course_materials(self, course_id: int, course_name: str | None = None) -> list[Material]:
        nx = self.nx
        course_id = int(course_id)
        if course_name is None:
            course_name = await nx.courses.course_name(course_id)
        sections = await nx.courses.contents(course_id)
        out: list[Material] = []
        seen: set[tuple[int, str | None]] = set()
        for s in sections:
            section_name = s.get("name") or None
            for m in s.get("modules", []) or []:
                modname = m.get("modname") or ""
                if modname in ACTIVITY_MODULES:
                    continue
                if modname not in MATERIAL_MODULES and not m.get("contents"):
                    continue
                cmid = int(m["id"])
                files = [c for c in m.get("contents", []) or [] if c.get("type") == "file" and not str(c.get("filename", "")).startswith("structure")]
                desc = html_to_text(m.get("description"), max_len=400) or None
                base = dict(
                    cmid=cmid,
                    course_id=course_id,
                    course=course_name,
                    module_type=modname,
                    section=section_name,
                    url=m.get("url"),
                )
                if modname == "url":
                    ext = next((c for c in m.get("contents", []) or [] if c.get("type") == "url"), {})
                    out.append(Material(id=str(cmid), title=m.get("name", ""), type="url", description=desc, file_url=ext.get("fileurl"), modified=nx.when(ext.get("timemodified")), **base))
                    continue
                if modname == "page":
                    out.append(Material(id=str(cmid), title=m.get("name", ""), type="page", description=desc, modified=nx.when(next((c.get("timemodified") for c in files if c.get("filename") == "index.html"), None)), **base))
                    continue
                if modname == "book":
                    out.append(Material(id=str(cmid), title=m.get("name", ""), type="book", description=desc, **base))
                    continue
                if modname == "label":
                    if desc:
                        out.append(Material(id=str(cmid), title=(desc.splitlines()[0][:80] if desc else m.get("name", "")), type="label", description=desc, **base))
                    continue
                if modname == "folder" or (modname != "resource" and len(files) > 1):
                    out.append(Material(id=str(cmid), title=m.get("name", ""), type="folder", description=desc, size=sum(int(f.get("filesize") or 0) for f in files) or None, **base))
                    for index, f in enumerate(files):
                        key = (cmid, f.get("fileurl"))
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append(
                            Material(
                                id=f"{cmid}/{index}",
                                title=f"{m.get('name', '')} / {f.get('filename', '')}",
                                type=classify_file(f.get("mimetype"), f.get("filename")),
                                filename=f.get("filename"),
                                description=desc,
                                file_url=f.get("fileurl"),
                                mimetype=f.get("mimetype"),
                                size=int(f["filesize"]) if f.get("filesize") else None,
                                modified=nx.when(f.get("timemodified")),
                                **base,
                            )
                        )
                    continue
                # resource (single file) and anything else that carries one file
                f = files[0] if files else None
                out.append(
                    Material(
                        id=str(cmid),
                        title=m.get("name", ""),
                        type=classify_file(f.get("mimetype") if f else None, f.get("filename") if f else None) if f else "file",
                        filename=f.get("filename") if f else None,
                        description=desc,
                        file_url=f.get("fileurl") if f else None,
                        mimetype=f.get("mimetype") if f else None,
                        size=int(f["filesize"]) if f and f.get("filesize") else None,
                        modified=nx.when(f.get("timemodified")) if f else None,
                        **base,
                    )
                )
        return out

    async def all_materials(self, course_id: int | None = None) -> tuple[list[Material], list[str]]:
        nx = self.nx
        warnings: list[str] = []
        if course_id is not None:
            name = await nx.courses.course_name(int(course_id))
            return await self.course_materials(int(course_id), name), warnings
        courses = await nx.courses.current_courses()

        async def one(course: Any) -> list[Material]:
            try:
                return await self.course_materials(course.id, course.name)
            except NexusError as exc:
                warnings.append(f"{course.name}: {exc.message}")
                return []

        results = await asyncio.gather(*(one(c) for c in courses))
        return [m for group in results for m in group], warnings

    async def search(self, query: str, course_id: int | None = None, *, limit: int = 25) -> tuple[list[Material], list[str]]:
        query = (query or "").strip()
        if not query:
            raise ValueError("query must not be empty")
        materials, warnings = await self.all_materials(course_id)
        tokens = _tokens(query)
        scored: list[Material] = []
        seen_urls: set[str] = set()
        for m in materials:
            score = score_material(m, query, tokens)
            if score <= 0:
                continue
            dedupe_key = m.file_url or f"cm:{m.id}"
            if dedupe_key in seen_urls:
                continue
            seen_urls.add(dedupe_key)
            m.score = score
            scored.append(m)
        scored.sort(key=lambda m: (-(m.score or 0), m.title.lower()))
        return scored[: max(1, int(limit))], warnings

    # -- single material ---------------------------------------------------
    async def find(self, material_id: str) -> Material:
        nx = self.nx
        material_id = str(material_id).strip()
        cmid_text, _, index_text = material_id.partition("/")
        if not cmid_text.isdigit():
            raise ValueError("material_id must be a course-module id like '1234' or '1234/0'")
        cmid = int(cmid_text)
        nx.require("core_course_get_course_module", feature="looking up a material by id")
        try:
            payload = await nx.client.call("core_course_get_course_module", cmid=cmid)
        except NexusNotFoundError:
            raise NexusNotFoundError(f"No material with id {material_id} is visible to your account.", details={"material_id": material_id})
        cm = (payload or {}).get("cm") or {}
        course_id = int(cm.get("course") or 0)
        if not course_id:
            raise NexusNotFoundError(f"No material with id {material_id} is visible to your account.")
        materials = await self.course_materials(course_id)
        wanted = f"{cmid}/{index_text}" if index_text else str(cmid)
        for m in materials:
            if m.id == wanted:
                return m
        for m in materials:
            if m.cmid == cmid:
                return m
        raise NexusNotFoundError(
            f"Module {cmid} exists but is not a material (it is a '{cm.get('modname')}' activity).",
            details={"material_id": material_id, "modname": cm.get("modname")},
        )

    async def get_material(self, material_id: str) -> MaterialContent:
        nx = self.nx
        m = await self.find(material_id)
        content: str | None = None
        content_type: str | None = None
        truncated = False
        note: str | None = None
        try:
            if m.module_type == "page":
                content, content_type = await self._page_content(m)
            elif m.module_type == "book":
                content, content_type, truncated = await self._book_content(m)
            elif m.module_type == "url":
                content, content_type = m.file_url, "url"
                note = "External link; open it in a browser."
            elif m.module_type == "label":
                content, content_type = m.description, "text"
            elif m.file_url and _is_textual(m.mimetype, m.filename):
                body, ctype, cut = await nx.client.download(m.file_url, max_bytes=2_000_000)
                text = body.decode("utf-8", errors="replace")
                if "html" in (m.mimetype or "") or (m.filename or "").lower().endswith((".html", ".htm")):
                    text = html_to_text(text)
                content, content_type, truncated = text, "text", cut
            elif m.file_url and m.type == "pdf":
                content, content_type, truncated, note = await self._pdf_content(m)
            elif m.file_url:
                note = f"{m.type} content is not extracted ({m.mimetype or 'binary'}); open the url in Nexus to view it."
            elif m.module_type == "folder":
                note = "This is a folder; its files are listed as separate materials with ids like '<cmid>/<n>'."
        except NexusError as exc:
            note = f"Content could not be retrieved: {exc.message}"
        if content and len(content) > MAX_TEXT_CHARS:
            content = content[:MAX_TEXT_CHARS] + "\n…"
            truncated = True
        return MaterialContent(**m.model_dump(), content=content, content_type=content_type, truncated=truncated, note=note)

    async def _page_content(self, m: Material) -> tuple[str | None, str]:
        nx = self.nx
        if nx.has("mod_page_get_pages_by_courses"):
            async def fetch() -> list[dict[str, Any]]:
                payload = await nx.client.call("mod_page_get_pages_by_courses", courseids=[m.course_id])
                return list((payload or {}).get("pages", []))

            pages, _ = await nx.cache.get_or_fetch(("pages", m.course_id), nx.ttl.materials, fetch)
            for p in pages:
                if int(p.get("coursemodule") or 0) == m.cmid:
                    return html_to_text(p.get("content")), "text"
        sections = await nx.courses.contents(m.course_id)
        for s in sections:
            for mod in s.get("modules", []) or []:
                if int(mod["id"]) == m.cmid:
                    for c in mod.get("contents", []) or []:
                        if c.get("filename") == "index.html" and c.get("fileurl"):
                            body, _, _ = await nx.client.download(c["fileurl"], max_bytes=2_000_000)
                            return html_to_text(body.decode("utf-8", errors="replace")), "text"
        return None, "text"

    async def _book_content(self, m: Material) -> tuple[str | None, str, bool]:
        nx = self.nx
        sections = await nx.courses.contents(m.course_id)
        chapters: list[tuple[str, str]] = []
        for s in sections:
            for mod in s.get("modules", []) or []:
                if int(mod["id"]) != m.cmid:
                    continue
                for c in mod.get("contents", []) or []:
                    if c.get("type") == "file" and c.get("filename") == "index.html" and c.get("fileurl"):
                        chapters.append((str(c.get("content") or c.get("filepath") or ""), c["fileurl"]))
        parts: list[str] = []
        total = 0
        truncated = False
        for title, url in chapters:
            body, _, _ = await nx.client.download(url, max_bytes=1_000_000)
            text = html_to_text(body.decode("utf-8", errors="replace"))
            heading = title.strip("/").strip()
            chunk = (f"## {heading}\n" if heading else "") + text
            total += len(chunk)
            parts.append(chunk)
            if total > MAX_TEXT_CHARS:
                truncated = True
                break
        return ("\n\n".join(parts) if parts else None), "text", truncated

    async def _pdf_content(self, m: Material) -> tuple[str | None, str | None, bool, str | None]:
        try:
            from pypdf import PdfReader  # optional dependency
        except ImportError:
            return None, None, False, (
                "PDF text extraction needs the optional 'pdf' extra (uv sync --extra pdf). "
                "Metadata only; open the url in Nexus to read it."
            )
        import io

        assert m.file_url
        body, _, cut = await self.nx.client.download(m.file_url, max_bytes=25_000_000)
        if cut:
            return None, None, True, "PDF is larger than 25 MB; not extracted."
        try:
            reader = PdfReader(io.BytesIO(body))
            pages = []
            total = 0
            truncated = False
            for page in reader.pages:
                text = page.extract_text() or ""
                pages.append(text)
                total += len(text)
                if total > MAX_TEXT_CHARS:
                    truncated = True
                    break
            return "\n\n".join(pages).strip() or None, "text", truncated, (
                f"Extracted text from {len(pages)} of {len(reader.pages)} pages." if truncated else None
            )
        except Exception as exc:  # pragma: no cover - depends on pypdf internals
            return None, None, False, f"PDF could not be parsed: {exc}"
