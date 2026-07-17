from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from typing import Any


def adf_to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return adf_to_text(json.loads(stripped))
            except json.JSONDecodeError:
                return value
        return value
    if isinstance(value, list):
        return _clean("\n".join(adf_to_text(item) for item in value))
    if not isinstance(value, dict):
        return str(value)

    node_type = value.get("type")
    content = value.get("content") or []
    attrs = value.get("attrs") or {}

    if node_type == "text":
        text = value.get("text", "")
        for mark in value.get("marks") or []:
            if mark.get("type") == "link":
                href = (mark.get("attrs") or {}).get("href")
                if href:
                    text = f"{text} ({href})"
        return text
    if node_type in {"hardBreak", "rule"}:
        return "\n"
    if node_type == "emoji":
        return attrs.get("shortName") or attrs.get("text") or ""
    if node_type == "inlineCard":
        return attrs.get("url", "")
    if node_type == "mediaSingle":
        return ""
    if node_type == "codeBlock":
        return "\n".join(adf_to_text(item) for item in content)
    if node_type == "listItem":
        return "- " + _clean(" ".join(adf_to_text(item) for item in content))
    if node_type in {"bulletList", "orderedList"}:
        return "\n".join(adf_to_text(item) for item in content)
    if node_type in {"paragraph", "heading", "blockquote", "panel"}:
        return _clean("".join(adf_to_text(item) for item in content)) + "\n"
    if node_type in {"table", "tableRow"}:
        return "\n".join(adf_to_text(item) for item in content)
    if node_type in {"tableCell", "tableHeader"}:
        return _clean(" ".join(adf_to_text(item) for item in content))

    return _clean("\n".join(adf_to_text(item) for item in content))


def html_to_text(html: str) -> str:
    parser = _HTMLTextParser()
    parser.feed(html or "")
    return _clean(parser.text())


def extract_urls(text: str) -> list[str]:
    if not text:
        return []
    urls = re.findall(r"https?://[^\s)\]\"'>]+", text)
    cleaned = []
    for url in urls:
        url = url.rstrip(".,;:")
        if url not in cleaned:
            cleaned.append(url)
    return cleaned


def extract_confluence_page_ids(text: str) -> list[str]:
    ids = []
    for url in extract_urls(text):
        match = re.search(r"/wiki/(?:spaces/[^/]+/)?pages/(\d+)", url)
        if match and match.group(1) not in ids:
            ids.append(match.group(1))
    return ids


def infer_market(summary: str) -> str:
    match = re.match(r"\s*([A-Z]{2,6})\s*-", summary or "")
    return match.group(1) if match else ""


def infer_page_type(summary: str, description: str) -> str:
    haystack = f"{summary} {description}".lower()
    candidates = [
        ("checkout", ["checkout"]),
        ("funnel", ["funnel", "step 1", "step1", "step 2", "booking overlay"]),
        ("property", ["property", "hotel page", "hotel pages"]),
        ("lister", ["lister", "search results", "destination landing"]),
        ("campaign", ["campaign"]),
        ("homepage", ["homepage", "home page"]),
        ("website", ["website"]),
    ]
    for page_type, needles in candidates:
        if any(needle in haystack for needle in needles):
            return page_type
    parts = [part.strip().lower() for part in (summary or "").split("-")]
    return parts[1] if len(parts) > 2 else ""


def compact_text(*parts: str) -> str:
    return _clean("\n\n".join(part for part in parts if part))


