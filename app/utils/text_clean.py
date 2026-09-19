"""Cleans up meeting descriptions that arrive as literal HTML markup.

Google Calendar's API returns an event's description exactly as the
organizer's calendar client wrote it — and some clients (Outlook chief
among them) write invite bodies as an RTF-to-HTML export, e.g. a string
starting with "<!-- Converted from text/rtf format -->" and wrapping every
line in <P>/<SPAN>/<FONT> tags, rather than as plain text. Google just
stores and returns that verbatim, so it lands in Meeting.description as-is
and renders as raw tag soup instead of readable text.
"""
from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser

# A quick, cheap check before bothering to parse: does this look like it
# contains real markup, rather than a stray "<" or ">" in ordinary prose
# (e.g. "Budget < 100k")?
_LOOKS_LIKE_HTML = re.compile(
    r"<(!--|/?\s*(p|span|font|br|div|table|tr|td|th|html|body|head|a|b|i|u|ul|ol|li|style)\b)",
    re.IGNORECASE,
)

# Tags whose opening implies a line break in the plain-text rendering.
_BLOCK_TAGS = {"p", "div", "tr", "table", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag.lower() in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_comment(self, data: str) -> None:
        pass  # drop "<!-- Converted from text/rtf format -->" and similar

    def text(self) -> str:
        return "".join(self._parts)


def clean_description(raw: str | None) -> str | None:
    """Strip HTML markup down to readable plain text. A description that
    doesn't look like markup is returned untouched, so genuinely plain-text
    descriptions (manual entry, or well-behaved calendar clients) are
    never altered.
    """
    if not raw or not _LOOKS_LIKE_HTML.search(raw):
        return raw

    parser = _TextExtractor()
    parser.feed(raw)
    parser.close()
    text = unescape(parser.text())

    # Collapse the blank-line noise tag soup leaves behind, but keep
    # intentional paragraph breaks — rendered with white-space: pre-line
    # wherever a description is shown.
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    cleaned = "\n\n".join(lines).strip()
    return cleaned or None
