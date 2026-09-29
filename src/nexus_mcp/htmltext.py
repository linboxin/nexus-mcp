"""Convert Moodle HTML (descriptions, forum posts, page content) to readable text."""

from __future__ import annotations

import html
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


def clean_name(name: str | None) -> str:
    """Moodle returns some names HTML-escaped ("Software &amp; Hardware"); decode them."""
    return html.unescape(name or "").strip()


def clip(text: str | None, limit: int, *, hint: str | None = None) -> str | None:
    """Shorten ``text`` to ``limit`` characters, saying so (and where the rest is) instead of a bare "…"."""
    if not text:
        return text or None
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    more = f"[… {len(text) - len(cut):,} more characters"
    return f"{cut} {more}{'; ' + hint if hint else ''}]"


def html_to_text(html_text: str | None, *, max_len: int | None = None, hint: str | None = None) -> str:
    """Readable text from Moodle HTML. ``max_len`` clips with an explicit marker (see ``clip``)."""
    if not html_text:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:  # pragma: no cover - HTMLParser is lenient; belt and braces
        text = re.sub(r"<[^>]+>", " ", html_text)
    else:
        text = "".join(parser.parts)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if max_len is not None:
        text = clip(text, max_len, hint=hint) or ""
    return text
