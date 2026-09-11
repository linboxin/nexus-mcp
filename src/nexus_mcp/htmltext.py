"""Convert Moodle HTML (descriptions, forum posts, page content) to readable text."""

from __future__ import annotations

import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table",
    "section", "article", "blockquote", "pre", "hr", "dd", "dt",
}
_SKIP_TAGS = {"script", "style", "noscript", "svg"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")
        if tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self.parts.append(f"[image: {alt}]")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")
        if tag in ("td", "th"):
            self.parts.append("\t")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str | None, *, max_len: int | None = None) -> str:
    if not html:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # pragma: no cover - HTMLParser is lenient; belt and braces
        text = re.sub(r"<[^>]+>", " ", html)
    else:
        text = "".join(parser.parts)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if max_len is not None and len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text