def select_pdf_attachments(attachments: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not attachments:
        return []
    return [
        attachment
        for attachment in attachments
        if attachment.get("mimeType") == "application/pdf"
        or (attachment.get("filename") or "").lower().endswith(".pdf")
    ]


def pdf_to_text(data: bytes) -> tuple[str, bool]:
    """Extract text from a PDF, plus a best-effort Control/Variation breakdown.

    Returns (text, labeled) where `labeled` is True if at least one section's
    numbers could be confidently split into Control vs Variation rows. The
    plain text always comes first and is never dropped, even when the
    Control/Variation layout can't be detected (older/newer report
    templates, scanned pages, or a pdfplumber failure on a given page) — a
    row is only ever added to the labeled layer if it contains a digit.
    """
    from io import BytesIO

    try:
        import pdfplumber
    except ImportError:
        return _pdf_to_text_pypdf(data), False

    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            parts = []
            labeled = False
            for page in pdf.pages:
                try:
                    page_text = page.extract_text() or ""
                except Exception:
                    page_text = ""
                if page_text:
                    parts.append(page_text)
                try:
                    section_lines = _label_control_variation(page)
                except Exception:
                    section_lines = []
                if section_lines:
                    labeled = True
                    parts.append("\n".join(section_lines))
            if parts:
                return _clean("\n\n".join(parts)), labeled
    except Exception:
        pass
    return _pdf_to_text_pypdf(data), False


def _pdf_to_text_pypdf(data: bytes) -> str:
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return _clean("\n\n".join(pages))


def _label_control_variation(page) -> list[str]:
    def row_text(row) -> str:
        return " ".join((row[0] or "").split())

    def looks_like_metrics(text: str) -> bool:
        # A real metrics row has several numbers (e.g. "4,729 164 3.47% 64,223 € 392 €").
        # A category label that merely contains a digit (e.g. "checkout_step2",
        # "/step1") should not be mistaken for one, so require more than one digit.
        return sum(char.isdigit() for char in text) >= 3

    def bbox_contains(a, b) -> bool:
        ax0, atop, ax1, abot = a
        bx0, btop, bx1, bbot = b
        return ax0 <= bx0 and atop <= btop and ax1 >= bx1 and abot >= bbot and a != b

    tables = page.find_tables()
    # A report with color-filled but unruled table headers makes pdfplumber's
    # default detector also return one giant table spanning the whole page,
    # on top of the real ones. Drop any table whose bbox fully contains another.
    clean = [t for t in tables if not any(bbox_contains(t.bbox, other.bbox) for other in tables if other is not t)]
    clean.sort(key=lambda t: t.bbox[1])

    groups: list[list] = []
    for table in clean:
        if groups and abs(groups[-1][0].bbox[1] - table.bbox[1]) < 3:
            groups[-1].append(table)
        else:
            groups.append([table])

    out: list[str] = []
    prev_bottom = 0.0
    for group in groups:
        group.sort(key=lambda t: t.bbox[0])
        top = group[0].bbox[1]
        title = "(untitled)"
        if top > prev_bottom:
            strip = page.within_bbox((0, prev_bottom, page.width, top))
            title_text = (strip.extract_text() or "").strip()
            if title_text:
                title = title_text.splitlines()[-1]
        prev_bottom = max(t.bbox[3] for t in group)

        # Two tables sharing a row band, side by side, is the "left column
        # = Control, right column = Variation" report layout. One table
        # spanning most of the page width with exactly two data rows is the
        # "row 1 = Control, row 2 = Variation" layout.
        is_wide = len(group) == 1 and (group[0].bbox[2] - group[0].bbox[0]) > page.width * 0.7

        section_lines: list[str] = []
        if len(group) == 2:
            left_rows = [row_text(r) for r in group[0].extract()[1:] if row_text(r)]
            right_rows = [row_text(r) for r in group[1].extract()[1:] if row_text(r)]
            for left, right in zip(left_rows, right_rows):
                if looks_like_metrics(left) and looks_like_metrics(right):
                    section_lines.append(f"Control: {left}")
                    section_lines.append(f"Variation: {right}")
        elif is_wide:
            data_rows = [row_text(r) for r in group[0].extract()[1:] if row_text(r)]
            if len(data_rows) == 2 and looks_like_metrics(data_rows[0]) and looks_like_metrics(data_rows[1]):
                section_lines.append(f"Control: {data_rows[0]}")
                section_lines.append(f"Variation: {data_rows[1]}")

        if section_lines:
            out.append(f"{title} (Control vs Variation):")
            out.extend(section_lines)

    return out


def _clean(text: str) -> str:
    # PDF font ligatures pdfplumber/pypdf can't map (e.g. "ffi", "fi") sometimes decode
    # to a NUL byte instead of being dropped. SQLite's length() truncates at an embedded
    # NUL even though the full string is stored correctly, which makes diagnostics
    # misleading, and a stray NUL mid-word can fragment it for keyword search.
    text = (text or "").replace("\x00", "")
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()


class _HTMLTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag in {"p", "br", "li", "tr", "h1", "h2", "h3"}:
            self._parts.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._parts.append(f" {href} ")

    def handle_data(self, data: str):
        self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)
